#!/usr/bin/env python3
"""variant_v2 重量真值链（毛子移植，PLAN-data-pool-parity-v1 Task 6.3）。

四组锁定：
1. fetch_variant_truth（ozon_widget）：mock CdpTab.evaluate 两次返回
   （search-sku-base → variant_id；create-bundle-by-variant-id → item 重量
   尺寸）→ {"weight_g", "dims_mm"} 真值 dict，且两端点/常量逐字来自取证；
2. 垃圾/缺字段/异常 → None（绝不 raise）；
3. _giveback_metrics 带 variant map → 上报行含 variant_payload；不带 → 行与
   Task 2.2 逐字一致（byte-identical）；
4. build_envelope_from_discovery：池行 variant_payload.weight_g 为正 →
   draft.weight 用真值 + weight_from_pool_variant 标记；池 None → 走既有
   估算路径（byte-identical）。

纯 mock，不触网、不启 Chrome。响应形状取证自 maozi-plugin-3.2.6
content-scripts/content.js（详见 ozon_widget.fetch_variant_truth 模块注释）。
"""
from __future__ import annotations

import json
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cloud_probe  # noqa: E402
from scripts.lib import metrics_pool_client as mpc  # noqa: E402
from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib import ozon_widget as ow  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402

# evaluate 缝 mock：两段 JS 的「返回值」（JS 内 JSON.stringify 的产物，
# 非 HTTP 原始响应——缝在 evaluate，不在 fetch）。
JS_SEARCH_OUT = json.dumps({"company_id": "123", "variant_id": 987654321})
JS_BUNDLE_OUT = json.dumps({
    "weight": 1840, "depth": 260, "width": 150, "height": 100,
})


class _FakeTab:
    """evaluate 按 JS 内容分发的假 tab（消费预排响应队列）。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[str] = []

    def set_bypass_csp(self):
        pass

    def evaluate(self, js, await_promise=False, timeout=15):
        self.calls.append(js)
        if not self._responses:
            raise AssertionError("unexpected extra evaluate call")
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _FakeCdp:
    def __init__(self, tab):
        self._tab = tab
        self.released: list = []

    def find_tab(self, pattern):
        return self._tab

    def release(self, tab):  # 复用用户 tab：只 release 不远程关
        self.released.append(tab)


# ---------------------------------------------------------------------------
# 1+2. fetch_variant_truth：真值形状 / 垃圾输入 → None
# ---------------------------------------------------------------------------


def test_fetch_variant_truth_shape():
    tab = _FakeTab([JS_SEARCH_OUT, JS_BUNDLE_OUT])
    cdp = _FakeCdp(tab)
    got = ow.fetch_variant_truth("http://127.0.0.1:9222", "3171397439", cdp=cdp)
    assert got == {"weight_g": 1840, "dims_mm": [260, 150, 100]}
    # 恰两次页内 evaluate；端点/常量逐字来自 maozi 取证（禁改断言锁漂移）
    assert len(tab.calls) == 2
    assert "https://seller.ozon.ru/api/v1/search" in tab.calls[0]
    assert "3171397439" in tab.calls[0]                      # sku 进搜索体
    assert "seller-prototype/create-bundle-by-variant-id" in tab.calls[1]
    assert "SOURCE_UI_COPY_MERGED" in tab.calls[1]
    assert "987654321" in tab.calls[1]                       # variant_id 进 bundle 体
    # E4 纪律：复用命中 tab 立即 release（防连接 close 远程关用户 tab）
    assert cdp.released == [tab]


def test_fetch_variant_truth_none_on_garbage():
    cdp_url = "http://127.0.0.1:9222"

    # evaluate 返回非 JSON 垃圾
    assert ow.fetch_variant_truth(
        cdp_url, "1", cdp=_FakeCdp(_FakeTab(["not json at all"]))) is None

    # search 端点报错（HTTP 401 等）→ 无 variant_id，仅 1 次 evaluate
    tab = _FakeTab([json.dumps({"error": "HTTP 401 unauthorized"})])
    assert ow.fetch_variant_truth(cdp_url, "1", cdp=_FakeCdp(tab)) is None
    assert len(tab.calls) == 1

    # search 成功但 bundle 响应缺 item（data 为空壳）
    tab = _FakeTab([JS_SEARCH_OUT, json.dumps({"data": {}})])
    assert ow.fetch_variant_truth(cdp_url, "1", cdp=_FakeCdp(tab)) is None

    # item.weight 缺失/非正 → 视为无真值
    tab = _FakeTab([JS_SEARCH_OUT,
                    json.dumps({"weight": 0, "depth": 1, "width": 2, "height": 3})])
    assert ow.fetch_variant_truth(cdp_url, "1", cdp=_FakeCdp(tab)) is None
    tab = _FakeTab([JS_SEARCH_OUT,
                    json.dumps({"depth": 1, "width": 2, "height": 3})])
    assert ow.fetch_variant_truth(cdp_url, "1", cdp=_FakeCdp(tab)) is None

    # search 无候选（variants 空壳）
    tab = _FakeTab([json.dumps({})])
    assert ow.fetch_variant_truth(cdp_url, "1", cdp=_FakeCdp(tab)) is None

    # evaluate 抛异常（tab 已死/CDP 断开）→ None 不外逃
    tab = _FakeTab([RuntimeError("websocket closed")])
    assert ow.fetch_variant_truth(cdp_url, "1", cdp=_FakeCdp(tab)) is None

    # find_tab/new_tab 全炸（无 Chrome）→ None 不外逃
    class _DeadCdp:
        def find_tab(self, pattern):
            raise RuntimeError("no chrome")

        def new_tab(self, url):
            raise RuntimeError("no chrome")

    assert ow.fetch_variant_truth(cdp_url, "1", cdp=_DeadCdp()) is None


# ---------------------------------------------------------------------------
# 3. _giveback_metrics 携带 variant_payload
# ---------------------------------------------------------------------------


def test_giveback_carries_variant_payload(monkeypatch):
    seen = {}

    def fake_report(items, **kwargs):
        seen["items"] = items
        return len(items)

    monkeypatch.setattr(mpc, "report_seller_sync", fake_report)

    vp = {"weight_g": 1840, "dims_mm": [260, 150, 100]}
    od._giveback_metrics(
        [("3171397439", {"monthsales": 140}), ("42", {"monthsales": 1})],
        variant_payloads={"3171397439": vp})
    assert seen["items"] == [
        {"sku": "3171397439", "sales_payload": {"monthsales": 140},
         "variant_payload": vp},
        {"sku": "42", "sales_payload": {"monthsales": 1}},  # 未命中行零增键
    ]

    # 不带 variant map → 行与 Task 2.2 逐字一致（byte-identical）
    od._giveback_metrics([("3171397439", {"monthsales": 140})])
    assert seen["items"] == [
        {"sku": "3171397439", "sales_payload": {"monthsales": 140}},
    ]


# ---------------------------------------------------------------------------
# 4. 信封重量消费池真值
# ---------------------------------------------------------------------------


def _mk_candidate(**overrides) -> ProductCandidate:
    base = dict(
        ozon_product_id="4767514314",
        ozon_title="Автопоилка для кошек 2л",
        ozon_price=1290.0,
        match_1688_url="https://detail.1688.com/offer/980815374096.html",
        competing_sellers=0,
    )
    base.update(overrides)
    c = ProductCandidate(
        ozon_product_id=base["ozon_product_id"],
        ozon_title=base["ozon_title"],
        ozon_price=base["ozon_price"],
    )
    c.match_1688_url = base["match_1688_url"]
    c.competing_sellers = base["competing_sellers"]
    c.weight_g = 0
    c.dimensions_mm = {}
    c.ozon_category = {}
    return c


def _build_envelope(cand: ProductCandidate) -> dict | None:
    """走 build_envelope_from_discovery 降级组装段（retry → None → 本地兜底，
    该段 draft.weight = candidate.weight_g or 300 即「既有估算路径」）。"""
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=None), \
         mock.patch("scripts.lib.config_store.get_mxou_token",
                    return_value="sk-test"), \
         mock.patch("scripts.lib.config_store.get_store_profile",
                    return_value={}):
        return cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})


def test_envelope_weight_uses_pool_variant(monkeypatch):
    monkeypatch.delenv("METRICS_POOL_QUERY", raising=False)
    pool = {"4767514314": {
        "sku": 4767514314, "sales_payload": None,
        "variant_payload": {"weight_g": 1840, "dims_mm": [260, 150, 100]},
    }}
    seen = {}

    def fake_query(skus, **kw):
        seen["skus"] = list(skus)
        return {k: dict(v) for k, v in pool.items()}

    monkeypatch.setattr(mpc, "query_sku_metrics", fake_query)
    cand = _mk_candidate()
    result = _build_envelope(cand)

    assert result is not None
    assert seen["skus"] == ["4767514314"]  # 用候选 sku 查池
    draft = result["envelope"]["draft"]
    assert draft["weight"] == 1840          # 真值覆盖 300 估算兜底
    assert draft.get("weight_from_pool_variant") is True


def test_envelope_weight_pool_none_byte_identical(monkeypatch):
    monkeypatch.delenv("METRICS_POOL_QUERY", raising=False)
    monkeypatch.setattr(mpc, "query_sku_metrics", lambda skus, **kw: None)
    cand = _mk_candidate()
    result = _build_envelope(cand)

    assert result is not None
    draft = result["envelope"]["draft"]
    assert draft["weight"] == 300  # 既有估算兜底逐字保持
    assert "weight_from_pool_variant" not in draft


def test_envelope_weight_zero_pool_weight_ignored(monkeypatch):
    """variant_payload.weight_g 非正（缺失/0）→ 不覆盖不打标（宁缺毋滥）。"""
    monkeypatch.delenv("METRICS_POOL_QUERY", raising=False)
    monkeypatch.setattr(mpc, "query_sku_metrics", lambda skus, **kw: {
        "4767514314": {"sku": 4767514314, "variant_payload": {"weight_g": 0}}})
    result = _build_envelope(_mk_candidate())
    draft = result["envelope"]["draft"]
    assert draft["weight"] == 300
    assert "weight_from_pool_variant" not in draft


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
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
