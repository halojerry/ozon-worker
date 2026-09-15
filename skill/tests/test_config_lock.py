#!/usr/bin/env python3
"""T4 settings/stores RMW 跨进程锁 + write_ak_store_file 原子写回归测试
（fix/skill-concurrency-v1 批1，竞态 #3）。

背景（丢失更新）：`config_store` 的 set_setting/remove_setting/set_store/
remove_store/set_default_store 全是无锁 读→改→写回——并发 CLI 进程各自持整份
JSON 改完写回，后写者**整文件覆盖**先写者（aibuy token / 1688 ak /
mxou_token 互相抹的实证）。`write_ak_store_file` 还是非原子 write_text。

修复契约（本文件锁定，方案 §A3 T4）：
  1. 5 个 RMW 函数的读-改-写全段由 `lock_utils.file_lock`（A1 交付）包住，
     锁文件 `data/locks/settings.lock`（stores.json 与 settings.json 共用一把
     ——都是毫秒级临界区，不值得分锁）；
  2. 超时策略 fail-open：锁等待 timeout=10s，超时/目录不可建 → 告警日志后
     **照常写**（_atomic_write_json 保证不产生半截文件，极端争用下退化为
     现状丢失更新，不新增故障面）；
  3. get_setting/get_store 等纯读**不加锁**（读原子替换文件安全）；
  4. write_ak_store_file 改原子写（逐文件 tmp + os.replace，Windows 锁重试
     一次，抄 _atomic_write_json 模式）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_config_lock.py -q
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.config_store as cs  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """settings/stores/锁路径全部重定向到 tmp（不碰真实 skill/data）。"""
    monkeypatch.setattr(cs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(cs, "STORES_FILE", tmp_path / "stores.json")
    monkeypatch.setattr(cs, "SETTINGS_LOCK_PATH", tmp_path / "locks" / "settings.lock")
    return tmp_path


# 多进程 worker 脚本（subprocess 真进程——锁是跨进程文件锁，线程测不出丢失更新；
# 不用 multiprocessing.Process 是因为 pytest 下 spawn 对测试模块函数的 pickle
# 在 CI 上有重导入脆性，subprocess -c 是本仓库已验证的形态，见 test_heavy_gate）
_MP_CHILD = """
import sys
sys.path.insert(0, {root!r})
from pathlib import Path
import scripts.lib.config_store as cs
cs.SETTINGS_FILE = Path({settings!r})
cs.SETTINGS_LOCK_PATH = Path({lock!r})
for i in range({rounds}):
    cs.set_setting({key!r}, {{"worker": {key!r}, "i": i}})
print("done-{key}")
"""


class TestRmwLock:
    def test_set_setting_acquires_settings_lock(self, isolated, monkeypatch):
        """RMW 必须经 settings.lock（路径 + 10s 超时语义）。"""
        calls = []
        real = cs.lock_utils.try_acquire

        def spy(path, timeout=30.0):
            calls.append((path, timeout))
            return real(path, timeout=timeout)

        monkeypatch.setattr(cs.lock_utils, "try_acquire", spy)
        cs.set_setting("mxou_token", "sk-x")
        assert calls and calls[0][0] == isolated / "locks" / "settings.lock"
        assert calls[0][1] == 10.0, "锁等待超时必须是 10s（fail-open 前的最后等待）"

    def test_set_setting_lock_contention_still_writes_serialized(self, isolated):
        """持锁方在场 → 第二个 RMW 等待其释放后执行，两笔更新都不丢。"""
        cs.SETTINGS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)  # 直接纳锁方自备目录
        holder = cs.lock_utils.try_acquire(cs.SETTINGS_LOCK_PATH, timeout=0.0)
        assert holder is not None
        result = {}

        def blocked_write():
            result["ok"] = (cs.set_setting("k2", "v2"), True)

        import threading
        t = threading.Thread(target=blocked_write)
        t.start()
        time.sleep(0.3)  # 让 blocked_write 进入等待
        cs.set_setting("k1", "v1")  # 主线程持锁期间自己写（验证等待者没抢走锁）
        cs.lock_utils.release(holder)
        t.join(timeout=15)
        assert result.get("ok"), "等待者必须在持锁方释放后完成"
        assert cs.get_setting("k1") == "v1" and cs.get_setting("k2") == "v2", \
            "两笔更新都必须落盘（丢失更新回归）"

    def test_fail_open_when_lock_never_available(self, isolated, monkeypatch, caplog):
        """锁永远拿不到（等待 10s 超时）→ 告警后照常写（fail-open，不新增故障面）。"""
        monkeypatch.setattr(cs.lock_utils, "try_acquire", lambda p, timeout=30.0: None)
        with caplog.at_level(logging.WARNING, logger="scripts.lib.config_store"):
            cs.set_setting("mxou_token", "sk-degraded")
        assert cs.get_setting("mxou_token") == "sk-degraded", "fail-open 必须照常写"
        assert any("降级" in r.message for r in caplog.records), "必须有告警日志"

    def test_pure_reads_do_not_take_lock(self, isolated, monkeypatch):
        """get_setting / get_store 纯读不加锁（读原子替换文件安全）。"""

        def forbidden(*a, **k):
            raise AssertionError("纯读不得碰锁")

        monkeypatch.setattr(cs.lock_utils, "try_acquire", forbidden)
        cs.SETTINGS_FILE.write_text(json.dumps({"a": 1}), encoding="utf-8")
        cs.STORES_FILE.write_text(
            json.dumps({"default": "s", "stores": {"s": {"client_id": "c", "api_key": "k"}}}),
            encoding="utf-8")
        assert cs.get_setting("a") == 1
        assert cs.get_store("s")["client_id"] == "c"

    def test_store_rmw_roundtrip_under_lock(self, isolated):
        """set_store / set_default_store / remove_store 行为不变（只是整段入锁）。"""
        cs.set_store("shop1", "cid", "key", currency="CNY")
        cs.set_store("shop2", "cid2", "key2")
        assert cs.set_default_store("shop2") is True
        assert cs.get_store()["client_id"] == "cid2"
        assert cs.remove_store("shop2") is True
        assert cs.get_store()["client_id"] == "cid", "删除默认店后回退剩余店铺"
        data = json.loads(cs.STORES_FILE.read_text(encoding="utf-8"))
        assert data["default"] == "shop1"
        assert cs.remove_setting("nope") is False
        cs.set_setting("tmp", 1)
        assert cs.remove_setting("tmp") is True

    def test_multiprocess_set_setting_no_lost_update(self, tmp_path):
        """≥6 个真进程并发 set_setting 不同 key 各 N 次 → 全部 key 存在且值正确。

        丢失更新回归核心用例：无锁时后写者整文件覆盖先写者（并行窗口内高概率
        丢 key）；有锁后每个 RMW 读-改-写原子，任何进程的 key 都不会被整份抹掉。
        """
        settings = tmp_path / "settings.json"
        lock = tmp_path / "locks" / "settings.lock"
        root = str(Path(__file__).resolve().parent.parent)
        n_proc, rounds = 6, 25
        procs = []
        for k in range(n_proc):
            script = _MP_CHILD.format(
                root=root, settings=str(settings), lock=str(lock),
                key=f"key_{k}", rounds=rounds)
            procs.append(subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for idx, p in enumerate(procs):
            out, err = p.communicate(timeout=180)
            assert p.returncode == 0, f"worker {idx} 失败: {err!r}"
        data = json.loads(settings.read_text(encoding="utf-8"))
        for k in range(n_proc):
            key = f"key_{k}"
            assert key in data, f"{key} 丢失——整文件覆盖式丢失更新回归！"
            assert data[key]["worker"] == key
            assert data[key]["i"] in range(rounds), f"{key} 终值必须是本 worker 写过的值"


class TestWriteAkStoreAtomic:
    def _redirect_ak_dirs(self, monkeypatch, tmp_path):
        """AK 候选位重定向：3 规范位 + 1 已存在旧读位 + 1 不存在旧读位。

        返回 (primary 预期路径, 已存在旧读位, 不存在旧读位)——SKILL_ROOT=d1 时
        resolve_ak_store_path 回退首位 = d1/.1688-AK/.ak_store.json。
        """
        d1, d2, d3 = tmp_path / "p1", tmp_path / "p2", tmp_path / "p3"
        legacy_exist = tmp_path / "legacy_a"
        legacy_missing = tmp_path / "legacy_b"
        for d in (d1, d2, d3, legacy_exist):
            d.mkdir(parents=True)
        (legacy_exist / cs.AK_STORE_FILENAME).write_text(
            json.dumps({"ak": "OLD"}), encoding="utf-8")
        monkeypatch.setattr(cs, "_ak_store_dirs",
                            lambda: [d1, d2, d3, legacy_exist, legacy_missing])
        monkeypatch.setattr(cs, "SKILL_ROOT", d1)
        primary = d1 / '.1688-AK' / cs.AK_STORE_FILENAME
        return primary, legacy_exist, legacy_missing

    def test_atomic_write_no_tmp_leftover_and_writes_through_legacy(self, tmp_path,
                                                                    monkeypatch):
        """首选位必写、已存在旧读位写穿、不存在旧读位不新建；无 .tmp 残留。"""
        primary, legacy_exist, legacy_missing = self._redirect_ak_dirs(monkeypatch, tmp_path)
        ret = cs.write_ak_store_file("AK_NEW")
        assert ret == primary
        for p in (primary, legacy_exist / cs.AK_STORE_FILENAME):
            data = json.loads(p.read_text(encoding="utf-8"))
            assert data["ak"] == "AK_NEW", f"{p} 必须写穿新值"
        assert not (legacy_missing / cs.AK_STORE_FILENAME).exists(), "不新建旧位"
        leftovers = [str(p) for p in tmp_path.rglob("*.tmp")]
        assert leftovers == [], f"不得残留 tmp 文件: {leftovers}"
        assert cs.read_ak_store_file() == "AK_NEW"

    def test_write_goes_through_os_replace(self, tmp_path, monkeypatch):
        """写入必须走 tmp + os.replace（原子替换），非 write_text 直写。"""
        primary, _lx, _lm = self._redirect_ak_dirs(monkeypatch, tmp_path)
        real_replace = os.replace
        calls = {"n": 0}

        def spy_replace(src, dst):
            calls["n"] += 1
            assert str(src).endswith(".tmp"), "必须先写 tmp 再 replace"
            return real_replace(src, dst)

        import scripts.lib.config_store as cs_mod
        monkeypatch.setattr(cs_mod.os, "replace", spy_replace)
        cs.write_ak_store_file("AK_R")
        assert calls["n"] == 2, "首选位 + 已存在旧读位 = 2 次 replace"

    def test_windows_replace_retry_once(self, tmp_path, monkeypatch):
        """os.replace 遇 Windows 文件锁失败 → 短等待重试一次成功。"""
        primary, _lx, _lm = self._redirect_ak_dirs(monkeypatch, tmp_path)
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("Windows file lock")
            return real_replace(src, dst)

        monkeypatch.setattr(cs.os, "replace", flaky)
        cs.write_ak_store_file("AK_RETRY")
        assert calls["n"] >= 2, "首次 replace 失败必须重试"
        data = json.loads(primary.read_text(encoding="utf-8"))
        assert data["ak"] == "AK_RETRY"
