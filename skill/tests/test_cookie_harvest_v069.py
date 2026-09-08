# -*- coding: utf-8 -*-
"""v0.69 跨浏览器 cookie 导入回归（cookie_harvest）。

锁定：
1. 目标域/过期/session 过滤（_cookie_dict）。
2. Chromium 解密链：PBKDF2 派生 + openssl 子进程 AES-128-CBC 往返（零新依赖）。
3. Cookies sqlite 读取（临时目录拷贝 + 解密 + 过滤）。
4. Safari binarycookies 合成样本解析。
5. Storage.setCookies 注入（假 CDP，分块）。
6. readiness 自动兜底：冷却落盘、kill-switch、成功修复语义。

运行: cd skill && .venv314/bin/python -m pytest tests/test_cookie_harvest_v069.py -q
"""
from __future__ import annotations

import sqlite3
import struct
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.cookie_harvest as ch  # noqa: E402


# ═══════════ 1. 过滤 ═══════════


def test_cookie_dict_accepts_target_domains():
    for host in ("1688.com", ".1688.com", "www.1688.com", "seller.ozon.ru",
                 "ozon.ru", "ozone.ru"):
        c = ch._cookie_dict(host, "cookie2", "v", "/",
                            time.time() + 3600, True, True)
        assert c is not None, host
        assert c["domain"].startswith("."), "注入域名统一带前导点"
    assert ch._cookie_dict("taobao.com", "cookie2", "v", "/",
                           time.time() + 3600, False, False) is None, "邻域不搬"
    assert ch._cookie_dict("example.com", "a", "v", "/",
                           time.time() + 3600, False, False) is None


def test_cookie_dict_rejects_expired_and_session():
    assert ch._cookie_dict("1688.com", "a", "v", "/",
                           time.time() - 10, False, False) is None, "已过期"
    assert ch._cookie_dict("1688.com", "a", "v", "/",
                           0, False, False) is None, "session cookie 不搬"
    assert ch._cookie_dict("1688.com", "", "v", "/",
                           time.time() + 3600, False, False) is None, "空名"
    assert ch._cookie_dict("1688.com", "a", "", "/",
                           time.time() + 3600, False, False) is None, "空值"


# ═══════════ 2. Chromium 解密链 ═══════════


def test_pbkdf2_derivation_shape():
    key = ch.hashlib_pbkdf2_sha1(b"peanuts")
    assert len(key) == 16
    assert key == ch.hashlib_pbkdf2_sha1(b"peanuts"), "确定性"
    assert key != ch.hashlib_pbkdf2_sha1(b"other")


def test_openssl_roundtrip_v10():
    """v10 前缀 + AES-128-CBC 加密 → openssl 解密还原（真实子进程路径）。"""
    key = ch.hashlib_pbkdf2_sha1(b"test-key")
    iv = b" " * 16
    plaintext = b"sc_company_id_value_123"
    proc = subprocess.run(
        ["openssl", "enc", "-aes-128-cbc", "-K", key.hex(), "-iv", iv.hex()],
        input=plaintext, capture_output=True, timeout=15,
    )
    assert proc.returncode == 0, "本机 openssl 需支持 -K/-iv"
    encrypted = b"v10" + proc.stdout
    assert ch._decrypt_chromium_value(encrypted, key) == plaintext


def test_decrypt_plaintext_fallback_and_failure():
    assert ch._decrypt_chromium_value(b"", b"k") == b""
    assert ch._decrypt_chromium_value(b"plain", b"k") == b"plain", "无 v10 前缀=旧明文"
    with pytest.raises(ValueError):
        ch._decrypt_chromium_value(b"v10" + b"\x00" * 32, b"wrong-key-123456")


def test_chrome_timestamp_conversion():
    # 2026-01-01 00:00:00 UTC ≈ Chrome µs 133490000000000000 量级；校验换算公式
    unix = 1767225600.0  # 2026-01-01
    chrome_us = (unix + 11644473600) * 1_000_000
    assert ch._chrome_ts_to_unix(int(chrome_us)) == unix
    assert ch._chrome_ts_to_unix(0) == 0


# ═══════════ 3. Cookies sqlite 读取 ═══════════


def _openssl_encrypt(key: bytes, plaintext: bytes) -> bytes:
    proc = subprocess.run(
        ["openssl", "enc", "-aes-128-cbc", "-K", key.hex(),
         "-iv", (b" " * 16).hex()],
        input=plaintext, capture_output=True, timeout=15)
    assert proc.returncode == 0
    return b"v10" + proc.stdout


def _make_chromium_db(path: Path, key: bytes) -> None:
    """建一个形似 Chrome Cookies 库（Chrome 130+ 明文=SHA256(host)+value）：
    1 条有效 1688 加密（带域哈希）+ 1 条过期 + 1 条外域 + 1 条旧版无哈希。"""
    import hashlib
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB, "
        "value TEXT, path TEXT, expires_utc INTEGER, is_secure INTEGER, "
        "is_httponly INTEGER)")
    future_us = int((time.time() + 86400 + 11644473600) * 1_000_000)
    past_us = int((time.time() - 3600 + 11644473600) * 1_000_000)
    # Chrome 130+：明文 = sha256(host_key) + value
    payload = hashlib.sha256(b".1688.com").digest() + b"goodvalue"
    enc = _openssl_encrypt(key, payload)
    conn.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?)",
                 (".1688.com", "cookie2", enc, "", "/", future_us, 1, 1))
    # 旧版：明文 = value（无哈希）
    enc_legacy = _openssl_encrypt(key, b"legacyval")
    conn.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?)",
                 (".1688.com", "legacy", enc_legacy, "", "/", future_us, 0, 0))
    conn.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?)",
                 (".1688.com", "stale", b"", "", "/", past_us, 0, 0))
    conn.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?)",
                 ("example.com", "junk", b"", "x", "/", future_us, 0, 0))
    conn.commit()
    conn.close()


def test_read_chromium_db_filters_and_decrypts(tmp_path):
    key = ch.hashlib_pbkdf2_sha1(b"pw")
    db = tmp_path / "Cookies"
    _make_chromium_db(db, key)
    cookies = ch._read_chromium_db(db, key)
    by_name = {c["name"]: c for c in cookies}
    assert set(by_name) == {"cookie2", "legacy"}, "只留两条有效 1688 加密条目"
    assert by_name["cookie2"]["value"] == "goodvalue", "域哈希剥离（Chrome 130+）"
    assert by_name["legacy"]["value"] == "legacyval", "旧版无哈希明文"
    assert by_name["cookie2"]["httpOnly"] is True


# ═══════════ 4. Safari binarycookies 合成样本 ═══════════


def _make_binarycookies(path: Path) -> None:
    """合成一页两 cookie：1 条有效 1688 + 1 条已过期。"""

    def cookie_blob(name: bytes, value: bytes, expiry: float, flags: int) -> bytearray:
        blob = bytearray(160)
        struct.pack_into("<I", blob, 0, 160)  # size
        struct.pack_into("<6I", blob, 4,
                         flags, 0, 96, 112, 128, 140)  # flags,unk,url,name,path,value
        struct.pack_into("<2d", blob, 32, expiry, time.time())
        blob[96:96 + 10] = b".1688.com\x00"
        blob[112:112 + len(name)] = name
        blob[128:128 + 2] = b"/\x00"
        blob[140:140 + len(value)] = value
        return blob

    live = cookie_blob(b"cookie2\x00", b"val\x00", time.time() + 3600, 0x1)
    dead = cookie_blob(b"old\x00", b"x\x00", time.time() - 100, 0)
    # 页布局：header(8) + 偏移数组(4*2=8) → 两个 cookie 从 16 开始
    page = struct.pack("<II", 0x00000100, 2) + struct.pack("<II", 16, 16 + 160)
    page += bytes(live) + bytes(dead)
    path.write_bytes(b"cook" + struct.pack(">I", 1) + struct.pack("<I", len(page)) + page)


def test_read_binarycookies_synthetic(tmp_path):
    p = tmp_path / "Cookies.binarycookies"
    _make_binarycookies(p)
    cookies = ch._read_binarycookies(p)
    assert len(cookies) == 1, "过期条目被过滤"
    assert cookies[0]["name"] == "cookie2"
    assert cookies[0]["value"] == "val"
    assert cookies[0]["secure"] is True
    assert cookies[0]["domain"] == ".1688.com"


def test_harvest_safari_missing_file():
    with mock.patch.object(ch, "SAFARI_COOKIES", "/nonexistent/x.binarycookies"):
        r = ch._harvest_safari()
    assert r["status"] == "not_installed"


# ═══════════ 5. 注入（假 CDP）═══════════


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

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def new_tab(self, url="about:blank", background=False):
        return self.tab

    def close(self):
        pass


def test_inject_cookies_chunks_and_payload(monkeypatch):
    monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", _FakeConn)
    cookies = [ch._cookie_dict("1688.com", f"c{i}", "v", "/",
                               time.time() + 3600, False, False)
               for i in range(120)]
    result = ch.inject_cookies(cookies)
    assert result["ok"] is True and result["count"] == 120
    tab = _FakeConn.last_tab
    assert len(tab.calls) == 3, "120 条按 50 分块 = 3 次"
    method, params = tab.calls[0]
    assert method == "Storage.setCookies"
    assert len(params["cookies"]) == 50
    assert params["cookies"][0]["domain"] == ".1688.com"
    assert "expires" in params["cookies"][0]


def test_inject_cookies_empty():
    assert ch.inject_cookies([])["ok"] is False


# ═══════════ 6. 编排 + readiness 自动兜底 ═══════════


def test_harvest_all_platform_guard(monkeypatch):
    monkeypatch.setattr(ch, "sys", types.SimpleNamespace(platform="win32"))
    report = ch.harvest_all()
    assert report["platform"] == "unsupported"
    assert report["total"] == 0


def test_harvest_all_unknown_sources_skipped():
    report = ch.harvest_all(sources=["nosuch"])
    assert report["sources"] == {} and report["total"] == 0


def test_try_auto_import_cooldown(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(ch, "auto_fallback_allowed", lambda probe: False)
    called = mock.Mock()
    monkeypatch.setattr(ch, "harvest_and_import", called)
    assert ch.try_auto_import("seller_login") is False
    assert not called.called, "冷却中不得触发扫描"


def test_try_auto_import_kill_switch(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("SKILL_DISABLE_COOKIE_HARVEST", "1")
    called = mock.Mock()
    monkeypatch.setattr(ch, "harvest_and_import", called)
    assert ch.try_auto_import("seller_login") is False
    assert not called.called


def test_try_auto_import_success_marks_cooldown(monkeypatch):
    calls = {}
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # 摘守卫（扫描已 mock）
    monkeypatch.setattr(sys, "platform", "darwin")  # try_auto_import 仅 macOS（CI Linux 实证）
    monkeypatch.setattr(ch, "auto_fallback_allowed", lambda probe: True)
    monkeypatch.setattr(ch, "mark_auto_fallback_attempted",
                        lambda probe: calls.setdefault("marked", probe))
    monkeypatch.setattr(ch, "harvest_and_import",
                        lambda cdp_url, sources=None: {"verified": {"seller": True}})
    assert ch.try_auto_import("seller_login") is True
    assert calls["marked"] == "seller_login", "先记冷却防授权框循环"


def test_try_auto_import_failure_returns_false(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")  # 走到 verified=False 分支而非平台守卫
    monkeypatch.setattr(ch, "auto_fallback_allowed", lambda probe: True)
    monkeypatch.setattr(ch, "mark_auto_fallback_attempted", lambda probe: None)
    monkeypatch.setattr(ch, "harvest_and_import",
                        lambda cdp_url, sources=None: {"verified": {"seller": False}})
    assert ch.try_auto_import("seller_login") is False


def test_try_auto_import_inert_under_pytest(monkeypatch):
    """pytest 下兜底恒 False 且不触扫描（防真实注入翻转探针断言，全量回归实证）。"""
    called = mock.Mock()
    monkeypatch.setattr(ch, "harvest_and_import", called)
    monkeypatch.delenv("SKILL_DISABLE_COOKIE_HARVEST", raising=False)
    assert ch.try_auto_import("seller_login") is False
    assert not called.called


def test_readiness_fallback_repairs_login_probe(monkeypatch):
    """readiness：alibaba_login 探针失败 → cookie 导入成功 → ok + repaired。"""
    import scripts.lib.readiness as rd
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(rd, "_under_pytest", lambda: False)
    monkeypatch.setattr(rd, "probe_chrome_cdp", lambda **kw: True)
    monkeypatch.setattr(rd, "_cached_ok", lambda probe: False)
    monkeypatch.setitem(rd._PROBES, "alibaba_login", lambda url: False)
    monkeypatch.setattr("scripts.lib.cookie_harvest.try_auto_import",
                        lambda probe, url: True)
    report = rd.ensure_pipeline_ready("graph", use_cache=False, interactive=False)
    assert report["results"]["alibaba_login"] is True
    assert any("导入登录 cookie" in r for r in report["repaired"])
