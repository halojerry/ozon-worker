# -*- coding: utf-8 -*-
"""Windows cookie 导入 v1（feat/win-cookie-import-v1 B-T1/T2/T3）。

锁定：
1. per-source 平台闸矩阵：整机 darwin 闸废除后——win32/linux 下
   chrome/edge/brave/safari=unsupported_source、firefox 可用；darwin 下全可用。
2. Firefox Windows/Linux 根目录解析 + profiles.ini（Install* Default 优先 /
   Profile* Path 回退 / 相对路径拼接 / 绝对路径 / ini 缺失兜底）。
3. --paste 手动粘贴通道：Cookie 头容错解析、cookie 名指纹落域、
   未知 cookie 名报错提示 --site、显式 --site 落域、双域全注入。
4. --paste 与源扫描互斥（CLI 级）+ expires=0 会话 cookie 注入载荷省略 expires。

全部 mock/临时目录，不碰真浏览器、不解密任何真实 cookie 库。
cookie 明文不落任何断言输出（红线）。

运行: cd skill && .venv314/bin/python -m pytest tests/test_cookie_import_win.py -q
"""
from __future__ import annotations

import io
import sqlite3
import sys
import time
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.cookie_harvest as ch  # noqa: E402


# ═══════════ 工具 ═══════════


def _set_platform(monkeypatch, name: str) -> None:
    """替换 cookie_harvest 可见的 sys（模块级引用，仿 v069 测试先例）。"""
    monkeypatch.setattr(ch, "sys", types.SimpleNamespace(platform=name))


def _make_firefox_db(path: Path) -> None:
    """形似 Firefox cookies.sqlite：1 条有效 1688 + 1 条外域（应被过滤）。"""
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
    conn.execute("INSERT INTO moz_cookies (host, name, value, path, expiry, "
                 "isSecure, isHttpOnly) VALUES (?,?,?,?,?,?,?)",
                 ("example.com", "junk", "x", "/", future, 0, 0))
    conn.commit()
    conn.close()


def _fake_win_firefox(monkeypatch, tmp_path: Path) -> Path:
    """在 tmp 造一棵 Windows 形态 Firefox 目录树（APPDATA 根 + profiles.ini + profile）。"""
    appdata = tmp_path / "AppData" / "Roaming"
    root = appdata / "Mozilla" / "Firefox"
    prof = root / "Profiles" / "abc123.default-release"
    prof.mkdir(parents=True)
    _make_firefox_db(prof / "cookies.sqlite")
    (root / "profiles.ini").write_text(
        "[Install4F96D1932A9F858E]\n"
        "Default=Profiles/abc123.default-release\n"
        "Locked=1\n",
        encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    return root


# ═══════════ ① per-source 平台闸矩阵 ═══════════


class TestPerSourceGate:
    def test_win32_chromium_unsupported_source(self, monkeypatch):
        """B-T4 起 win32 Chromium 默认走接管通道；kill-switch 下回闸语义
        （hermetic：真实 Windows 测试机不真启浏览器）。"""
        _set_platform(monkeypatch, "win32")
        monkeypatch.setenv("SKILL_DISABLE_TAKEOVER", "1")
        for src in ("chrome", "edge", "brave"):
            r = ch._harvest_chromium(src)
            assert r["status"] == "unsupported_source", src
            assert r["cookies"] == []
            assert r["message"], "须带人话说明"
            assert r["source"] == src

    def test_win32_safari_unsupported_source(self, monkeypatch):
        _set_platform(monkeypatch, "win32")
        r = ch._harvest_safari()
        assert r["status"] == "unsupported_source"
        assert r["cookies"] == []

    def test_linux_chromium_and_safari_unsupported_source(self, monkeypatch):
        _set_platform(monkeypatch, "linux")
        assert ch._harvest_chromium("chrome")["status"] == "unsupported_source"
        assert ch._harvest_safari()["status"] == "unsupported_source"

    def test_win32_firefox_available(self, monkeypatch, tmp_path):
        _set_platform(monkeypatch, "win32")
        _fake_win_firefox(monkeypatch, tmp_path)
        r = ch._harvest_firefox()
        assert r["status"] == "ok"
        names = {c["name"] for c in r["cookies"]}
        assert names == {"cookie2"}, "外域被过滤"

    def test_win32_harvest_all_matrix(self, monkeypatch, tmp_path):
        """整机闸废除：win32 下 firefox 照常扫描；chromium/safari 在 kill-switch
        下报 unsupported_source（接管默认路由的 mock 覆盖见 test_takeover_channel）。"""
        _set_platform(monkeypatch, "win32")
        monkeypatch.setenv("SKILL_DISABLE_TAKEOVER", "1")
        _fake_win_firefox(monkeypatch, tmp_path)
        report = ch.harvest_all()
        st = {n: r["status"] for n, r in report["sources"].items()}
        assert st["chrome"] == st["edge"] == st["brave"] == "unsupported_source"
        assert st["safari"] == "unsupported_source"
        assert st["firefox"] == "ok"
        assert report["total"] >= 1
        assert report["platform"] == "win32", "platform 字段=实际平台名"

    def test_darwin_no_source_unsupported(self, monkeypatch):
        """macOS 下所有源都能进各自扫描逻辑（不存在 unsupported_source 状态）。"""
        _set_platform(monkeypatch, "darwin")
        for src in ("chrome", "edge", "brave"):
            monkeypatch.setitem(ch.CHROMIUM_BROWSERS[src], "base",
                                "/nonexistent/cookie_harvest_test")
        monkeypatch.setattr(ch, "SAFARI_COOKIES", "/nonexistent/x.binarycookies")
        monkeypatch.setattr(ch, "FIREFOX_BASE", "/nonexistent/ff/Profiles")
        report = ch.harvest_all()
        st = {n: r["status"] for n, r in report["sources"].items()}
        assert st == {"chrome": "not_installed", "edge": "not_installed",
                      "brave": "not_installed", "firefox": "not_installed",
                      "safari": "not_installed"}
        assert report["platform"] == "darwin"


# ═══════════ ② Firefox 根目录 + profiles.ini ═══════════


class TestFirefoxRootAndProfilesIni:
    def test_win32_root_from_appdata(self, monkeypatch, tmp_path):
        _set_platform(monkeypatch, "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert ch._firefox_root() == tmp_path / "Mozilla" / "Firefox"

    def test_win32_root_missing_appdata(self, monkeypatch):
        _set_platform(monkeypatch, "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        assert ch._firefox_root() is None

    def test_linux_root_default(self, monkeypatch, tmp_path):
        _set_platform(monkeypatch, "linux")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert ch._firefox_root() == tmp_path / ".mozilla" / "firefox"

    def test_ini_install_default_priority_and_relative_join(self, tmp_path):
        root = tmp_path / "Firefox"
        root.mkdir()
        (root / "Profiles" / "aaa.default-release").mkdir(parents=True)
        (root / "Profiles" / "bbb.dev").mkdir(parents=True)
        (root / "profiles.ini").write_text(
            "[Install4F96D1932A9F858E]\n"
            "Default=Profiles/aaa.default-release\n"
            "Locked=1\n"
            "\n"
            "[Profile0]\n"
            "Name=default\n"
            "IsRelative=1\n"
            "Path=Profiles/aaa.default-release\n"
            "\n"
            "[Profile1]\n"
            "Name=dev\n"
            "IsRelative=1\n"
            "Path=Profiles/bbb.dev\n",
            encoding="utf-8")
        dirs = ch._firefox_profile_dirs(root)
        assert dirs[0] == root / "Profiles" / "aaa.default-release", \
            "Install* Default= 优先"
        assert root / "Profiles" / "bbb.dev" in dirs, "Profile* Path= 回退收录"
        assert dirs.count(root / "Profiles" / "aaa.default-release") == 1, \
            "Install 与 Profile 重复条目去重"

    def test_ini_absolute_path_kept_as_is(self, tmp_path):
        external = tmp_path / "elsewhere" / "prof"
        external.mkdir(parents=True)
        root = tmp_path / "Firefox"
        root.mkdir()
        (root / "profiles.ini").write_text(
            f"[Profile0]\nIsRelative=0\nPath={external}\n", encoding="utf-8")
        assert ch._firefox_profile_dirs(root) == [external]

    def test_ini_missing_fallback_rglob_cookies_sqlite(self, tmp_path):
        root = tmp_path / "Firefox"
        prof = root / "Profiles" / "x.default"
        prof.mkdir(parents=True)
        _make_firefox_db(prof / "cookies.sqlite")
        assert ch._firefox_profile_dirs(root) == [prof]

    def test_ini_malformed_falls_back(self, tmp_path):
        root = tmp_path / "Firefox"
        prof = root / "Profiles" / "y.default"
        prof.mkdir(parents=True)
        _make_firefox_db(prof / "cookies.sqlite")
        (root / "profiles.ini").write_bytes(b"\x00\x01broken")
        assert prof in ch._firefox_profile_dirs(root)

    def test_harvest_only_profiles_with_cookies_db(self, monkeypatch, tmp_path):
        """profiles.ini 指向的目录没有 cookies.sqlite → 跳过，不报错。
        （B1 #2 修正：原 fixture 首行 Default= 裸键在任何 section 之前，
        configparser 抛 MissingSectionHeaderError 走了损坏兜底路径——修正为
        合法 ini，覆盖「ini 解析成功但无 cookies.sqlite」的预期路径。）"""
        _set_platform(monkeypatch, "win32")
        appdata = tmp_path / "ad"
        root = appdata / "Mozilla" / "Firefox"
        empty = root / "Profiles" / "empty.default"
        empty.mkdir(parents=True)
        (root / "profiles.ini").write_text(
            "[InstallX]\nDefault=Profiles/empty.default\n"
            "[Profile0]\nPath=Profiles/empty.default\n",
            encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(appdata))
        assert ch._firefox_profile_dirs(root) == [empty], \
            "ini 解析成功（损坏 ini 会走 rglob 兜底且此处解析不出该目录）"
        r = ch._harvest_firefox()
        assert r["status"] == "not_installed", "有 profile 无目标 cookie=按既有语义"
        assert r["cookies"] == []

    def test_darwin_firefox_path_unchanged(self, monkeypatch, tmp_path):
        """macOS 语义保持 v0.69：直接扫 FIREFOX_BASE 子目录，不走 profiles.ini。"""
        _set_platform(monkeypatch, "darwin")
        base = tmp_path / "Profiles"
        prof = base / "zzzz.default-release"
        prof.mkdir(parents=True)
        _make_firefox_db(prof / "cookies.sqlite")
        (base / "profiles.ini").write_text("[InstallX]\nDefault=nothing\n",
                                           encoding="utf-8")
        monkeypatch.setattr(ch, "FIREFOX_BASE", str(base))
        r = ch._harvest_firefox()
        assert r["status"] == "ok"
        assert {c["name"] for c in r["cookies"]} == {"cookie2"}


# ═══════════ ③ --paste 解析 / 指纹 / 落域 ═══════════


class TestPasteParsing:
    def test_parse_strips_cookie_prefix_newlines_whitespace(self):
        assert ch.parse_cookie_header("Cookie: a=1; b=2\r\nc=3\n") == \
            [("a", "1"), ("b", "2"), ("c", "3")]
        assert ch.parse_cookie_header("cookie:  x = y ") == [("x", "y")]
        assert ch.parse_cookie_header("a=1;; ;b=2") == [("a", "1"), ("b", "2")]

    def test_parse_edge_cases(self):
        assert ch.parse_cookie_header("") == []
        assert ch.parse_cookie_header("   ") == []
        assert ch.parse_cookie_header("no-equals-token") == []
        assert ch.parse_cookie_header("novalue=") == [("novalue", "")], \
            "空值 cookie 保留（用户显式输入）"
        assert ch.parse_cookie_header("=novalue") == [], "空名对跳过"

    def test_parse_keeps_value_with_equals_and_semicolon_free(self):
        assert ch.parse_cookie_header("k=a=b=c") == [("k", "a=b=c")]

    def test_classify_fingerprints_two_sites(self):
        grouped, skipped = ch._classify_paste_pairs(
            [("cookie2", "v"), ("_m_h5_tk", "t"), ("sc_company_id", "s"),
             ("unknown", "u")])
        assert set(grouped) == {"1688", "ozon-seller"}
        assert grouped["1688"] == [("cookie2", "v"), ("_m_h5_tk", "t")]
        assert grouped["ozon-seller"] == [("sc_company_id", "s")]
        assert skipped == 1

    def test_fingerprint_sets_match_brief(self):
        """指纹清单以裁决后的清单为准（不含含逗号的笔误 token）。"""
        assert ch.PASTE_NAME_FINGERPRINTS["1688"] == frozenset(
            {"cookie2", "__cn_logon__", "_m_h5_tk", "_m_h5_tk_enc",
             "tfstk", "isg"})
        assert ch.PASTE_NAME_FINGERPRINTS["ozon-seller"] == frozenset(
            {"sc_company_id", "__Secure-access_token", "abt_data"})


class TestPasteAndImport:
    def _ok_inject(self, captured: dict):
        def fake(cookies, cdp_url=ch.CDP_URL):
            captured["cookies"] = cookies
            return {"ok": True, "count": len(cookies), "message": "ok"}
        return fake

    def test_unknown_names_error_hints_site(self, monkeypatch):
        called = mock.Mock()
        monkeypatch.setattr(ch, "inject_cookies", called)
        r = ch.paste_and_import("foo=bar; baz=qux")
        assert "--site" in r.get("error", "")
        assert not called.called, "无法识别不得注入"
        assert r.get("cookies") == []

    def test_explicit_site_assigns_domain(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(ch, "inject_cookies", self._ok_inject(captured))
        monkeypatch.setattr(ch, "verify_after_import",
                            lambda cdp_url=ch.CDP_URL:
                            {"1688": True, "seller": False})
        r = ch.paste_and_import("whatever=1; other=2", site="1688")
        assert not r.get("error")
        assert all(c["domain"] == ".1688.com" for c in r["cookies"])
        assert len(r["cookies"]) == 2
        c0 = r["cookies"][0]
        assert c0["path"] == "/" and c0["secure"] is True
        assert c0["expires"] == 0, "会话级（paste 显式输入优先，见实现注释）"
        assert r["verified"]["1688"] is True
        assert r["sites"] == ["1688"]

    def test_both_fingerprints_all_injected_each_to_own_domain(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(ch, "inject_cookies", self._ok_inject(captured))
        monkeypatch.setattr(ch, "verify_after_import",
                            lambda cdp_url=ch.CDP_URL:
                            {"1688": False, "seller": False})
        header = ("Cookie: cookie2=a; _m_h5_tk_enc=b; __cn_logon__=c; tfstk=d; "
                  "isg=e; sc_company_id=f; __Secure-access_token=g; abt_data=h")
        r = ch.paste_and_import(header)
        assert not r.get("error")
        assert len(r["cookies"]) == 8, "双域指纹全命中=全部注入"
        domains = {c["domain"] for c in captured["cookies"]}
        assert domains == {".1688.com", ".ozon.ru"}
        assert sorted(r["sites"]) == ["1688", "ozon-seller"]

    def test_unrecognized_skipped_when_fingerprint_hit(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(ch, "inject_cookies", self._ok_inject(captured))
        monkeypatch.setattr(ch, "verify_after_import",
                            lambda cdp_url=ch.CDP_URL:
                            {"1688": False, "seller": False})
        r = ch.paste_and_import("cookie2=a; junk_cookie=x")
        assert len(r["cookies"]) == 1
        assert r["skipped"] == 1

    def test_empty_paste_error(self, monkeypatch):
        called = mock.Mock()
        monkeypatch.setattr(ch, "inject_cookies", called)
        r = ch.paste_and_import("   ")
        assert r.get("error") and not called.called

    def test_bad_site_value_error(self, monkeypatch):
        r = ch.paste_and_import("a=1", site="taobao")
        assert r.get("error")

    def test_inject_failure_skips_verify(self, monkeypatch):
        monkeypatch.setattr(ch, "inject_cookies",
                            lambda cookies, cdp_url=ch.CDP_URL:
                            {"ok": False, "count": 0, "message": "x"})
        verified = mock.Mock()
        monkeypatch.setattr(ch, "verify_after_import", verified)
        r = ch.paste_and_import("cookie2=a")
        assert r["verified"] is None
        assert not verified.called

    def test_cookie_values_never_logged(self, monkeypatch, caplog):
        """明文红线：注入失败路径的日志不得含 cookie value。"""
        secret = "SUPERSECRETVALUE123"
        monkeypatch.setattr(ch, "inject_cookies",
                            lambda cookies, cdp_url=ch.CDP_URL:
                            {"ok": False, "count": 0, "message": "x"})
        import logging
        with caplog.at_level(logging.DEBUG, logger="scripts.lib.cookie_harvest"):
            ch.paste_and_import(f"cookie2={secret}")
        assert secret not in caplog.text


# ═══════════ ④ paste 与扫描互斥 + 注入载荷会话语义 ═══════════


class _FakeTab:
    def __init__(self):
        self.calls = []

    def _send(self, method, params=None, msg_id=None):
        self.calls.append((method, params))
        return len(self.calls)

    def _recv_until_id(self, target_id, timeout=15):
        return {"result": {}}

    def close(self, close_remote=True):
        pass


class _FakeConn:
    last_tab = None

    def __init__(self, url):
        self.tab = _FakeTab()
        _FakeConn.last_tab = self.tab

    def new_tab(self, url="about:blank", background=False):
        return self.tab

    def close(self):
        pass


class TestPasteCliWiring:
    def _run(self, argv: list[str]):
        from scripts.cli import build_arg_parser, cmd_import_cookies
        args = build_arg_parser().parse_args(argv)
        return cmd_import_cookies(args)

    def test_paste_flag_skips_source_scan(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO("cookie2=abc"))
        scan_calls = mock.Mock()
        monkeypatch.setattr(ch, "harvest_and_import", scan_calls)
        monkeypatch.setattr(ch, "paste_and_import",
                            lambda header, site=None, cdp_url=ch.CDP_URL:
                            {"cookies": [{"name": "cookie2", "value": "v",
                                          "domain": ".1688.com", "path": "/",
                                          "secure": True, "httpOnly": False,
                                          "expires": 0.0}],
                             "sites": ["1688"], "skipped": 0,
                             "injected": {"ok": True, "count": 1,
                                          "message": "注入 1 个 cookie"},
                             "verified": {"1688": True, "seller": False}})
        rc = self._run(["import-cookies", "--paste"])
        assert rc == 0
        assert not scan_calls.called, "--paste 必须跳过源扫描"
        out = capsys.readouterr().out
        assert "1688 登录" in out

    def test_paste_reads_stdin_and_passes_site(self, monkeypatch, capsys):
        seen: dict = {}
        monkeypatch.setattr(sys, "stdin", io.StringIO("cookie2=abc"))

        def fake(header, site=None, cdp_url=ch.CDP_URL):
            seen["header"] = header
            seen["site"] = site
            return {"error": "x", "cookies": []}
        monkeypatch.setattr(ch, "paste_and_import", fake)
        rc = self._run(["import-cookies", "--paste", "--site", "1688"])
        assert rc == 1
        assert seen["header"] == "cookie2=abc"
        assert seen["site"] == "1688"

    def test_cli_paste_error_hints_site(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO("foo=bar"))
        monkeypatch.setattr(ch, "paste_and_import",
                            lambda header, site=None, cdp_url=ch.CDP_URL:
                            {"error": "未命中指纹，请用 --site 1688|ozon-seller",
                             "cookies": []})
        rc = self._run(["import-cookies", "--paste"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "--site" in out

    def test_cli_all_sources_unsupported_human_message(self, monkeypatch, capsys):
        """win32 全源零 cookie → 人话提示（含 --paste 出路）。
        kill-switch 让 chromium 源停在不支持态，测试 hermetic（不真启接管）。"""
        _set_platform(monkeypatch, "win32")
        monkeypatch.setenv("SKILL_DISABLE_TAKEOVER", "1")
        monkeypatch.delenv("APPDATA", raising=False)
        rc = self._run(["import-cookies"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "--paste" in out
        assert "unsupported_source" not in out, "人话提示不暴露内部状态码"

    def test_inject_payload_session_cookie_omits_expires(self, monkeypatch):
        """expires=0（会话级）在 CDP 载荷中省略 expires 键——显式传 0 会被当作
        已过期丢弃，省略才是 CDP 的会话语义。"""
        monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", _FakeConn)
        cookies = [{"name": "cookie2", "value": "v", "domain": ".1688.com",
                    "path": "/", "secure": True, "httpOnly": False,
                    "expires": 0.0}]
        r = ch.inject_cookies(cookies)
        assert r["ok"] is True
        method, params = _FakeConn.last_tab.calls[0]
        assert method == "Storage.setCookies"
        assert "expires" not in params["cookies"][0]

    def test_inject_payload_keeps_real_expiry(self, monkeypatch):
        """既有 harvested cookie（expires>0）行为不变。"""
        monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", _FakeConn)
        cookies = [ch._cookie_dict("1688.com", "cookie2", "v", "/",
                                   time.time() + 3600, False, False)]
        ch.inject_cookies(cookies)
        _, params = _FakeConn.last_tab.calls[0]
        assert params["cookies"][0]["expires"] > 0

    def test_auto_import_no_whole_machine_gate(self, monkeypatch):
        """try_auto_import 整机 darwin 闸废除：win32 下仍会真实走到扫描。"""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        _set_platform(monkeypatch, "win32")
        monkeypatch.setattr(ch, "auto_fallback_allowed", lambda probe: True)
        monkeypatch.setattr(ch, "mark_auto_fallback_attempted", lambda probe: None)
        called = mock.Mock(return_value={"verified": {"seller": False}})
        monkeypatch.setattr(ch, "harvest_and_import", called)
        assert ch.try_auto_import("seller_login") is False
        assert called.called, "win32 不再被整机平台闸短路（per-source 扫描照常尝试）"
