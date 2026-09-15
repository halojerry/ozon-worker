# -*- coding: utf-8 -*-
"""Windows cookie 只读探针 probe_win_cookies.py 测试（feat/win-cookie-import-v1 B-T0）。

锁定（简报测试清单）：
1. 假目录树 fixture（两个 Chromium profile + Local State + Cookies sqlite 造 v10/v20
   混合行 + Firefox profiles.ini/cookies.sqlite）→ 报告结构逐字段断言（含判定矩阵行）。
2. 前缀统计正确性（v20 占比 0 与 >0 两态 → dpapi_v10 / takeover 通道判定）。
3. 目标域过滤正确（后缀匹配，防 `not1688.com` / `ozon.ru.evil.com` 误命中）。
4. takeover 分支 mock（浏览器启动/CDP 读数/清理三段，不真启浏览器）——临时目录清理
   finally 兜底断言。
5. cli 子命令 wrapper 冒烟（mock 主函数）。

红线（测试同样锁）：不碰真实浏览器目录（全部 tmp_path）、不解密、cookie 值
不落报告 JSON（明文 fixture 值断言不存在于报告文本）。

运行: cd skill && .venv314/bin/python -m pytest tests/test_probe_win_cookies.py -q
"""
from __future__ import annotations

import base64
import json
import sqlite3
import sys
import time
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.cookie_harvest as ch  # noqa: E402
import scripts.probe_win_cookies as pw  # noqa: E402

# 报告红线断言用的「敏感值」：fixture 里写进 sqlite，最终断言不出现在报告里
SECRET_PLAIN_VALUE = "SECRET_PLAIN_COOKIE_VALUE_1688"


# ═══════════ 假目录树 fixture ═══════════


def _make_chromium_cookies_db(path: Path, rows: list[tuple]) -> None:
    """形似 Chromium Cookies 库。rows: (host_key, name, enc_prefix|None, plain_value)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
        "encrypted_value BLOB, path TEXT, expires_utc INTEGER, "
        "is_secure INTEGER, is_httponly INTEGER)")
    future_us = int((time.time() + 86400) * 1_000_000) + 11644473600_000000
    for host, name, enc_prefix, plain in rows:
        enc = (enc_prefix.encode() + b"\x01\x02\x03rest") if enc_prefix else b""
        conn.execute(
            "INSERT INTO cookies (host_key, name, value, encrypted_value, path, "
            "expires_utc, is_secure, is_httponly) VALUES (?,?,?,?,?,?,?,?)",
            (host, name, plain, enc, "/", future_us, 1, 1))
    conn.commit()
    conn.close()


def _make_local_state(user_data: Path, dpapi: bool = True,
                      app_bound: bool = False) -> None:
    """Local State：profile.info_cache + os_crypt 密钥（只造前缀形态，非真密钥）。"""
    os_crypt: dict = {}
    if dpapi:
        os_crypt["encrypted_key"] = base64.b64encode(b"DPAPI" + b"\x11" * 16).decode()
    if app_bound:
        os_crypt["app_bound_encrypted_key"] = base64.b64encode(
            b"APPB" + b"\x22" * 32).decode()
    payload: dict = {"profile": {"info_cache": {
        "Default": {"name": "person 1"},
        "Profile 1": {"name": "work"},
    }}}
    if os_crypt:
        payload["os_crypt"] = os_crypt
    user_data.mkdir(parents=True, exist_ok=True)
    (user_data / "Local State").write_text(
        json.dumps(payload), encoding="utf-8")


def _make_fake_tree(tmp_path: Path, v20_in_default: bool = True) -> dict:
    """两个 Chromium profile（Default 混 v10/v20、Profile 1 纯 v10）+ Firefox 树。"""
    user_data = tmp_path / "User Data"
    _make_local_state(user_data)
    # Default：目标域 v10/v20 混合 + 非目标域 + 明文行 + 仿冒域（不得算目标）
    default_rows = [
        ("login.1688.com", "cookie2", "v10", ""),
        (".ozon.ru", "sc_company_id", "v20" if v20_in_default else "v10", ""),
        ("ozon.ru", "__Secure-access_token", "v10", ""),
        ("example.com", "junk", "v10", ""),
        ("not1688.com", "fake", "v10", ""),          # 仿冒后缀，不得算目标
        ("ozon.ru.evil.com", "phish", "v10", ""),    # 仿冒前缀，不得算目标
        (".1688.com", "plain_session", None, SECRET_PLAIN_VALUE),  # 明文行
    ]
    _make_chromium_cookies_db(user_data / "Default" / "Network" / "Cookies",
                              default_rows)
    _make_chromium_cookies_db(user_data / "Profile 1" / "Cookies", [
        ("seller.ozon.ru", "sc_company_id", "v10", ""),
        ("example.com", "junk", "v10", ""),
    ])
    # Firefox：profiles.ini + cookies.sqlite（1 目标 + 1 外域）
    ff_root = tmp_path / "Firefox"
    ff_prof = ff_root / "Profiles" / "abc.default-release"
    ff_prof.mkdir(parents=True)
    conn = sqlite3.connect(str(ff_prof / "cookies.sqlite"))
    conn.execute(
        "CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, expiry INTEGER)")
    future = int(time.time() + 86400)
    conn.execute("INSERT INTO moz_cookies VALUES (?,?,?,?)",
                 (".1688.com", "cookie2", "ffvalue", future))
    conn.execute("INSERT INTO moz_cookies VALUES (?,?,?,?)",
                 ("example.com", "junk", "x", future))
    conn.commit()
    conn.close()
    (ff_root / "profiles.ini").write_text(
        "[Install4F96D1932A9F858E]\nDefault=Profiles/abc.default-release\n",
        encoding="utf-8")
    return {"user_data": user_data, "ff_root": ff_root, "ff_profile": ff_prof}


def _patch_resolvers(monkeypatch, tree: dict, platform: str = "win32") -> None:
    """把探针的路径解析全部指向假树（真代码跑假 FS；不碰真实浏览器目录）。"""
    monkeypatch.setattr(pw, "sys", types.SimpleNamespace(platform=platform))
    monkeypatch.setattr(
        pw, "_resolve_user_data",
        lambda source, platform_name=None:
            tree["user_data"] if source == "chrome" else None)
    monkeypatch.setattr(pw, "_firefox_root", lambda: tree["ff_root"])


# ═══════════ ① 报告结构逐字段断言（含判定矩阵行）═══════════


class TestReportStructure:
    def test_full_report_fields(self, monkeypatch, tmp_path):
        tree = _make_fake_tree(tmp_path)
        _patch_resolvers(monkeypatch, tree)
        out = tmp_path / "report.json"
        rc = pw.main(["--out", str(out)])
        assert rc == 0
        assert out.is_file(), "报告 JSON 落盘"
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["tool"] == "probe_win_cookies"
        assert report["platform"] == "win32"
        assert report["generated_at"]
        # ── 源枚举：chrome ──
        chrome = report["sources"]["chrome"]
        assert chrome["installed"] is True
        assert chrome["user_data_dir"] == str(tree["user_data"])
        assert chrome["crypt"]["dpapi_legacy_key"] is True, "DPAPI 前缀识别"
        assert chrome["crypt"]["app_bound_key"] is False
        dirs = {p["dir"]: p for p in chrome["profiles"]}
        assert set(dirs) == {"Default", "Profile 1"}
        assert dirs["Default"]["display_name"] == "person 1"
        assert dirs["Default"]["cookies_db"] == "Network/Cookies"
        assert dirs["Profile 1"]["cookies_db"] == "Cookies", "旧版布局识别"
        # ── 源枚举：edge/brave 未装 ──
        for src in ("edge", "brave"):
            assert report["sources"][src]["installed"] is False
        # ── 源枚举：firefox（profiles.ini 复用 B1 解析）──
        ff = report["sources"]["firefox"]
        assert ff["installed"] is True
        assert ff["root"] == str(tree["ff_root"])
        assert len(ff["profiles"]) == 1
        assert ff["profiles"][0]["target_cookies"] == 1, "只数目标域"
        # ── 判定矩阵行 ──
        rows = {r["profile"]: r for r in report["matrix"]}
        assert set(rows) == {"Default", "Profile 1"}
        d = rows["Default"]
        assert d["source"] == "chrome"
        assert d["target_rows"] == 4, "3 真目标 + 1 明文目标；仿冒域不数"
        # prefix_counts 为目标+非目标合并分布（加密形态是浏览器级，不按域分化）
        assert d["prefix_counts"]["v10"] == 5, "2 目标 + 3 非目标 v10"
        assert d["prefix_counts"]["v20"] == 1
        assert d["prefix_counts"]["plain"] == 1
        assert d["channel"] == "takeover", "v20 占比 > 0 → 需副本接管通道"
        p1 = rows["Profile 1"]
        assert p1["prefix_counts"]["v20"] == 0
        assert p1["channel"] == "dpapi_v10", "v20 占比 0 → DPAPI 直解通道可用"
        # ── 红线：cookie 值不落报告 ──
        assert SECRET_PLAIN_VALUE not in out.read_text(encoding="utf-8")
        # ── takeover 默认关 ──
        assert report["takeover_test"] is None

    def test_system_guest_profiles_skipped(self, monkeypatch, tmp_path):
        tree = _make_fake_tree(tmp_path)
        for name in ("System Profile", "Guest Profile"):
            (tree["user_data"] / name).mkdir(exist_ok=True)
        _patch_resolvers(monkeypatch, tree)
        report = pw.collect_report()
        dirs = {p["dir"] for p in report["sources"]["chrome"]["profiles"]}
        assert not dirs & {"System Profile", "Guest Profile"}
        skipped = report["sources"]["chrome"]["profiles_skipped"]
        assert set(skipped) == {"System Profile", "Guest Profile"}

    def test_info_cache_filters_component_dirs(self, monkeypatch, tmp_path):
        """实机实证（macOS Chrome User Data 下几十个组件目录）：info_cache 优先，
        非 profile 目录（Crashpad 等）不进清单；未登记但带 Cookies 库的目录补录。"""
        tree = _make_fake_tree(tmp_path)
        for name in ("Crashpad", "OptimizationHints"):
            (tree["user_data"] / name).mkdir(exist_ok=True)
        # 未登记但带 Cookies 库的目录（异常态）→ 补录
        _make_chromium_cookies_db(
            tree["user_data"] / "Profile 2" / "Network" / "Cookies",
            [("login.1688.com", "cookie2", "v10", "")])
        _patch_resolvers(monkeypatch, tree)
        report = pw.collect_report()
        dirs = [p["dir"] for p in report["sources"]["chrome"]["profiles"]]
        assert "Crashpad" not in dirs and "OptimizationHints" not in dirs, \
            "组件目录不是 profile，被 info_cache 过滤"
        assert set(dirs) == {"Default", "Profile 1", "Profile 2"}, "补录未登记带库目录"

    def test_info_cache_registered_system_guest_skipped(self, monkeypatch, tmp_path):
        """现代 Chrome（M114+）会把 System/Guest Profile 也登记进
        profile.info_cache——仍必须跳过，绝不因登记在册当用户 profile 列出。"""
        tree = _make_fake_tree(tmp_path)
        (tree["user_data"] / "Local State").write_text(json.dumps({
            "profile": {"info_cache": {
                "Default": {"name": "person 1"},
                "Profile 1": {"name": "work"},
                "System Profile": {"name": "System Profile"},
                "Guest Profile": {"name": "Guest Profile"},
            }}}), encoding="utf-8")
        for name in ("System Profile", "Guest Profile"):
            (tree["user_data"] / name).mkdir(exist_ok=True)
        _patch_resolvers(monkeypatch, tree)
        report = pw.collect_report()
        dirs = {p["dir"] for p in report["sources"]["chrome"]["profiles"]}
        assert dirs == {"Default", "Profile 1"}, "登记在册的 System/Guest 不进清单"
        assert set(report["sources"]["chrome"]["profiles_skipped"]) == \
            {"System Profile", "Guest Profile"}

    def test_no_sources_installed_exit_zero(self, monkeypatch, tmp_path, capsys):
        """「无源浏览器」是合法结果：exit 0，报告如实记录。"""
        monkeypatch.setattr(pw, "sys", types.SimpleNamespace(platform="win32"))
        monkeypatch.setattr(pw, "_resolve_user_data",
                            lambda source, platform_name=None: None)
        monkeypatch.setattr(pw, "_firefox_root", lambda: None)
        out = tmp_path / "empty.json"
        rc = pw.main(["--out", str(out)])
        assert rc == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        for src in ("chrome", "edge", "brave", "firefox"):
            assert report["sources"][src]["installed"] is False
        assert report["matrix"] == []
        assert "未安装" in capsys.readouterr().out

    def test_macos_run_is_legal(self, monkeypatch, tmp_path):
        """macOS 上运行也合法（本机自测路径）：Firefox 走 darwin 语义探测。"""
        tree = _make_fake_tree(tmp_path)
        _patch_resolvers(monkeypatch, tree, platform="darwin")
        monkeypatch.setattr(pw, "_firefox_profiles",
                            lambda: [tree["ff_profile"]])
        report = pw.collect_report()
        assert report["platform"] == "darwin"
        assert report["sources"]["firefox"]["profiles"][0]["target_cookies"] == 1

    def test_report_written_to_default_data_probe_dir(self, monkeypatch, tmp_path):
        """默认落 data/probe/win_cookies_<ts>.json（目录惰性创建）。"""
        tree = _make_fake_tree(tmp_path)
        _patch_resolvers(monkeypatch, tree)
        data_root = tmp_path / "skilldata"
        monkeypatch.setattr(pw, "DATA_ROOT", data_root)
        path = pw.write_report(pw.collect_report())
        assert path.parent == data_root / "probe"
        assert path.name.startswith("win_cookies_") and path.name.endswith(".json")

    def test_main_exception_exit_one(self, monkeypatch, tmp_path):
        """exit 1 只留给脚本自身异常。"""
        monkeypatch.setattr(pw, "collect_report",
                            mock.Mock(side_effect=RuntimeError("boom")))
        assert pw.main(["--out", str(tmp_path / "x.json")]) == 1


# ═══════════ ② 前缀统计正确性（v20 占比 0 与 >0 两态）═══════════


class TestPrefixStats:
    def test_v20_ratio_zero_and_positive(self, monkeypatch, tmp_path):
        tree_v20 = _make_fake_tree(tmp_path, v20_in_default=True)
        s = pw._sample_prefix_distribution(
            tree_v20["user_data"] / "Default" / "Network" / "Cookies")
        assert s["prefix_counts"]["v20"] == 1
        # 5 行有内容（4 目标非空 + 1 明文目标 + 2 仿冒/外域 v10 —— 全部有 enc）
        # 目标 4 + 非目标 3（example/not1688/evil）= 7 行，全有 encrypted_value
        assert s["v20_ratio"] == pytest.approx(1 / 7)
        assert pw._channel_for(s) == "takeover"

        tree_no = _make_fake_tree(tmp_path / "b", v20_in_default=False)
        s2 = pw._sample_prefix_distribution(
            tree_no["user_data"] / "Default" / "Network" / "Cookies")
        assert s2["prefix_counts"]["v20"] == 0
        assert s2["v20_ratio"] == 0.0
        assert pw._channel_for(s2) == "dpapi_v10"

    def test_channel_undetermined_on_zero_samples(self):
        empty = {"target_rows": 0, "other_rows": 0, "prefix_counts": {
            "v10": 0, "v11": 0, "v20": 0, "plain": 0, "other": 0, "empty": 0},
            "v20_ratio": 0.0}
        assert pw._channel_for(empty) == "undetermined"

    def test_prefix_classification(self):
        assert pw._classify_encrypted(b"v10abcdef", "") == "v10"
        assert pw._classify_encrypted(b"v11abcdef", "") == "v11"
        assert pw._classify_encrypted(b"v20abcdef", "") == "v20"
        assert pw._classify_encrypted(b"", "plainval") == "plain"
        assert pw._classify_encrypted(b"", "") == "empty"
        assert pw._classify_encrypted(b"\x01\x02\x03", "") == "other"

    def test_local_state_crypt_prefixes(self, tmp_path):
        user_data = tmp_path / "ud"
        _make_local_state(user_data, dpapi=True, app_bound=True)
        shape = pw._crypt_shape(pw._read_local_state(user_data))
        assert shape == {"dpapi_legacy_key": True, "app_bound_key": True}
        user_data2 = tmp_path / "ud2"
        _make_local_state(user_data2, dpapi=True, app_bound=False)
        shape2 = pw._crypt_shape(pw._read_local_state(user_data2))
        assert shape2["app_bound_key"] is False, "APPB 缺失 = app-bound 未启用"
        # 损坏 Local State → 不崩、形态未知
        (user_data2 / "Local State").write_bytes(b"\x00\x01broken")
        assert pw._read_local_state(user_data2) == {}

    def test_sample_reads_db_copy_not_source(self, monkeypatch, tmp_path):
        """只读红线：抽样经临时目录副本，源库 mtime/内容不被改动。"""
        tree = _make_fake_tree(tmp_path)
        db = tree["user_data"] / "Default" / "Network" / "Cookies"
        before = db.read_bytes()
        pw._sample_prefix_distribution(db)
        assert db.read_bytes() == before

    def test_sampling_cap(self, monkeypatch, tmp_path):
        """抽样上限：目标域 ≤200 行 + 非目标域 ≤50 行。"""
        db = tmp_path / "Cookies"
        rows = [("login.1688.com", f"c{i}", "v10", "") for i in range(250)]
        rows += [(f"site{i}.example.com", "x", "v10", "") for i in range(80)]
        _make_chromium_cookies_db(db, rows)
        s = pw._sample_prefix_distribution(db)
        assert s["target_rows"] == 200
        assert s["other_rows"] == 50

    def test_corrupt_cookies_db_error_line_not_crash(self, tmp_path):
        """损坏库：逐项独立失败——error 行返回、不 raise；sqlite 句柄任何路径
        都关闭（否则 Windows 文件锁下 TemporaryDirectory 清理 PermissionError
        会顶掉 error 行并把探针整体打成 exit 1）。"""
        db = tmp_path / "Cookies"
        db.write_bytes(b"this is not a sqlite database at all" * 20)
        s = pw._sample_prefix_distribution(db)
        assert s["error"], "损坏库记 error 行"
        assert s["target_rows"] == 0

    def test_wal_pending_rows_recovered(self, tmp_path):
        """主场景（浏览器运行中、WAL 未 checkpoint）：拷 db+wal(-shm) 后，
        wal 里未 checkpoint 的行必须能回放读出（ro 读 WAL 副本报
        SQLITE_READONLY 时降级非 ro——B1 cookie_harvest 先例）。"""
        db = tmp_path / "Cookies"
        writer = sqlite3.connect(str(db))
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
            "encrypted_value BLOB, path TEXT, expires_utc INTEGER, "
            "is_secure INTEGER, is_httponly INTEGER)")
        writer.execute("INSERT INTO cookies (host_key) VALUES (?)",
                       ("login.1688.com",))
        writer.commit()
        # writer 保持打开（wal 未 checkpoint），探针此时拷库读取
        s = pw._sample_prefix_distribution(db)
        writer.close()
        assert s["error"] is None
        assert s["target_rows"] == 1, "wal 中未 checkpoint 的行也要被统计到"

    def test_firefox_corrupt_db_error_recorded(self, tmp_path):
        """Firefox 损坏库同语义：error 行 + target_cookies=None，不 raise。"""
        prof = tmp_path / "prof"
        prof.mkdir()
        (prof / "cookies.sqlite").write_bytes(b"garbage" * 20)
        stats = pw._firefox_profile_stats(prof)
        assert stats["target_cookies"] is None
        assert stats["error"]


# ═══════════ ③ 目标域过滤正确 ═══════════


class TestTargetDomainFilter:
    def test_sql_target_filter_matches_b1_semantics(self):
        """SQL 目标域谓词与 cookie_harvest._domain_matches 同语义（后缀匹配）。"""
        where = pw._target_host_sql()
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE cookies (host_key TEXT)")
        hosts = ["1688.com", ".1688.com", "login.1688.com", "ozon.ru",
                 ".ozon.ru", "seller.ozon.ru", "ozone.ru",
                 "not1688.com", "1688.com.evil.com", "ozon.ru.evil.com",
                 "example.com"]
        conn.executemany("INSERT INTO cookies VALUES (?)",
                         [(h,) for h in hosts])
        hits = {r[0] for r in conn.execute(
            f"SELECT host_key FROM cookies WHERE {where}")}
        conn.close()
        expected = {h for h in hosts if ch._domain_matches(h)}
        assert hits == expected
        assert "not1688.com" not in hits
        assert "ozon.ru.evil.com" not in hits

    def test_firefox_target_count_uses_b1_matcher(self, tmp_path):
        tree = _make_fake_tree(tmp_path)
        stats = pw._firefox_profile_stats(tree["ff_profile"])
        assert stats["target_cookies"] == 1
        assert stats["target_cookies_unexpired"] == 1
        assert not stats.get("error")


# ═══════════ ④ takeover 分支 mock（不真启浏览器）═══════════


class _FakeProc:
    def __init__(self):
        self.pid = 4242
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class TestTakeoverBranch:
    def _patch_launch_chain(self, monkeypatch, cookies, cdp_up=True):
        proc = _FakeProc()
        launched: dict = {}

        def fake_popen(cmd):
            launched["cmd"] = cmd
            return proc

        monkeypatch.setattr(pw, "_popen_browser", fake_popen)
        monkeypatch.setattr(pw, "_wait_cdp", lambda port, timeout_s=30: cdp_up)
        monkeypatch.setattr(pw, "_cdp_all_cookies",
                            lambda port: cookies)
        return proc, launched

    def test_takeover_success_minimal_set_sufficient(self, monkeypatch, tmp_path):
        _make_local_state(tmp_path / "ud")
        _make_chromium_cookies_db(
            tmp_path / "ud" / "Default" / "Network" / "Cookies",
            [("login.1688.com", "cookie2", "v10", "")])
        candidate = {"source": "chrome", "profile": "Default",
                     "cookies_db": "Network/Cookies",
                     "target_rows": 4, "user_data_dir": str(tmp_path / "ud")}
        proc, launched = self._patch_launch_chain(monkeypatch, [
            {"domain": ".1688.com", "name": "cookie2", "value": "DECRYPTED_SECRET"},
            {"domain": "seller.ozon.ru", "name": "sc_company_id", "value": "S2"},
            {"domain": "example.com", "name": "junk", "value": "j"},
        ])
        base = tmp_path / "tmpdata"
        r = pw.run_takeover_test(candidate, base_dir=base)
        assert r["cdp_up"] is True
        assert r["target_cookies"] == 2, "只数目标域明文 cookie"
        assert r["all_cookies"] == 3
        assert r["minimal_set_sufficient"] is True
        assert r["headless"] is True
        assert r["error"] is None
        # 启动参数：临时 user-data-dir + 动态端口避开 9222 + --no-first-run
        cmd = launched["cmd"]
        udd = [a for a in cmd if a.startswith("--user-data-dir=")][0]
        workdir = Path(udd.split("=", 1)[1])
        assert base in workdir.parents or workdir.parent == base
        port = int([a for a in cmd
                    if a.startswith("--remote-debugging-port=")][0].split("=")[1])
        assert port != 9222, "动态端口避开主实例 9222"
        assert "--no-first-run" in cmd
        assert any(a.startswith("--headless") for a in cmd)
        assert any(a == "--profile-directory=Default" for a in cmd)
        # 最小复制集进了临时目录
        assert (workdir / "Local State").is_file() or not workdir.exists()
        # finally 兜底：进程关 + 临时目录删
        assert proc.terminated
        assert not workdir.exists(), "临时目录清理（finally 兜底）"
        assert r["cleaned"] is True
        assert "DECRYPTED_SECRET" not in json.dumps(r), "明文 cookie 值不落报告"

    def test_takeover_cdp_down_still_cleans(self, monkeypatch, tmp_path):
        _make_local_state(tmp_path / "ud")
        _make_chromium_cookies_db(
            tmp_path / "ud" / "Default" / "Network" / "Cookies",
            [("login.1688.com", "cookie2", "v10", "")])
        candidate = {"source": "chrome", "profile": "Default",
                     "cookies_db": "Network/Cookies",
                     "target_rows": 4, "user_data_dir": str(tmp_path / "ud")}
        cdp_called = mock.Mock()
        monkeypatch.setattr(pw, "_cdp_all_cookies", cdp_called)
        proc, launched = self._patch_launch_chain(monkeypatch, [],
                                                  cdp_up=False)
        base = tmp_path / "tmpdata"
        r = pw.run_takeover_test(candidate, base_dir=base)
        assert r["cdp_up"] is False
        assert r["minimal_set_sufficient"] is False
        assert not cdp_called.called, "CDP 未起不读 cookie"
        assert proc.terminated
        assert r["cleaned"] is True
        udd = [a for a in launched["cmd"]
               if a.startswith("--user-data-dir=")][0]
        assert not Path(udd.split("=", 1)[1]).exists(), \
            "从启动 argv 解析的真实 workdir 已被 finally 清理"

    def test_takeover_no_browser_executable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pw.chrome_launcher, "_find_chrome_executable",
                            lambda: None)
        popen_calls = mock.Mock()
        monkeypatch.setattr(pw, "_popen_browser", popen_calls)
        r = pw.run_takeover_test(
            {"source": "chrome", "profile": "Default", "cookies_db": "Cookies",
             "target_rows": 1, "user_data_dir": str(tmp_path)},
            base_dir=tmp_path / "t")
        assert r["error"], "无浏览器 → 报告一行结论"
        assert not popen_calls.called
        assert r["cdp_up"] is False

    def test_takeover_launch_exception_recorded_and_cleaned(self, monkeypatch,
                                                            tmp_path):
        _make_local_state(tmp_path)  # Local State 就位，让启动阶段（Popen）抛错
        _make_chromium_cookies_db(tmp_path / "Default" / "Network" / "Cookies",
                                  [("login.1688.com", "cookie2", "v10", "")])

        def boom(cmd):
            raise OSError("spawn failed")
        monkeypatch.setattr(pw, "_popen_browser", boom)
        r = pw.run_takeover_test(
            {"source": "chrome", "profile": "Default",
             "cookies_db": "Network/Cookies", "target_rows": 1,
             "user_data_dir": str(tmp_path)},
            base_dir=tmp_path / "t")
        assert r["error"] and "spawn failed" in r["error"]
        assert r["cleaned"] is True, "异常路径同样清理临时目录"

    def test_takeover_via_collect_report(self, monkeypatch, tmp_path):
        """collect_report(takeover_test=True) 挑目标域 cookie 最多的 profile 做试验。"""
        tree = _make_fake_tree(tmp_path)
        _patch_resolvers(monkeypatch, tree)
        picked: dict = {}

        def fake_takeover(candidate, base_dir=None, wait_timeout_s=30):
            picked["candidate"] = candidate
            return {"cdp_up": False, "minimal_set_sufficient": False,
                    "cleaned": True, "error": "mock"}

        monkeypatch.setattr(pw, "run_takeover_test", fake_takeover)
        report = pw.collect_report(takeover_test=True)
        assert report["takeover_test"]["error"] == "mock"
        assert picked["candidate"]["profile"] == "Default", \
            "Default 目标行 4 > Profile 1 目标行 1"
        assert picked["candidate"]["target_rows"] == 4

    def test_takeover_skipped_without_candidates(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pw, "sys", types.SimpleNamespace(platform="win32"))
        monkeypatch.setattr(pw, "_resolve_user_data",
                            lambda source, platform_name=None: None)
        monkeypatch.setattr(pw, "_firefox_root", lambda: None)
        report = pw.collect_report(takeover_test=True)
        assert "skipped" in report["takeover_test"]

    def test_dynamic_port_avoids_9222(self, monkeypatch):
        ports = {pw._pick_dynamic_port() for _ in range(20)}
        assert 9222 not in ports
        assert all(isinstance(p, int) and 1024 < p < 65536 for p in ports)


# ═══════════ ⑤ cli 子命令 wrapper 冒烟（mock 主函数）═══════════


class TestCliWiring:
    def _run(self, argv):
        from scripts.cli import build_arg_parser, cmd_probe_win_cookies
        args = build_arg_parser().parse_args(argv)
        return cmd_probe_win_cookies(args)

    def test_wrapper_calls_run_cli_with_flags(self, monkeypatch):
        called: dict = {}
        monkeypatch.setattr(
            pw, "run_cli",
            lambda takeover_test=False, out=None: called.update(
                {"takeover_test": takeover_test, "out": out}) or 0)
        rc = self._run(["probe-win-cookies", "--takeover-test",
                        "--out", "/tmp/x.json"])
        assert rc == 0
        assert called == {"takeover_test": True, "out": "/tmp/x.json"}

    def test_wrapper_defaults(self, monkeypatch):
        called: dict = {}
        monkeypatch.setattr(
            pw, "run_cli",
            lambda takeover_test=False, out=None: called.update(
                {"takeover_test": takeover_test, "out": out}) or 0)
        rc = self._run(["probe-win-cookies"])
        assert rc == 0
        assert called == {"takeover_test": False, "out": None}

    def test_wrapper_propagates_failure_exit_code(self, monkeypatch):
        monkeypatch.setattr(pw, "run_cli", lambda takeover_test=False, out=None: 1)
        assert self._run(["probe-win-cookies"]) == 1

    def test_registered_in_arg_parser(self):
        from scripts.cli import build_arg_parser
        parser = build_arg_parser()
        opts = vars(parser.parse_args(["probe-win-cookies"]))
        assert opts["command"] == "probe-win-cookies"
        assert "takeover_test" in opts and "out" in opts
        assert "probe-win-cookies" in parser.format_help()
