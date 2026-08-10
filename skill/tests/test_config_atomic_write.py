#!/usr/bin/env python3
"""Q15: config_store 三处 JSON 写原子化（tmp + os.replace）回归测试。

背景：v0.14 E3 只修了 cache.py，config_store 的 stores.json / settings.json /
auth_cache.json 仍用 write_text 直接覆写——并发 CLI 进程（check/set_token/
set_store 同开）可能让读者读到半截 JSON → 凭证丢失/解析失败。

修复：新增 _atomic_write_json（临时文件 + os.replace，Windows 文件锁重试，
与 cache.py E3 同模式），三个 _save_* 全部改走它。

运行：
    cd skill && .venv314/bin/python tests/test_config_store_atomic.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.config_store as cs  # noqa: E402


def _redirect_files() -> Path:
    """把模块级文件路径重定向到临时目录，避免污染真实 skill/data/config/。"""
    tmp = Path(tempfile.mkdtemp(prefix="config_store_test_"))
    cs.STORES_FILE = tmp / "stores.json"
    cs.SETTINGS_FILE = tmp / "settings.json"
    cs.AUTH_CACHE_FILE = tmp / "auth_cache.json"
    return tmp


def test_save_stores_uses_os_replace():
    """stores.json 写入必须走 os.replace（原子替换），而非 write_text 直写。"""
    _redirect_files()
    with mock.patch("scripts.lib.config_store.os.replace") as rep:
        cs._save_stores_file({"default": "s1", "stores": {"s1": {"client_id": "c", "api_key": "k"}}})
    rep.assert_called_once()


def test_save_settings_uses_os_replace():
    """settings.json 写入必须走 os.replace。"""
    _redirect_files()
    with mock.patch("scripts.lib.config_store.os.replace") as rep:
        cs._save_settings_file({"mxou_token": "sk-test"})
    rep.assert_called_once()


def test_save_auth_cache_uses_os_replace():
    """auth_cache.json 写入必须走 os.replace。"""
    _redirect_files()
    with mock.patch("scripts.lib.config_store.os.replace") as rep, \
         mock.patch("scripts.lib.config_store._time.time", return_value=1000.0):
        cs._save_auth_cache("sk-token", 86400)
    rep.assert_called_once()


def test_no_tmp_leftover_after_saves():
    """全部保存后不残留 *.tmp 临时文件。"""
    tmp = _redirect_files()
    cs.set_store("s1", "cid", "key", currency="RUB")
    cs.set_setting("mxou_token", "sk-x")
    cs._save_auth_cache("sk-x", 3600)
    assert list(tmp.glob("*.tmp")) == []


def test_os_replace_retry_on_windows_lock():
    """os.replace 遇 Windows 文件锁失败 → 重试一次成功，内容完整。"""
    _redirect_files()
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("Windows file lock")
        return real_replace(src, dst)

    with mock.patch("scripts.lib.config_store.os.replace", side_effect=flaky_replace):
        cs._save_settings_file({"a": 1})
    assert calls["n"] == 2
    assert json.loads(cs.SETTINGS_FILE.read_text(encoding="utf-8")) == {"a": 1}


def test_concurrent_reads_never_corrupt_json():
    """并发写期间读者永不见半截 JSON（原子写核心性质）。"""
    tmp = _redirect_files()
    stop = threading.Event()
    errors: list[Exception] = []
    big_payload = {"data": list(range(2000)), "suffix": "x" * 500}

    def reader() -> None:
        while not stop.is_set():
            p = cs.SETTINGS_FILE
            if p.exists():
                try:
                    json.loads(p.read_text(encoding="utf-8"))
                except Exception as e:  # 读到半截 JSON = 原子性被破坏
                    errors.append(e)
                    return
            time.sleep(0.0005)

    t = threading.Thread(target=reader)
    t.start()
    for i in range(200):
        cs._atomic_write_json(cs.SETTINGS_FILE, {"key": i, "payload": big_payload})
    stop.set()
    t.join(timeout=5)
    assert not errors
    assert list(tmp.glob("*.tmp")) == []


def test_settings_roundtrip():
    _redirect_files()
    cs.set_setting("mxou_token", "sk-abc")
    assert cs.get_setting("mxou_token") == "sk-abc"


def test_stores_roundtrip():
    _redirect_files()
    cs.set_store("shop1", "cid", "key", currency="CNY")
    assert cs.get_store("shop1")["client_id"] == "cid"
    assert cs.remove_store("shop1") is True


def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    total = len(fns)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
