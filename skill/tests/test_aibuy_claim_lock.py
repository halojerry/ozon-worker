#!/usr/bin/env python3
"""T3 aibuy 刷新 claim 真原子化 + mtop token 错误标志加锁回归测试（fix/skill-concurrency-v1）。

背景（竞态 #4）：旧 `_try_claim_aibuy_refresh` = get_setting 检查 → set_setting
占位，两步 TOCTOU——双进程可同时通过检查 → 双 tab 同时导航 1688。同模块
`_MTOP_TOKEN_ERROR` 模块级 dict 在 `--match-concurrency>1` 线程间无锁读写。

修复契约（本文件锁定，方案 §A3 T3；O_EXCL 先例 = ak_1688_client
`.refresh_claim.json`）：
  1. claim 改 `O_CREAT|O_EXCL` 占位文件 `data/config/.aibuy_refresh_claim.json`，
     内容 `{"ts", "pid"}`——建文件是原子操作，双竞争者恰一方成功；
  2. 已有未过期占位 → False；过期/损坏 → unlink 后重试一次 O_EXCL；
  3. 机制自身故障（非 FileExistsError 的 OSError 等）→ True（降级无冷却，
     不阻塞刷新，与 ak_1688_client 同口径）；
  4. `_clear_aibuy_refresh_claim()` 改 unlink；旧 settings 键逻辑删除；
  5. `_MTOP_TOKEN_ERROR` 置位/读清全部持模块级 `_MTOP_TOKEN_LOCK`，读-清一体。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_aibuy_claim_lock.py -q
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts._const  # noqa: E402
from scripts.lib import ozon_image_search as ois  # noqa: E402


@pytest.fixture()
def claim_dir(tmp_path, monkeypatch):
    """claim 占位文件目录重定向到 tmp（_aibuy_refresh_claim_path 每次函数内取
    scripts._const.CONFIG_DIR，patch 模块属性即生效，不碰真实 skill/data/config）。"""
    monkeypatch.setattr(scripts._const, "CONFIG_DIR", tmp_path)
    return tmp_path


def _claim_path(claim_dir) -> Path:
    return ois._aibuy_refresh_claim_path()


def _write_claim(claim_dir, ts=None, payload=None) -> Path:
    p = _claim_path(claim_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        payload = json.dumps({"ts": time.time() if ts is None else ts, "pid": 999})
    p.write_text(payload, encoding="utf-8")
    return p


# ── ① O_EXCL 占位文件语义 ─────────────────────────────────────────────────────

class TestClaimFile:
    def test_claim_writes_ts_and_pid(self, claim_dir):
        assert _claim_path(claim_dir).exists() is False
        assert ois._try_claim_aibuy_refresh() is True
        data = json.loads(_claim_path(claim_dir).read_text(encoding="utf-8"))
        assert abs(data["ts"] - time.time()) < 60
        assert data["pid"] == os.getpid()

    def test_fresh_claim_blocks_second_claim(self, claim_dir):
        _write_claim(claim_dir, ts=time.time())
        assert ois._try_claim_aibuy_refresh() is False, "未过期占位必须拒绝（跨进程冷却）"

    def test_expired_claim_allows_reclaim(self, claim_dir):
        _write_claim(claim_dir, ts=time.time() - ois.AIBUY_REFRESH_COOLDOWN_SECONDS - 1)
        assert ois._try_claim_aibuy_refresh() is True, "过期占位必须可重 claim"
        data = json.loads(_claim_path(claim_dir).read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid(), "重 claim 后占位必须换成本进程"

    def test_corrupted_stale_claim_treated_expired(self, claim_dir):
        """陈旧损坏占位（内容坏 + mtime 超冷却）→ 过期重建（崩溃自愈，不永久堵死）。"""
        p = _write_claim(claim_dir, payload="not-json{{")
        stale = time.time() - ois.AIBUY_REFRESH_COOLDOWN_SECONDS - 1
        os.utime(p, (stale, stale))
        assert ois._try_claim_aibuy_refresh() is True

    def test_corrupt_but_fresh_mtime_blocks(self, claim_dir):
        """空/坏内容但 mtime 新鲜 → False——O_EXCL 建文件与写内容之间有窗口，
        并发读者恰在窗口内读到空文件时绝不能当「损坏→过期」unlink 抢走别人的
        claim（否则双 tab 同时导航 1688，T3 竞测实证的窗口竞态）。"""
        _write_claim(claim_dir, payload="")  # 空文件 = 恰在 create-write 窗口内的形态
        assert ois._try_claim_aibuy_refresh() is False

    def test_two_racers_exactly_one_wins(self, claim_dir):
        """双竞争者（跨线程逼近跨进程）：O_EXCL 原子性 → 每轮恰一方成功。"""
        for _round in range(20):
            p = _claim_path(claim_dir)
            if p.exists():
                p.unlink()
            barrier = threading.Barrier(4)
            winners = []

            def racer():
                barrier.wait(timeout=5)
                if ois._try_claim_aibuy_refresh():
                    winners.append(1)

            threads = [threading.Thread(target=racer) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert winners == [1], f"4 竞争者必须恰 1 方拿到 claim，实际 {len(winners)} 方"

    def test_clear_removes_file(self, claim_dir):
        _write_claim(claim_dir)
        ois._clear_aibuy_refresh_claim()
        assert _claim_path(claim_dir).exists() is False

    def test_clear_missing_file_ok(self, claim_dir):
        ois._clear_aibuy_refresh_claim()  # 文件不存在必须静默（不抛）
        assert _claim_path(claim_dir).exists() is False

    def test_fail_open_on_mechanism_failure(self, claim_dir, monkeypatch):
        """占位机制自身故障 → True（降级无冷却，不阻塞刷新）。"""
        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(os, "open", boom)
        assert ois._try_claim_aibuy_refresh() is True

    def test_old_settings_key_logic_removed(self):
        """旧 settings 键 AIBUY_REFRESH_CLAIM_KEY 逻辑删除（冷却语义瞬态，无迁移）。"""
        assert not hasattr(ois, "AIBUY_REFRESH_CLAIM_KEY"), \
            "旧 settings 键常量应已删除（claim 改 O_EXCL 文件）"

    def test_search_skips_navigation_on_fresh_claim_file(self, claim_dir, monkeypatch):
        """集成：新鲜占位文件在场 → search_by_image_aibuy 零导航直接降级 []。"""
        _write_claim(claim_dir, ts=time.time())
        nav_calls = []
        monkeypatch.setattr(ois, "cache_get", lambda *_a, **_k: None)
        monkeypatch.setattr(ois, "_read_aibuy_token", lambda: None)
        monkeypatch.setattr(ois, "_read_1688_cookies_silent", lambda cdp_url=None: {})
        monkeypatch.setattr(
            ois, "_fetch_aibuy_cookies_from_chrome",
            lambda cdp_url=None: nav_calls.append(1) or {})
        assert ois.search_by_image_aibuy("img_x") == []
        assert nav_calls == [], "冷却内有占位文件必须零导航"


# ── ② _MTOP_TOKEN_ERROR 线程安全 ─────────────────────────────────────────────

class TestMtopTokenErrorLock:
    def test_lock_exists_and_roundtrip(self):
        assert isinstance(ois._MTOP_TOKEN_LOCK, type(threading.Lock()))
        ois._MTOP_TOKEN_ERROR["hit"] = False
        ois._mark_mtop_token_error()
        assert ois._MTOP_TOKEN_ERROR["hit"] is True
        assert ois._consume_mtop_token_error() is True, "读清返回置位状态"
        assert ois._MTOP_TOKEN_ERROR["hit"] is False, "读清必须复位"
        assert ois._consume_mtop_token_error() is False, "复位后再读为 False"

    def test_mark_and_consume_hold_module_lock(self, monkeypatch):
        """置位与读-清都必须持 _MTOP_TOKEN_LOCK（读-清一体是防抹掉并发置位的关键）。"""
        enters = []
        real_lock = ois._MTOP_TOKEN_LOCK

        class _SpyLock:
            def __enter__(self):
                enters.append(1)
                return real_lock.__enter__()

            def __exit__(self, *exc):
                return real_lock.__exit__(*exc)

        monkeypatch.setattr(ois, "_MTOP_TOKEN_LOCK", _SpyLock())
        ois._MTOP_TOKEN_ERROR["hit"] = False
        ois._mark_mtop_token_error()
        assert ois._consume_mtop_token_error() is True
        assert len(enters) == 2, f"置位 1 次 + 读清 1 次 = 2 次持锁，实际 {len(enters)}"

    def test_thread_pressure_no_crash_and_bounded(self):
        """压力断言（简单口径）：并发置位/读清无异常、消费数 ≤ 置位数、终态可控。"""
        ois._MTOP_TOKEN_ERROR["hit"] = False
        errors = []
        consumed = []
        n = 400

        def setter():
            try:
                for _ in range(n):
                    ois._mark_mtop_token_error()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def consumer():
            try:
                for _ in range(n):
                    if ois._consume_mtop_token_error():
                        consumed.append(1)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=setter) for _ in range(2)] + \
                  [threading.Thread(target=consumer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        assert len(consumed) <= 2 * n, "消费数不得超过置位数"
        ois._mark_mtop_token_error()
        assert ois._consume_mtop_token_error() is True, "压力后置位必须仍可消费（锁不吞置位）"
        assert ois._consume_mtop_token_error() is False
