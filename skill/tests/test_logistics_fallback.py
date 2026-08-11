"""P1-5: 物流报价失败 → last-good 缓存 / 40kg 兜底 + 估算标记（TDD RED→GREEN）。

反馈：报价 API 失败时利润估算用 40 CNY/kg 平仓费率，与实际标价偏差大 ——
估算必须标记为估算，并在 API 失败时优先复用 last-good 费率。

覆盖：
1. API 失败 + last-good 命中（同重量带）→ 用缓存费率而非 40/kg；estimated=True。
2. API 成功 → 真实费率；estimated=False；fallback_chain 透传。
3. API 失败 + 无 last-good → 40/kg 兜底 + estimated=True。
4. candidate.dimensions_mm（竞品尺寸）传入 → 请求体按 cm 转换。
5. 无尺寸 → 请求体默认 10cm 立方（行为保持）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_logistics_fallback.py -q
"""
from __future__ import annotations

import contextlib
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def _mk(weight_g=0, dims_mm=None, price=1000.0, cost=50.0):
    c = ProductCandidate(ozon_product_id="p1", ozon_title="Товар", ozon_price=price)
    c.match_1688_price = cost
    c.weight_g = weight_g
    c.dimensions_mm = dict(dims_mm) if dims_mm else {}
    return c


def _clear_caches():
    od._LOGISTICS_QUOTE_CACHE.clear()
    od._LAST_GOOD_LOGISTICS.clear()


def _patch_token(post=None):
    """mock get_mxou_token + requests.post；post 缺省 → 模拟 Worker 不可达。"""
    if post is None:

        def _boom(*a, **kw):
            raise ConnectionError("worker unreachable")

        post = _boom
    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"))
    stack.enter_context(mock.patch("requests.post", side_effect=post))
    return stack


# ── ① API 失败 + last-good 命中 → 用缓存费率（非 40/kg）───────────────────────

def test_api_fail_last_good_rate_used_not_flat():
    """API 失败 + 同重量带 last-good 命中 → 用缓存费率；estimated=True。"""
    _clear_caches()
    od._LAST_GOOD_LOGISTICS[500] = (18.5, time.time())  # 500g 带（250g 带宽）
    cand = _mk(weight_g=550)
    with _patch_token():
        od._calculate_profit(cand)
    assert cand.estimated_logistics_cny == 18.5, "应复用 last-good 费率而非 40/kg"
    assert cand.logistics_estimated is True
    assert cand.logistics_fallback_chain == "last_good"


# ── ② API 成功 → 真实费率，非估算，fallback_chain 透传 ───────────────────────

def test_api_success_real_rate_not_estimated():
    """API 成功 → 真实费率；estimated=False；fallback_chain/channel 透传。"""
    _clear_caches()
    resp = _FakeResp(payload={
        "logistics_cost_cny": 12.5,
        "fallback_chain": ["default_weight_rate", "RETS_standard"],
        "channel": "RETS_Standard_fallback",
    })
    cand = _mk(weight_g=1000)
    with _patch_token(post=lambda *a, **k: resp):
        od._calculate_profit(cand)
    assert cand.estimated_logistics_cny == 12.5
    assert cand.logistics_estimated is False, "实时费率不算估算"
    assert "RETS_standard" in cand.logistics_fallback_chain
    assert od._LAST_GOOD_LOGISTICS.get(od._logistics_weight_band(1000))[0] == 12.5, \
        "API 成功后应写入 last-good 缓存供后续失败复用"


# ── ③ API 失败 + 无 last-good → 40/kg 兜底 + estimated=True ──────────────────

def test_api_fail_no_last_good_flat_fallback():
    """API 失败 + 无 last-good → 40/kg 兜底；estimated=True。"""
    _clear_caches()
    cand = _mk(weight_g=1000)
    with _patch_token():
        od._calculate_profit(cand)
    expected = max(8.0, cand.weight_g / 1000.0 * od.LOGISTICS_PER_KG_CNY)
    assert cand.estimated_logistics_cny == expected
    assert cand.logistics_estimated is True
    assert cand.logistics_fallback_chain == "flat_per_kg_40"


# ── ④ 竞品尺寸传入 → 请求体按 cm 转换 ────────────────────────────────────────

def test_competitor_dims_forwarded_as_cm():
    """candidate.dimensions_mm 传入 → 请求体 depth/width/height_cm 为 mm/10。"""
    _clear_caches()
    resp = _FakeResp(payload={"logistics_cost_cny": 9.9})
    seen = {}

    def _capture(url, json=None, **kw):
        seen.update(json or {})
        return resp

    cand = _mk(weight_g=800, dims_mm={"length": 200, "width": 150, "height": 100})
    with _patch_token(post=_capture):
        od._calculate_profit(cand)
    assert seen.get("depth_cm") == 20.0
    assert seen.get("width_cm") == 15.0
    assert seen.get("height_cm") == 10.0
    assert seen.get("weight_g") == 800


# ── ⑤ 无尺寸 → 默认 10cm 立方（行为保持） ────────────────────────────────────

def test_default_dims_10cm_cube_when_none():
    """无竞品尺寸 → 请求体仍用 10cm 立方（不回归）。"""
    _clear_caches()
    resp = _FakeResp(payload={"logistics_cost_cny": 5.5})
    seen = {}

    def _capture(url, json=None, **kw):
        seen.update(json or {})
        return resp

    cand = _mk(weight_g=300)
    with _patch_token(post=_capture):
        od._calculate_profit(cand)
    assert seen.get("depth_cm") == 10.0
    assert seen.get("width_cm") == 10.0
    assert seen.get("height_cm") == 10.0


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
