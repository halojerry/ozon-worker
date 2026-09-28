"""P1-5: 物流报价失败 → last-good 缓存 + 鉴权 header（TDD RED→GREEN）。

v0.83 批①：算价走 worker batch 后，本地物流 helper（``_query_logistics_from_worker``）
不再参与利润估算，但仍是 discover 直查物流费率表的唯一入口（cli/cloud_probe 复用）
——本文件改为**直接测该 helper**（原经 _calculate_profit 的间接断言已随公式退役）。

覆盖：
1. API 失败 + last-good 命中（同重量带）→ 用缓存费率；estimated=True。
2. API 成功 → 真实费率；estimated=False；fallback_chain 透传 + 写 last-good。
3. API 失败 + 无 last-good → None（调用方如实降级，不再本地假运费）。
4. candidate.dimensions_mm（竞品尺寸）传入 → 请求体按 cm 转换。
5. 无尺寸 → 请求体默认 10cm 立方（行为保持）。
6. worker api-M4 鉴权收口联动 → 请求必须带 Authorization Bearer header。

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


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


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


# ── ① API 失败 + last-good 命中 → 用缓存费率 ──────────────────────────────────

def test_api_fail_last_good_rate_used_not_flat():
    """API 失败 + 同重量带 last-good 命中 → 用缓存费率；estimated=True。"""
    _clear_caches()
    od._LAST_GOOD_LOGISTICS[500] = (18.5, time.time())  # 500g 带（250g 带宽）
    with _patch_token():
        q = od._query_logistics_from_worker(550)
    assert q is not None and q.cost == 18.5, "应复用 last-good 费率"
    assert q.estimated is True
    assert q.fallback_chain == "last_good"


# ── ② API 成功 → 真实费率，非估算，fallback_chain 透传 + 写 last-good ─────────

def test_api_success_real_rate_not_estimated():
    """API 成功 → 真实费率；estimated=False；fallback_chain/channel 透传。"""
    _clear_caches()
    resp = _FakeResp(payload={
        "logistics_cost_cny": 12.5,
        "fallback_chain": ["default_weight_rate", "RETS_standard"],
        "channel": "RETS_Standard_fallback",
    })
    with _patch_token(post=lambda *a, **k: resp):
        q = od._query_logistics_from_worker(1000)
    assert q is not None and q.cost == 12.5
    assert q.estimated is False, "实时费率不算估算"
    assert "RETS_standard" in q.fallback_chain
    assert od._LAST_GOOD_LOGISTICS.get(od._logistics_weight_band(1000))[0] == 12.5, \
        "API 成功后应写入 last-good 缓存供后续失败复用"


# ── ③ API 失败 + 无 last-good → None（如实降级）──────────────────────────────

def test_api_fail_no_last_good_returns_none():
    """API 失败 + 无 last-good → None（调用方无预估，不再本地假运费）。"""
    _clear_caches()
    with _patch_token():
        assert od._query_logistics_from_worker(1000) is None


# ── ④ 竞品尺寸传入 → 请求体按 cm 转换 ────────────────────────────────────────

def test_competitor_dims_forwarded_as_cm():
    """dims_mm 传入 → 请求体 depth/width/height_cm 为 mm/10。"""
    _clear_caches()
    resp = _FakeResp(payload={"logistics_cost_cny": 9.9})
    seen = {}

    def _capture(url, json=None, **kw):
        seen.update(json or {})
        return resp

    with _patch_token(post=_capture):
        od._query_logistics_from_worker(800, dims_mm={"length": 200, "width": 150, "height": 100})
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

    with _patch_token(post=_capture):
        od._query_logistics_from_worker(300)
    assert seen.get("depth_cm") == 10.0
    assert seen.get("width_cm") == 10.0
    assert seen.get("height_cm") == 10.0


# ── ⑥ worker api-M4 后 Bearer 必填 → 请求必须带 Authorization header ─────────

def test_bearer_header_sent_with_token():
    """worker T10(api-M4) 鉴权收口联动：请求带 ``Authorization: Bearer <token>``。"""
    _clear_caches()
    resp = _FakeResp(payload={"logistics_cost_cny": 9.9})
    seen_headers = {}

    def _capture(url, json=None, headers=None, **kw):
        seen_headers.update(headers or {})
        return resp

    with _patch_token(post=_capture):
        od._query_logistics_from_worker(300)
    assert seen_headers.get("Authorization") == "Bearer sk-test"


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
