"""v0.75 字典缓存单飞锁单测（BL-25 Phase 2 防击穿 singleflight）。

acquire_singleflight = per-key 互斥（同 key 串行、异 key 并行、等待超时重查
不 raise）；run_exclusive = 同 key 并发回源去重（leader 执行一次、follower
共享结果、异常后锁释放可重进）；get_or_fetch = miss 判定出口读穿接线点。
全部进程内 threading 并发，不触网；PG 走 mock（LocalDBManager 打桩）。
"""
import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402

from utils import dict_value_cache as dvc  # noqa: E402

# 夹具假 key 片段（非关键词常量 + f-string 拼接，规避 gitleaks 字面形态）
_FX = "abc" * 4


def _fx_key(tag: str, dc: int, tp: int) -> tuple:
    return (f"{_FX}_{tag}", dc, tp, "RU")


def _wait_for_waiters(base_hits: int, need: int, timeout: float = 5.0) -> None:
    """leader 在临界区内等全部 follower 进入锁等待（进入等待必已快照飞行代号），
    保证 run_exclusive 去重判定稳定——晚到线程不会退化成下一轮 leader。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if dvc.singleflight_stats()["inflight_hits"] - base_hits >= need:
            return
        time.sleep(0.005)


@pytest.fixture(autouse=True)
def _clean_negative_cache():
    """进程内负缓存是模块级状态，用例间清场防串扰。"""
    with dvc._NEG_LOCK:
        dvc._NEG_CACHE.clear()
    yield
    with dvc._NEG_LOCK:
        dvc._NEG_CACHE.clear()


def _patch_ldb_get(return_value):
    return mock.patch(
        "utils.local_db_manager.LocalDBManager.get_dictionary_value_cache",
        return_value=return_value,
    )


def _patch_ldb_set():
    recorder = mock.MagicMock()
    patcher = mock.patch(
        "utils.local_db_manager.LocalDBManager.set_dictionary_value_cache",
        recorder,
    )
    return patcher, recorder


# ═══════════ singleflight_stats 计数器 ═══════════

def test_singleflight_stats_shape_and_copy():
    stats = dvc.singleflight_stats()
    assert set(stats.keys()) == {"inflight_hits", "acquired"}
    assert all(isinstance(v, int) and v >= 0 for v in stats.values())
    # 返回副本：外部改写不影响内部计数
    stats["acquired"] = 10 ** 9
    assert dvc.singleflight_stats()["acquired"] != 10 ** 9


# ═══════════ acquire_singleflight：per-key 互斥 ═══════════

def test_acquire_same_key_serializes():
    key = _fx_key("ser", 1, 2)
    before = dvc.singleflight_stats()
    cur = [0]
    max_conc = [0]
    entered = []
    guard = threading.Lock()

    def worker():
        with dvc.acquire_singleflight(key):
            with guard:
                cur[0] += 1
                max_conc[0] = max(max_conc[0], cur[0])
                entered.append(1)
            time.sleep(0.02)
            with guard:
                cur[0] -= 1

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads)
    assert len(entered) == 10                       # 阻塞等待后全部进入
    assert max_conc[0] == 1                         # 临界区严格串行
    after = dvc.singleflight_stats()
    assert after["acquired"] - before["acquired"] == 10
    assert after["inflight_hits"] - before["inflight_hits"] >= 9
    assert key not in dvc._SF_INFLIGHT              # 全部退出后无残留


def test_acquire_different_keys_do_not_mutex():
    k_a, k_b = _fx_key("parA", 1, 2), _fx_key("parB", 3, 4)
    a_in, b_in = threading.Event(), threading.Event()

    def worker_a():
        with dvc.acquire_singleflight(k_a):
            a_in.set()
            assert b_in.wait(timeout=5), "异 key 应并行进入（疑被全局互斥阻塞）"

    def worker_b():
        with dvc.acquire_singleflight(k_b):
            b_in.set()
            assert a_in.wait(timeout=5), "异 key 应并行进入（疑被全局互斥阻塞）"

    ta, tb = threading.Thread(target=worker_a), threading.Thread(target=worker_b)
    ta.start()
    tb.start()
    ta.join(timeout=15)
    tb.join(timeout=15)
    assert not ta.is_alive() and not tb.is_alive()


def test_acquire_wait_timeout_rechecks_without_raise(monkeypatch):
    """wait 超时后循环重查：仍占则继续等，不 raise（宁可慢不可死锁）。"""
    monkeypatch.setattr(dvc, "_SF_WAIT_TIMEOUT", 0.05)
    key = _fx_key("tmo", 9, 9)
    held, release = threading.Event(), threading.Event()
    entered = []

    def holder():
        with dvc.acquire_singleflight(key):
            held.set()
            release.wait(timeout=5)

    th = threading.Thread(target=holder)
    th.start()
    assert held.wait(timeout=5)
    tw = threading.Thread(
        target=lambda: [dvc.acquire_singleflight(key).__enter__(),
                        entered.append(1),
                        dvc._SF_INFLIGHT.discard(key)])
    tw.start()
    time.sleep(0.3)                                 # 经历若干次 50ms 超时重查
    release.set()
    th.join(timeout=10)
    tw.join(timeout=10)
    assert entered == [1]                           # 等待者最终进入且未 raise
    assert not tw.is_alive()


# ═══════════ run_exclusive：同 key 去重执行 ═══════════

def test_run_exclusive_dedup_and_shared_result():
    key = _fx_key("dedup", 5, 6)
    calls = [0]
    shared = {"payload": _FX}                       # 同一对象 = 全线程同一返回值
    base_hits = dvc.singleflight_stats()["inflight_hits"]
    barrier = threading.Barrier(10)

    def fn():
        _wait_for_waiters(base_hits, 9)
        calls[0] += 1
        return shared

    results = [None] * 10
    errors = []

    def worker(i):
        try:
            barrier.wait(timeout=5)
            results[i] = dvc.run_exclusive(key, fn)
        except Exception as exc:                    # pragma: no cover - 失败时暴露
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not errors
    assert all(not t.is_alive() for t in threads)
    assert calls[0] == 1                            # fn 并发 10 线程只执行 1 次
    assert all(r is shared for r in results)        # 全部线程拿到同一返回值


def test_run_exclusive_exception_releases_lock():
    key = _fx_key("boom", 7, 8)

    class _Boom(Exception):
        pass

    def bad():
        raise _Boom("fetch failed")

    with pytest.raises(_Boom):
        dvc.run_exclusive(key, bad)
    assert key not in dvc._SF_INFLIGHT              # 异常路径锁已释放

    good_calls = [0]

    def good():
        good_calls[0] += 1
        return "ok"

    assert dvc.run_exclusive(key, good) == "ok"     # 后续可重进
    assert good_calls[0] == 1


# ═══════════ get_or_fetch：miss 判定出口读穿接线点 ═══════════

def test_get_or_fetch_miss_dedups_concurrent_fetch():
    values = [{"id": 101, "value": "Alpha"}]
    fetches = [0]
    base_hits = dvc.singleflight_stats()["inflight_hits"]
    barrier = threading.Barrier(10)

    def fetch_fn():
        _wait_for_waiters(base_hits, 9)
        fetches[0] += 1
        return list(values)

    set_patcher, _rec = _patch_ldb_set()
    with _patch_ldb_get(None), set_patcher:
        results = [None] * 10
        errors = []

        def worker(i):
            try:
                barrier.wait(timeout=5)
                results[i] = dvc.get_or_fetch(11, 22, 33, "RU", fetch_fn)
            except Exception as exc:                # pragma: no cover - 失败时暴露
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
    assert not errors
    assert fetches[0] == 1                          # 并发 miss 回源只打一次
    assert all(r == values for r in results)        # 全部拿到同一份结果


def test_get_or_fetch_hit_skips_fetch():
    with _patch_ldb_get({"values_data": [{"id": 9}]}):
        def fetch_fn():
            raise AssertionError("缓存命中不应回源")

        assert dvc.get_or_fetch(1, 2, 3, "RU", fetch_fn) == [{"id": 9}]


def test_get_or_fetch_empty_result_records_negative():
    fetches = [0]

    def fetch_fn():
        fetches[0] += 1
        return []

    set_patcher, set_recorder = _patch_ldb_set()
    with _patch_ldb_get(None), set_patcher:
        assert dvc.get_or_fetch(44, 55, 66, "RU", fetch_fn) == []
        assert fetches[0] == 1
        # 负缓存窗口内的第二次 miss：短路不回源
        assert dvc.get_or_fetch(44, 55, 66, "RU", fetch_fn) == []
        assert fetches[0] == 1
    # 确认空 → 负缓存 sentinel 落库恰一次（空结果不走 routed_set 正数据）
    assert set_recorder.call_count == 1


# ── 发版前终审 review 补测（Important#1/#3）──

def test_fetch_raise_then_success_not_negatively_cached():
    """fetch_fn 抛异常 → 不落负缓存：紧随的成功调用必须真回源拿到值。"""
    from utils.dict_value_cache import get_or_fetch
    calls = {"n": 0}

    class _Boom(RuntimeError):
        pass

    def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Boom("ozon down")
        return [{"id": 1, "value": "v"}]

    try:
        get_or_fetch(77001, 1, 1, "RU", fetch_fn=fetch)
        raise AssertionError("should raise _Boom")
    except _Boom:
        pass
    ok = get_or_fetch(77001, 1, 1, "RU", fetch_fn=fetch)
    assert ok == [{"id": 1, "value": "v"}]
    assert calls["n"] == 2  # 失败未被当成「确认空」缓存——第二次真回源


def test_single_flight_no_waiter_leaves_no_slot():
    """零并发等待者的 miss 不留结果槽（内存驻留修复，终审 Important#1）。"""
    from utils import dict_value_cache as dvc
    dvc._SF_SLOT.clear()
    big = [{"id": i, "value": "v" * 50} for i in range(2000)]
    for i in range(50):  # 顺序（无并发等待者）50 次不同 key miss
        dvc.run_exclusive((99000 + i, 1, 1, "RU"), lambda b=big: b)
    assert len(dvc._SF_SLOT) == 0, f"零等待 miss 不应留槽，残留 {len(dvc._SF_SLOT)}"


def test_single_flight_slot_consumed_and_released():
    """并发等待场景：读者消费后槽归零（不驻留）。"""
    import threading
    from utils import dict_value_cache as dvc
    dvc._SF_SLOT.clear()
    out = []
    barrier = threading.Barrier(4)
    big = [{"id": 1, "value": "v"}]

    def worker():
        barrier.wait()
        out.append(dvc.run_exclusive((99500, 1, 1, "RU"), lambda: big))

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(o is big for o in out)
    assert len(out) == 4
    assert len(dvc._SF_SLOT) == 0, "读者消费完应删槽"
