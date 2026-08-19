"""任务 2.2: _query_commission_from_worker TDD（RED→GREEN）。

worker 端点 GET /api/v1/commissions/lookup?category_id={int}（Bearer token，sk- 前缀服务端剥离）
命中 {"found": true, "fbs": {"leq_1500","leq_5000","gt_5000"}, "fbo": {...}, "source": "..."}
未命中 {"found": false}

覆盖：
1. 命中 → 返回 fbs/fbo/source 分段 dict；请求带 Bearer token + category_id。
2. found:false → None（调用方走本地兜底）。
3. Worker 不可达/异常 → None（不 raise）。
4. _calculate_profit 接入：worker 分段佣金优先（按 ozon_price 选带）。
5. _calculate_profit 接入：worker 不可达 → 本地候选分段 → 默认 12/14/18。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_query_commission_worker.py -q
"""
from __future__ import annotations

import contextlib
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def _clear_commission_caches():
    od._COMMISSION_CACHE.clear()
    od._LAST_GOOD_COMMISSION.clear()


def _patch_token(get=None):
    """mock get_mxou_token + requests.get；get 缺省 → 模拟 Worker 不可达。"""
    if get is None:

        def _boom(*a, **kw):
            raise ConnectionError("worker unreachable")

        get = _boom
    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"))
    stack.enter_context(mock.patch("requests.get", side_effect=get))
    return stack


# ── ① 命中 → 返回分段 dict ──────────────────────────────────────────

def test_query_commission_hit():
    """found:true → 返回 {"fbs","fbo","source"}；请求带 Bearer token + category_id。"""
    _clear_commission_caches()
    payload = {
        "found": True,
        "fbs": {"leq_1500": 12.0, "leq_5000": 14.0, "gt_5000": 18.0},
        "fbo": {"leq_1500": 10.0, "leq_5000": 11.0, "gt_5000": 13.0},
        "source": "cache",
    }
    seen = {}

    def _capture(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers") or {}
        seen["params"] = kw.get("params") or {}
        return _FakeResp(payload=payload)

    with _patch_token(get=_capture):
        result = od._query_commission_from_worker(17028892)
    assert result is not None
    assert result["fbs"]["leq_5000"] == 14.0
    assert result["fbo"]["gt_5000"] == 13.0
    assert result["source"] == "cache"
    assert int(seen["params"].get("category_id")) == 17028892, \
        f"应传 category_id 查询参数, got {seen['params']}"
    assert seen["headers"].get("Authorization") == "Bearer sk-test", \
        f"应带 Bearer token, got {seen['headers']}"


# ── ② found:false → None ────────────────────────────────────────────

def test_query_commission_miss_fallback():
    """found:false → None（调用方走本地兜底，不抛异常）。"""
    _clear_commission_caches()
    resp = _FakeResp(payload={"found": False})
    with _patch_token(get=lambda *a, **kw: resp):
        result = od._query_commission_from_worker(17028892)
    assert result is None


# ── ③ Worker 不可达/异常 → None ─────────────────────────────────────

def test_query_commission_worker_down():
    """异常/超时 → None（不 raise）。"""
    _clear_commission_caches()
    with _patch_token():  # 默认抛 ConnectionError
        result = od._query_commission_from_worker(17028892)
    assert result is None


def test_query_commission_no_category_id():
    """无 category_id → 不请求，直接 None。"""
    _clear_commission_caches()
    assert od._query_commission_from_worker(0) is None
    assert od._query_commission_from_worker(None) is None


# ── ④ _calculate_profit：worker 分段佣金优先 ─────────────────────────

def _mk_candidate(price=2000.0, category_id="17028892"):
    c = ProductCandidate(ozon_product_id="p1", ozon_title="Товар", ozon_price=price)
    c.match_1688_price = 50.0
    c.weight_g = 500
    if category_id:
        c.ozon_category = {"description_category_id": str(category_id), "type_id": "1"}
    return c


def test_calculate_profit_uses_worker_commission_segments():
    """worker 分段佣金优先：2000₽ → leq_5000 带 14%。"""
    _clear_commission_caches()
    cand = _mk_candidate(price=2000.0)
    worker_segs = {
        "fbs": {"leq_1500": 12.0, "leq_5000": 14.0, "gt_5000": 18.0},
        "fbo": {},
        "source": "cache",
    }
    with mock.patch("scripts.lib.ozon_discovery._query_commission_from_worker",
                    return_value=worker_segs) as m_c:
        od._calculate_profit(cand, fx_rate=0.08)
    m_c.assert_called_once_with("17028892")
    assert cand.estimated_commission == pytest.approx(cand.ozon_price * 0.08 * 0.14), \
        f"应使用 worker leq_5000 段 14%, got {cand.estimated_commission}"


# ── ⑤ _calculate_profit：本地分段 → 默认 12/14/18 ───────────────────

def test_calculate_profit_fallback_local_segments_then_defaults():
    """worker 不可达 → 本地候选分段；两者皆无 → 默认分段 12/14/18。"""
    _clear_commission_caches()
    # worker None + 候选无分段 → 默认：8000₽ → gt_5000 段 18%
    cand = _mk_candidate(price=8000.0)
    with mock.patch("scripts.lib.ozon_discovery._query_commission_from_worker",
                    return_value=None):
        od._calculate_profit(cand, fx_rate=0.08)
    assert cand.estimated_commission == pytest.approx(cand.ozon_price * 0.08 * 0.18), \
        f"默认应取 gt_5000 段 18%, got {cand.estimated_commission}"

    # worker None + 候选有本地分段 → 本地：1200₽ → leq_1500 段 10%
    cand2 = _mk_candidate(price=1200.0)
    cand2.commission_rfbs_segments = {"leq_1500": 10.0, "leq_5000": 11.0, "gt_5000": 12.0}
    with mock.patch("scripts.lib.ozon_discovery._query_commission_from_worker",
                    return_value=None):
        od._calculate_profit(cand2, fx_rate=0.08)
    assert cand2.estimated_commission == pytest.approx(cand2.ozon_price * 0.08 * 0.10), \
        f"应使用本地 leq_1500 段 10%, got {cand2.estimated_commission}"


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
