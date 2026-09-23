# -*- coding: utf-8 -*-
"""B-T4 副本目录 CDP 接管通道 + B1 审查遗留四小件（win-cookie-import v1 收尾）。

锁定（对应简报测试节 ①-⑦ + Windows 真机反馈双缺陷修复）：
1. takeover 成功链全 mock：exe 查找 / 最小复制集（Local State + Cookies*）/ Popen /
   CDP getAllCookies / 域过滤 / 进程终止 / 临时目录清理；CDP 抛错路径 finally 清理也断言。
2. headless 失败 → 去 headless 同参重试一次。
3. exe 缺失 → takeover_no_browser 不 raise。
4. 接线：win32 chromium 走 takeover；darwin 走 Keychain 零变化；linux 零变化；
   SKILL_DISABLE_TAKEOVER=1 回 unsupported_source。
5. 动态端口不占 9222（Popen 参数断言）。
6. profile 枚举排除 System/Guest + --browser-profile 透传（display 名/目录名）。
7. B1 四小件：全源 unsupported 分支真覆盖 / Firefox -wal/-shm sidecar /
   parse_cookie_header 行内前缀（#2 ini fixture 修正在 test_cookie_import_win.py）。
8. ISSUE-3（report 75b24068）：接管启动参数必含 --remote-allow-origins=*
   （Chrome 111+ WS 握手 Origin 校验，漏参 = 探活通过但握手 403 假就绪）。
9. ISSUE-2（report 0b999d17）：源 Cookies 独占锁（WinError 32）——未显式
   --browser-profile 自动改用可读 profile（message 注明）；显式指定/无替代给
   「退出浏览器」明确指引；复制失败按锁/非锁分流；WS 握手失败人话文案。

全部 mock/临时目录：不真启浏览器、不解密、不写用户目录；cookie 明文不落断言输出。

运行: cd skill && .venv314/bin/python -m pytest tests/test_takeover_channel.py -q
"""
from __future__ import annotations

import json
import pytest
import shutil
import sqlite3
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.cookie_harvest as ch  # noqa: E402


# ═══════════ 工具 ═══════════


def _set_platform(monkeypatch, name: str) -> None:
    """替换 cookie_harvest 可见的 sys（模块级引用，仿既有测试先例）。"""
    monkeypatch.setattr(ch, "sys", types.SimpleNamespace(platform=name))


def _make_firefox_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT, "
        "host TEXT, name TEXT, value TEXT, path TEXT, expiry INTEGER, "
        "lastAccessed INTEGER, creationTime INTEGER, isSecure INTEGER, "
        "isHttpOnly INTEGER, inBrowserElement INTEGER, sameSite INTEGER)")
    future = int(time.time() + 86400)
    conn.execute("INSERT INTO moz_cookies (host, name, value, path, expiry, "
                 "isSecure, isHttpOnly) VALUES (?,?,?,?,?,?,?)",
                 (".1688.com", "cookie2", "goodvalue", "/", future, 1, 1))
    conn.commit()
    conn.close()


class _FakeProc:
    """Popen 替身（绝不真启浏览器）。"""

    def __init__(self, pid: int):
        self.pid = pid
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminate_calls += 1

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.kill_calls += 1


def _fake_user_data(tmp_path: Path, *, with_cookies: bool = True,
                    legacy_layout: bool = False) -> Path:
    """假 Windows User Data 树：Local State + Default/Cookies（含 -wal）。"""
    ud = tmp_path / "userdata"
    if legacy_layout:
        prof = ud / "Default"
    else:
        prof = ud / "Default" / "Network"
    prof.mkdir(parents=True)
    (ud / "Local State").write_bytes(b"{}")
    if with_cookies:
        (prof / "Cookies").write_bytes(b"fake-cookies-db")
        Path(str(prof / "Cookies") + "-wal").write_bytes(b"fake-wal")
    return ud


def _patch_takeover_env(monkeypatch, tmp_path, *, exe: str | None = "C:/fake/chrome.exe",
                        ud: Path | None = None, port: int = 12345,
                        wait_results: list | None = None,
                        cdp_cookies: list | None = None,
                        cdp_error: Exception | None = None) -> dict:
    """接管通道五边界全 mock。返回记录器 dict。"""
    rec: dict = {"cmds": [], "procs": [], "killed": []}
    monkeypatch.setattr(ch, "_takeover_find_exe", lambda b: exe)
    monkeypatch.setattr(ch, "_takeover_user_data_root", lambda b: ud)
    monkeypatch.setattr(ch, "_takeover_dynamic_port", lambda: port)

    def fake_popen(cmd):
        rec["cmds"].append(list(cmd))
        p = _FakeProc(pid=5000 + len(rec["cmds"]))
        rec["procs"].append(p)
        return p
    monkeypatch.setattr(ch, "_popen_takeover_browser", fake_popen)
    monkeypatch.setattr(ch, "_takeover_kill_proc", lambda p: rec["killed"].append(p))

    waits = list(wait_results) if wait_results is not None else [True]
    monkeypatch.setattr(ch, "_takeover_wait_cdp",
                        lambda port_, timeout_s: waits.pop(0) if waits else False)
    if cdp_error is not None:
        def raise_cdp(port_):
            raise cdp_error
        monkeypatch.setattr(ch, "_cdp_read_all_cookies", raise_cdp)
    else:
        monkeypatch.setattr(ch, "_cdp_read_all_cookies",
                            lambda port_: list(cdp_cookies or []))

    # 最小复制集 spy（workdir 在 finally 被清理，落盘断言只能盯复制调用本身）
    rec["copied"] = []
    real_copy_shared = ch._copy_shared

    def spy_copy_shared(src, dst):
        rec["copied"].append((str(src), str(dst)))
        return real_copy_shared(src, dst)
    monkeypatch.setattr(ch, "_copy_shared", spy_copy_shared)

    made: list[Path] = []

    def fake_new_workdir():
        wd = tmp_path / "wk" / f"takeover_{len(made)}"
        wd.mkdir(parents=True)
        made.append(wd)
        return wd
    monkeypatch.setattr(ch, "_takeover_new_workdir", fake_new_workdir)
    rec["workdirs"] = made
    return rec


def _cdp_cookie(name: str, domain: str, expires: float | None = None) -> dict:
    return {"name": name, "value": "v-" + name, "domain": domain, "path": "/",
            "expires": expires if expires is not None else time.time() + 3600,
            "secure": True, "httpOnly": True}


# ═══════════ ① takeover 成功链 / 清理 finally ═══════════


class TestTakeoverSuccessChain:
    def test_success_chain_end_to_end_mock(self, monkeypatch, tmp_path):
        """exe/复制集/Popen/CDP/域过滤/进程终止/临时目录清理全链。"""
        _set_platform(monkeypatch, "win32")
        ud = _fake_user_data(tmp_path)
        cdp = [_cdp_cookie("cookie2", ".1688.com"),
               _cdp_cookie("sc_company_id", ".ozon.ru"),
               _cdp_cookie("junk", "example.com"),           # 外域 → 滤
               _cdp_cookie("stale", ".1688.com",
                           expires=time.time() - 10),        # 过期 → 滤
               _cdp_cookie("sess", ".1688.com", expires=-1)]  # session → 滤
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=ud, port=12345,
                                  cdp_cookies=cdp)
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["source"] == "chrome"
        assert r["status"] == "ok"
        assert {c["name"] for c in r["cookies"]} == {"cookie2", "sc_company_id"}, \
            "域过滤+过期/session 排除"
        assert all(c["domain"].startswith(".") for c in r["cookies"])
        assert all("v-" + c["name"] == c["value"] for c in r["cookies"])
        # 启动参数边界
        assert len(rec["cmds"]) == 1
        cmd = rec["cmds"][0]
        wd = rec["workdirs"][0]
        assert cmd[0] == "C:/fake/chrome.exe"
        assert f"--user-data-dir={wd}" in cmd
        assert "--remote-debugging-port=12345" in cmd
        assert "--profile-directory=Default" in cmd
        assert "--no-first-run" in cmd
        assert "--headless=new" in cmd, "首拍 headless=new"
        assert "--remote-allow-origins=*" in cmd, \
            "ISSUE-3：CDP WS 握手 Origin 白名单参数（漏参=假就绪后 403）"
        assert 12345 != ch.TAKEOVER_PORT_BANNED, "动态端口绝不占 9222"
        # 最小复制集边界（Local State + Cookies + -wal；-shm 不存在不拷）
        copied_src = {Path(s).name for s, _ in rec["copied"]}
        assert "Local State" in copied_src
        assert "Cookies" in copied_src and "Cookies-wal" in copied_src
        assert "Cookies-shm" not in copied_src
        assert any(dst.endswith(str(wd / "Default" / "Network" / "Cookies"))
                   for _, dst in rec["copied"])
        # finally 清理边界
        assert rec["killed"] == [rec["procs"][0]], "浏览器进程被终止"
        assert not wd.exists(), "临时目录被清理"

    def test_cleanup_on_cdp_error(self, monkeypatch, tmp_path):
        """CDP 读 cookie 抛错 → takeover_failed 不 raise，finally 清理照常。"""
        _set_platform(monkeypatch, "win32")
        ud = _fake_user_data(tmp_path)
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=ud,
                                  cdp_error=RuntimeError("cdp boom"))
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "takeover_failed"
        assert r["cookies"] == []
        assert "--paste" in r["message"], "失败降级信息指路 --paste"
        assert rec["killed"] == [rec["procs"][0]]
        assert not rec["workdirs"][0].exists()

    def test_failure_message_never_contains_cookie_values(self, monkeypatch, tmp_path):
        """明文红线：失败 message 只带异常类型名，不带 CDP 内容。"""
        _set_platform(monkeypatch, "win32")
        ud = _fake_user_data(tmp_path)
        secret = "SUPERSECRETCOOKIEVALUE"
        _patch_takeover_env(
            monkeypatch, tmp_path, ud=ud,
            cdp_error=RuntimeError(f"leak? {secret}"))
        r = ch._harvest_chromium_via_takeover("chrome")
        assert secret not in (r.get("message") or "")

    def test_minimal_set_incomplete_fails(self, monkeypatch, tmp_path):
        """源 profile 无 Cookies 库 → takeover_failed（不启动浏览器）。"""
        _set_platform(monkeypatch, "win32")
        ud = _fake_user_data(tmp_path, with_cookies=False)
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=ud)
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "takeover_failed"
        assert rec["cmds"] == [], "无 cookie 可搬不该启动浏览器"
        assert rec["killed"] == []

    def test_minimal_set_legacy_layout(self, tmp_path):
        """旧版布局（Cookies 直接在 profile 下）也能拷。"""
        ud = _fake_user_data(tmp_path, legacy_layout=True)
        wd = tmp_path / "wd"
        wd.mkdir()
        ch._takeover_copy_minimal_set(ud, "Default", wd)
        assert (wd / "Local State").is_file()
        assert (wd / "Default" / "Cookies").is_file()


# ═══════════ ② headless 失败 → 去 headless 重试 ═══════════


class TestHeadlessRetry:
    def test_headless_down_retries_without_headless(self, monkeypatch, tmp_path):
        _set_platform(monkeypatch, "win32")
        ud = _fake_user_data(tmp_path)
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=ud, port=23456,
                                  wait_results=[False, True],
                                  cdp_cookies=[_cdp_cookie("cookie2", ".1688.com")])
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "ok"
        assert len(rec["cmds"]) == 2, "恰好重试一次"
        first, second = rec["cmds"]
        assert "--headless=new" in first
        assert "--headless=new" not in second, "重试拍去 headless"
        assert first[0] == second[0], "同 exe"
        port_args = [c for c in first if c.startswith("--remote-debugging-port=")]
        assert port_args[0] in second, "同端口同参数"
        udd_args = [c for c in first if c.startswith("--user-data-dir=")]
        assert udd_args[0] in second, "同临时目录"
        assert rec["killed"][0] is rec["procs"][0], "headless 失败拍先被终止"
        assert rec["killed"][-1] is rec["procs"][1], "finally 终止成功拍"

    def test_both_forms_fail(self, monkeypatch, tmp_path):
        _set_platform(monkeypatch, "win32")
        ud = _fake_user_data(tmp_path)
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=ud,
                                  wait_results=[False, False])
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "takeover_failed"
        assert "--paste" in r["message"]
        assert len(rec["cmds"]) == 2, "只重试一次不无限循环"
        assert len(rec["killed"]) == 2, "两拍进程都被终止"
        assert not rec["workdirs"][0].exists()


# ═══════════ ③ exe 缺失 ═══════════


class TestExeMissing:
    def test_no_exe_takeover_no_browser(self, monkeypatch, tmp_path):
        _set_platform(monkeypatch, "win32")
        rec = _patch_takeover_env(monkeypatch, tmp_path, exe=None,
                                  ud=_fake_user_data(tmp_path))
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "takeover_no_browser"
        assert r["cookies"] == []
        assert rec["cmds"] == [], "不启动浏览器"
        assert rec["killed"] == []

    def test_no_user_data_takeover_failed(self, monkeypatch, tmp_path):
        _set_platform(monkeypatch, "win32")
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=None)
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "takeover_failed"
        assert rec["cmds"] == []


# ═══════════ ④ 接线：平台分支 + kill-switch ═══════════


class TestHarvestChromiumWiring:
    def _spy_takeover(self, monkeypatch) -> list:
        calls = []

        def fake(browser, profile_dir_name="Default"):
            calls.append((browser, profile_dir_name))
            return {"source": browser, "status": "ok", "cookies": []}
        monkeypatch.setattr(ch, "_harvest_chromium_via_takeover", fake)
        return calls

    def test_win32_chromium_routes_to_takeover(self, monkeypatch):
        _set_platform(monkeypatch, "win32")
        monkeypatch.delenv("SKILL_DISABLE_TAKEOVER", raising=False)
        calls = self._spy_takeover(monkeypatch)
        r = ch._harvest_chromium("chrome")
        assert r["status"] == "ok"
        assert calls == [("chrome", None)], \
            "缺省透传 None（未显式指定语义，接管层解析 Default）"

    def test_win32_kill_switch_back_to_unsupported(self, monkeypatch):
        _set_platform(monkeypatch, "win32")
        monkeypatch.setenv("SKILL_DISABLE_TAKEOVER", "1")
        calls = self._spy_takeover(monkeypatch)
        r = ch._harvest_chromium("edge")
        assert r["status"] == "unsupported_source"
        assert r["cookies"] == []
        assert calls == [], "kill-switch 下不进接管"
        assert "--paste" in r["message"]

    def test_linux_unchanged_unsupported(self, monkeypatch):
        _set_platform(monkeypatch, "linux")
        calls = self._spy_takeover(monkeypatch)
        r = ch._harvest_chromium("chrome")
        assert r["status"] == "unsupported_source"
        assert calls == [], "linux 不走接管"

    def test_darwin_keychain_path_unchanged(self, monkeypatch, tmp_path):
        """darwin 行为零变化：不走接管，仍 Keychain 链（base 无库 → not_installed）。"""
        _set_platform(monkeypatch, "darwin")
        calls = self._spy_takeover(monkeypatch)
        monkeypatch.setitem(ch.CHROMIUM_BROWSERS["chrome"], "base",
                            str(tmp_path / "no-such-base"))
        r = ch._harvest_chromium("chrome")
        assert r["status"] == "not_installed"
        assert calls == [], "darwin 不走接管"

    def test_darwin_keychain_denied_still_keychain_semantics(self, monkeypatch,
                                                             tmp_path):
        _set_platform(monkeypatch, "darwin")
        base = tmp_path / "cb"
        (base / "Default").mkdir(parents=True)
        (base / "Default" / "Cookies").write_bytes(b"x")
        monkeypatch.setitem(ch.CHROMIUM_BROWSERS["chrome"], "base", str(base))
        monkeypatch.setattr(ch, "_keychain_safe_storage", lambda svc: None)
        assert ch._harvest_chromium("chrome")["status"] == "keychain_denied"

    def test_harvest_all_passes_browser_profile(self, monkeypatch):
        _set_platform(monkeypatch, "win32")
        monkeypatch.delenv("SKILL_DISABLE_TAKEOVER", raising=False)
        calls = self._spy_takeover(monkeypatch)
        report = ch.harvest_all(["chrome"], browser_profile="Profile 1")
        assert report["platform"] == "win32"
        assert calls == [("chrome", "Profile 1")], "--browser-profile 透传 takeover"

    def test_harvest_and_import_passes_browser_profile(self, monkeypatch):
        seen: dict = {}

        def fake_harvest_all(sources=None, browser_profile=None):
            seen["sources"] = sources
            seen["browser_profile"] = browser_profile
            return {"sources": {}, "total": 0, "platform": sys.platform}
        monkeypatch.setattr(ch, "harvest_all", fake_harvest_all)
        ch.harvest_and_import(sources=["chrome"], browser_profile="Default")
        assert seen == {"sources": ["chrome"], "browser_profile": "Default"}


# ═══════════ ⑤ 动态端口 ═══════════


class TestDynamicPort:
    def test_real_dynamic_port_never_banned(self):
        for _ in range(5):
            p = ch._takeover_dynamic_port()
            assert isinstance(p, int) and 0 < p < 65536
            assert p != ch.TAKEOVER_PORT_BANNED, "绝不占 9222 主实例端口"


# ═══════════ ⑥ profile 枚举 / 解析 ═══════════


class TestProfileEnum:
    def test_info_cache_excludes_system_guest(self, monkeypatch, tmp_path):
        ud = tmp_path / "ud"
        ud.mkdir()
        (ud / "Local State").write_text(json.dumps({
            "profile": {"info_cache": {
                "Default": {"name": "个人资料"},
                "Profile 1": {"name": "工作资料"},
                "System Profile": {"name": "System Profile"},
                "Guest Profile": {"name": "Guest Profile"},
            }}}), encoding="utf-8")
        dirs = {p["dir"] for p in ch._takeover_profiles(ud)}
        assert dirs == {"Default", "Profile 1"}, "System/Guest 名字过滤排除"

    def test_fallback_dirs_exclude_system_guest(self, tmp_path):
        """兜底扫目录：System/Guest 名字过滤排除（口径与 B2 探针一致）。
        ⚠️ 已知口径边界：无 info_cache 时组件目录（Crashpad 等）无法凭名字区分，
        会进候选表——只用于 --browser-profile 名字解析，不影响接管主流程。"""
        ud = tmp_path / "ud2"
        for name in ("Default", "Profile 2", "System Profile", "Guest Profile"):
            (ud / name).mkdir(parents=True)
        dirs = {p["dir"] for p in ch._takeover_profiles(ud)}
        assert dirs == {"Default", "Profile 2"}

    def test_resolve_default_and_dir_name(self, tmp_path):
        ud = tmp_path / "ud3"
        (ud / "Profile 1").mkdir(parents=True)
        (ud / "Local State").write_text(json.dumps(
            {"profile": {"info_cache": {"Profile 1": {"name": "工作资料"}}}}),
            encoding="utf-8")
        assert ch._takeover_resolve_profile(ud, "") == "Default"
        assert ch._takeover_resolve_profile(ud, None) == "Default"
        assert ch._takeover_resolve_profile(ud, "Profile 1") == "Profile 1", \
            "目录名直取"
        assert ch._takeover_resolve_profile(ud, "工作资料") == "Profile 1", \
            "info_cache 显示名解析为目录名"

    def test_resolve_unknown_passthrough(self, tmp_path):
        ud = tmp_path / "ud4"
        ud.mkdir()
        assert ch._takeover_resolve_profile(ud, "NoSuch") == "NoSuch", \
            "未知名原样透传（复制步骤自然失败成 takeover_failed）"


# ═══════════ ⑥b CLI --browser-profile 接线 ═══════════


class TestCliWiring:
    def _run(self, argv: list[str]):
        from scripts.cli import build_arg_parser, cmd_import_cookies
        args = build_arg_parser().parse_args(argv)
        return cmd_import_cookies(args)

    def test_browser_profile_flag_passthrough(self, monkeypatch, capsys):
        _set_platform(monkeypatch, "win32")
        seen: dict = {}

        def fake_harvest_and_import(cdp_url=ch.CDP_URL, sources=None,
                                    browser_profile=None):
            seen["sources"] = sources
            seen["browser_profile"] = browser_profile
            return {"scan": {"sources": {"chrome": {"status": "ok", "cookies": [
                {"name": "cookie2", "value": "v", "domain": ".1688.com",
                 "path": "/", "secure": True, "httpOnly": True,
                 "expires": time.time() + 3600}]}},
                "total": 1, "platform": "win32"},
                "injected": {"ok": True, "count": 1, "message": "注入 1 个 cookie"},
                "verified": {"1688": True, "seller": False}}
        monkeypatch.setattr(ch, "harvest_and_import", fake_harvest_and_import)
        rc = self._run(["import-cookies", "--browser-profile", "Profile 1"])
        assert rc == 0
        assert seen == {"sources": None, "browser_profile": "Profile 1"}

    def test_browser_profile_default_none(self, monkeypatch, capsys):
        _set_platform(monkeypatch, "win32")
        seen: dict = {}

        def fake_harvest_and_import(cdp_url=ch.CDP_URL, sources=None,
                                    browser_profile=None):
            seen["browser_profile"] = browser_profile
            return {"scan": {"sources": {}, "total": 0, "platform": "win32"},
                    "injected": None, "verified": None}
        monkeypatch.setattr(ch, "harvest_and_import", fake_harvest_and_import)
        rc = self._run(["import-cookies"])
        assert rc == 1
        assert seen["browser_profile"] is None

    def test_takeover_status_label_and_message_printed(self, monkeypatch, capsys):
        _set_platform(monkeypatch, "win32")

        def fake_harvest_and_import(cdp_url=ch.CDP_URL, sources=None,
                                    browser_profile=None):
            return {"scan": {"sources": {"chrome": {
                "status": "takeover_failed", "cookies": [],
                "message": "接管通道失败；可用 --paste 手动粘贴 Cookie 头"}},
                "total": 0, "platform": "win32"},
                "injected": None, "verified": None}
        monkeypatch.setattr(ch, "harvest_and_import", fake_harvest_and_import)
        rc = self._run(["import-cookies", "--sources", "chrome"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "接管" in out, "takeover_* 有人话标签（不暴露内部状态码）"
        assert "--paste" in out, "失败降级信息指路 --paste"
        assert "takeover_failed" not in out


# ═══════════ ⑦ B1 遗留四小件（#2 在 test_cookie_import_win.py）═══════════


class TestB1Items:
    def _run(self, argv: list[str]):
        from scripts.cli import build_arg_parser, cmd_import_cookies
        args = build_arg_parser().parse_args(argv)
        return cmd_import_cookies(args)

    def test_b1_1_cli_all_unsupported_branch_real_cover(self, monkeypatch, capsys):
        """#1 全源 unsupported 分支真覆盖：win32 + kill-switch + --sources chrome,safari
        （firefox 不在 sources 里，全 scanned 源确实全 unsupported）。"""
        _set_platform(monkeypatch, "win32")
        monkeypatch.setenv("SKILL_DISABLE_TAKEOVER", "1")
        rc = self._run(["import-cookies", "--sources", "chrome,safari"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "暂不支持" in out, "专用分支文案命中（此前该分支零覆盖）"
        assert "--paste" in out
        assert "unsupported_source" not in out, "人话提示不暴露内部状态码"

    def test_b1_3_firefox_wal_shm_sidecars_copied(self, monkeypatch, tmp_path):
        """#3 Firefox 读取补拷 -wal/-shm（刚登录未 checkpoint 的最新态不漏读）。"""
        _set_platform(monkeypatch, "win32")
        appdata = tmp_path / "ad"
        root = appdata / "Mozilla" / "Firefox"
        prof = root / "Profiles" / "abc.default-release"
        prof.mkdir(parents=True)
        _make_firefox_db(prof / "cookies.sqlite")
        Path(str(prof / "cookies.sqlite") + "-wal").write_bytes(b"wal")
        Path(str(prof / "cookies.sqlite") + "-shm").write_bytes(b"shm")
        (root / "profiles.ini").write_text(
            "[InstallX]\nDefault=Profiles/abc.default-release\n", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(appdata))
        copied: list[tuple[str, str]] = []
        real_copy2 = shutil.copy2

        def spy_copy2(src, dst, **kw):
            copied.append((str(src), str(dst)))
            return real_copy2(src, dst, **kw)
        monkeypatch.setattr(ch.shutil, "copy2", spy_copy2)
        r = ch._harvest_firefox()
        assert r["status"] == "ok"
        src_names = {Path(s).name for s, _ in copied}
        assert "cookies.sqlite-wal" in src_names
        assert "cookies.sqlite-shm" in src_names

    def test_b1_3_firefox_sidecar_absent_ok(self, monkeypatch, tmp_path):
        """sidecar 不存在（已 checkpoint）不拷也不报错。"""
        _set_platform(monkeypatch, "win32")
        appdata = tmp_path / "ad"
        root = appdata / "Mozilla" / "Firefox"
        prof = root / "Profiles" / "abc.default-release"
        prof.mkdir(parents=True)
        _make_firefox_db(prof / "cookies.sqlite")
        (root / "profiles.ini").write_text(
            "[InstallX]\nDefault=Profiles/abc.default-release\n", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(appdata))
        assert ch._harvest_firefox()["status"] == "ok"

    def test_b1_i1_firefox_conn_closed_on_execute_error(self, monkeypatch, tmp_path):
        """终审 I-1：execute 异常（损坏库/撕裂 WAL，浏览器运行中正是主场景）时
        conn 也必须关——Windows 文件锁下句柄存活会让 TemporaryDirectory 清理抛
        PermissionError 冲出 _harvest_firefox，把「单 profile 跳过」升级成整源
        error（探针 _query_copy_rows 同构先例的对称修复）。"""
        _set_platform(monkeypatch, "win32")
        appdata = tmp_path / "ad"
        root = appdata / "Mozilla" / "Firefox"
        bad = root / "Profiles" / "bad.default"
        good = root / "Profiles" / "good.default"
        bad.mkdir(parents=True)
        good.mkdir(parents=True)
        _make_firefox_db(bad / "cookies.sqlite")
        _make_firefox_db(good / "cookies.sqlite")
        (root / "profiles.ini").write_text(
            "[InstallX]\nDefault=Profiles/bad.default\n"
            "[Profile0]\nPath=Profiles/bad.default\n"
            "[Profile1]\nPath=Profiles/good.default\n",
            encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(appdata))
        closed: list[bool] = []
        real_connect = sqlite3.connect

        class _BoomConn:
            def execute(self, *args, **kwargs):
                raise sqlite3.DatabaseError("file is not a database")

            def close(self):
                closed.append(True)

        calls = {"n": 0}

        def fake_connect(path, *args, **kwargs):
            # connect 收到的是临时副本路径（不含源 profile 名），按调用序号命中：
            # ini Install Default 排第一（bad）——该顺序已由 profiles.ini 优先级用例锁定
            calls["n"] += 1
            if calls["n"] == 1:
                return _BoomConn()
            return real_connect(path, *args, **kwargs)
        monkeypatch.setattr(ch.sqlite3, "connect", fake_connect)
        r = ch._harvest_firefox()
        assert calls["n"] == 2, "坏坏去重后恰扫 bad+good 两库"
        assert closed == [True], "execute 炸掉后 conn 仍被 finally 关闭"
        assert r["status"] == "ok", "坏 profile 只跳过，好 profile 照常出 cookie"
        assert {c["name"] for c in r["cookies"]} == {"cookie2"}

    def test_b1_4_parse_inline_cookie_prefix_per_chunk(self):
        """#4 多行请求头粘贴：非首行的 Cookie: 前缀不再解析出垃圾对。"""
        assert ch.parse_cookie_header("Cookie: a=1\nCookie: b=2") == \
            [("a", "1"), ("b", "2")]
        assert ch.parse_cookie_header(
            "a=1;\r\nCookie: b=2; cookie:c=3") == \
            [("a", "1"), ("b", "2"), ("c", "3")], "行内前缀大小写不敏感"
        # 既有语义不回归：普通对、空值、无 = 碎片
        assert ch.parse_cookie_header("k=a=b=c") == [("k", "a=b=c")]
        assert ch.parse_cookie_header("novalue=") == [("novalue", "")]
        assert ch.parse_cookie_header("no-equals-token") == []


# ═══════════ ⑧ ISSUE-2/3：源 Cookies 独占锁 + origin 参数 ═══════════


def _locked_patch(monkeypatch, locked_dirs: set[str]) -> None:
    """让 _copy_shared 对指定 profile 目录下的 Cookies* 抛 WinError 32 语义的
    PermissionError；_is_locked 按同口径分类（仅锁 profile 内 Cookies 路径）。"""
    real_copy = ch._copy_shared

    def fake_copy(src, dst):
        s = Path(src)
        if s.name in ("Cookies", "Cookies-wal", "Cookies-shm") \
                and s.parent.parent.name in locked_dirs:
            raise PermissionError(13, "in use")
        return real_copy(src, dst)
    monkeypatch.setattr(ch, "_copy_shared", fake_copy)
    monkeypatch.setattr(ch, "_is_locked",
                        lambda p: Path(p).name in ("Cookies", "Cookies-wal",
                                                   "Cookies-shm")
                        and Path(p).parent.parent.name in locked_dirs)


def _two_profile_ud(tmp_path: Path) -> Path:
    """Default + Profile 3 双 profile 假 User Data（Local State + 各自 Cookies）。"""
    ud = tmp_path / "ud2p"
    ud.mkdir()
    (ud / "Local State").write_bytes(b"{}")
    for name in ("Default", "Profile 3"):
        (ud / name / "Network").mkdir(parents=True)
        (ud / name / "Network" / "Cookies").write_bytes(b"db-" + name.encode())
    return ud


class TestLockedSource:
    def test_locked_default_auto_falls_back_to_readable_profile(
            self, monkeypatch, tmp_path):
        """ISSUE-2 主路径：Default 被占用 + 未显式指定 → 自动改用可读 profile。"""
        _set_platform(monkeypatch, "win32")
        ud = _two_profile_ud(tmp_path)
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=ud,
                                  cdp_cookies=[_cdp_cookie("cookie2", ".1688.com")])
        _locked_patch(monkeypatch, locked_dirs={"Default"})
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "ok"
        assert {c["name"] for c in r["cookies"]} == {"cookie2"}
        assert "--profile-directory=Profile 3" in rec["cmds"][0], "改用未占用 profile"
        msg = r.get("message") or ""
        assert "Default" in msg and "Profile 3" in msg, "改用说明对用户可见"
        assert rec["killed"] == [rec["procs"][0]]
        assert not rec["workdirs"][0].exists(), "临时目录照常清理"

    def test_locked_explicit_profile_fixed_guidance(self, monkeypatch, tmp_path):
        """显式 --browser-profile 命中锁 → 不静默换 profile，给退出指引。"""
        _set_platform(monkeypatch, "win32")
        ud = _two_profile_ud(tmp_path)
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=ud)
        _locked_patch(monkeypatch, locked_dirs={"Default"})
        r = ch._harvest_chromium_via_takeover("chrome",
                                              profile_dir_name="Default")
        assert r["status"] == "takeover_failed"
        assert "独占锁定" in r["message"] and "退出" in r["message"]
        assert "--paste" in r["message"]
        assert rec["cmds"] == [], "不启动浏览器"

    def test_locked_no_alternative_fixed_guidance(self, monkeypatch, tmp_path):
        """唯一 profile 被占（无替代）→ 明确指引而非笼统失败。"""
        _set_platform(monkeypatch, "win32")
        ud = _fake_user_data(tmp_path)
        rec = _patch_takeover_env(monkeypatch, tmp_path, ud=ud)
        _locked_patch(monkeypatch, locked_dirs={"Default"})
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "takeover_failed"
        assert "独占锁定" in r["message"] and "退出" in r["message"]
        assert rec["cmds"] == []

    def test_copy_minimal_set_classifies_lock_vs_other(self, monkeypatch,
                                                       tmp_path):
        """复制失败分流：锁类 → TakeoverSourceLocked；非锁类原样透传。"""
        ud = _fake_user_data(tmp_path)
        wd = tmp_path / "wd"
        wd.mkdir()
        monkeypatch.setattr(ch, "_is_locked", lambda p: True)

        def boom(src, dst):
            raise PermissionError(13, "in use")
        monkeypatch.setattr(ch, "_copy_shared", boom)
        with pytest.raises(ch.TakeoverSourceLocked):
            ch._takeover_copy_minimal_set(ud, "Default", wd)

        monkeypatch.setattr(ch, "_is_locked", lambda p: False)
        with pytest.raises(PermissionError) as ei:
            ch._takeover_copy_minimal_set(ud, "Default", wd)
        assert not isinstance(ei.value, ch.TakeoverSourceLocked)

    def test_ws_handshake_failure_human_message(self, monkeypatch, tmp_path):
        """ISSUE-3 佐证文案：WS 握手被拒 → 人话（不甩类型名、不夹 CDP 内容）。"""
        _set_platform(monkeypatch, "win32")
        ud = _fake_user_data(tmp_path)
        ws_exc = type("WebSocketBadStatusException", (Exception,), {})("403")
        _patch_takeover_env(monkeypatch, tmp_path, ud=ud, cdp_error=ws_exc)
        r = ch._harvest_chromium_via_takeover("chrome")
        assert r["status"] == "takeover_failed"
        assert "握手" in r["message"] and "Origin" in r["message"]
        assert "WebSocketBadStatusException" not in r["message"]
        assert "--paste" in r["message"]

    def test_pick_readable_profile_skips_locked_and_empty(self, tmp_path,
                                                          monkeypatch):
        """替代 profile 选择：跳过被锁候选，选中首个可读者；全锁 → None。"""
        ud = tmp_path / "ud3p"
        ud.mkdir()
        (ud / "Local State").write_bytes(b"{}")
        for name in ("Profile 1", "Profile 2", "Profile 3"):
            (ud / name / "Network").mkdir(parents=True)
            (ud / name / "Network" / "Cookies").write_bytes(b"db")
        monkeypatch.setattr(ch, "_is_locked", lambda p: "Profile 1" in str(p))
        assert ch._pick_readable_profile(ud, "Default") == "Profile 2"
        monkeypatch.setattr(ch, "_is_locked", lambda p: True)
        assert ch._pick_readable_profile(ud, "Default") is None

    def test_is_locked_semantics(self, tmp_path):
        """_is_locked：可读 → False；缺失 → False（非锁类）；errno 13 → True。"""
        import unittest.mock as _m
        f = tmp_path / "ok.txt"
        f.write_bytes(b"x")
        assert ch._is_locked(f) is False
        assert ch._is_locked(tmp_path / "no-such") is False
        with _m.patch("builtins.open",
                      side_effect=PermissionError(13, "in use")):
            assert ch._is_locked(f) is True


# ═══════════ 冒烟：模块红线（零解密 API）═══════════


def test_takeover_never_imports_decryption_apis():
    """红线：接管通道实现零解密调用——AST 层禁 ctypes/win32crypt/cryptography/
    comtypes/win32com（IElevator COM 伪装的现实 Python 路径）import 与
    CryptUnprotect/CryptProtect 调用（注释可提红线，代码不得触）；
    生产侧 cookie_harvest 与诊断侧 probe_win_cookies 一并纳入扫描。"""
    import ast
    import scripts.probe_win_cookies as probe_mod
    banned_modules = {"ctypes", "win32crypt", "cryptography", "comtypes",
                      "win32com"}
    for mod in (ch, probe_mod):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0] not in banned_modules, a.name
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned_modules, \
                    node.module
            elif isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", "")
                assert "CryptUnprotect" not in name and "CryptProtect" not in name, \
                    name
