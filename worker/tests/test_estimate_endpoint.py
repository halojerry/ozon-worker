# -*- coding: utf-8 -*-
"""M1.2: 草稿预估售价端点测试（mock logistics quote + mock 草稿读取，无需 PG）。

覆盖：
1. CNY 草稿 → compute_price 数值与 pricing_node 公式 parity（精确数值断言）
2. RUB 草稿（exchange_rate 有值）→ price 用汇率换算
3. 草稿不存在 / 跨租户 → 404
4. 无 token → 401
5. 防漂移锁定：pricing_node 单 SKU 定价必须引用 compute_price（公式单处定义）
6. exchange_rate=None → 按 CNY 处理（与 skill 估算一致）
7. 任务 2.3：响应含真实佣金 commission_rate + commission_source
   （显式佣金 → explicit；mock get_category_commission → cache:{band}）

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_estimate_endpoint.py -q
"""
import asyncio
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from fastapi import HTTPException

# ── 与 test_drafts_api.make_envelope 同款草稿（物流费 mock 为 10.0，包装 2.0）──
VALID_ENVELOPE = {
    "draft": {
        "item_id": "980815374096",
        "title": "宠物自动饮水器",
        "images": ["https://cbu01.alicdn.com/x.jpg"],
        "weight": 227,
        "dimensions": {"length": 120, "width": 80, "height": 60},
        "purchase_cost": 5.5,
        "purchase_url": "https://detail.1688.com/offer/1.html",
    },
    "source": {"purchase_url": "https://detail.1688.com/offer/1.html", "purchase_cost": 5.5},
    "extensions": {"margin_rate": 0.25, "commission_rate": 0.10, "fx_buffer": 0.05},
}

# 独立期望值（从 pricing_node 公式独立推导，测试侧锁定数值，不依赖被测实现）：
# total_cost = 5.5(采购) + 10.0(mock物流) + 2.0(包装) = 17.5 CNY
# CNY:  price = ceil(17.5 × 1.25 / 0.9) = ceil(24.31) = 25
#       old_price (price≤25) = max(25+5, ceil(25×1.2)) = 30
#       profit_cny = 25 - 17.5 = 7.5 ; profit_rate = 7.5/17.5 = 0.4286
EXPECTED_CNY = {
    "price": 25,
    "old_price": 30,
    "profit_cny": 7.5,
    "profit_rate": 0.4286,
    "logistics_cost_cny": 10.0,
    "currency": "CNY",
}
# RUB (rate=12): base = 17.5 × 1.25 × 1.05 / 0.9 × 12 = 306.25 → ceil = 307
#       old_price = ceil(307×1.2) = 369
#       profit_cny = 307/12 - 17.5 = 8.0833 → 8.08 ; profit_rate = 8.0833/17.5 = 0.4619
EXPECTED_RUB = {
    "price": 307,
    "old_price": 369,
    "profit_cny": 8.08,
    "profit_rate": 0.4619,
    "logistics_cost_cny": 10.0,
    "currency": "RUB",
}


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def body(self):
        return json.dumps(self._body).encode("utf-8")


def _auth_mock(token: str, tenant: str) -> str:
    """模拟 main._authenticate_token 语义：空 token → 401，否则返回测试租户。"""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    return tenant


def _call_endpoint(draft_id, body, monkeypatch, payload=None, tenant="local_dev", auth_tenant=None):
    """直接调用端点函数：mock 鉴权（本地放行）/ 草稿读取 / 物流报价。"""
    from routes import estimate_routes
    from services import estimate_service
    import main

    eff_auth_tenant = auth_tenant if auth_tenant is not None else tenant
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    # ⚠️ v0.56 余额改造后 main._authenticate_token 返回 user_{sha256[:16]} 派生租户，
    # 不再返回 local_dev——mock 固定返回测试租户，否则 _load_draft_payload 恒 404
    monkeypatch.setattr(
        estimate_routes, "_authenticate_token",
        lambda token: _auth_mock(token, eff_auth_tenant),
    )
    monkeypatch.setattr(
        estimate_routes, "_load_draft_payload",
        lambda did, tid: (payload if tid == tenant else None),
    )
    monkeypatch.setattr(
        estimate_service, "query_logistics_cost",
        lambda weight, depth_cm, width_cm, height_cm, *a, **k: (10.0, "mock_channel", {}),
    )
    return asyncio.run(estimate_routes.estimate_draft(draft_id, FakeRequest(body)))


# ── 1. CNY 草稿：compute_price 与 pricing_node 公式 parity（数值精确锁定）──
def test_cny_draft_parity_with_pricing_formula(monkeypatch):
    result = _call_endpoint(
        "11111111-1111-1111-1111-111111111111",
        {"token": "sk-x"},
        monkeypatch,
        payload=copy.deepcopy(VALID_ENVELOPE),
    )
    for key, expected in EXPECTED_CNY.items():
        assert result[key] == expected, f"{key}: 期望 {expected}，实际 {result[key]}"
    # 任务 2.3: 显式佣金（信封 commission_rate=0.10）→ source=explicit，rate=0.10
    assert result["commission_rate"] == 0.10
    assert result["commission_source"] == "explicit"


# ── 2. RUB 草稿（exchange_rate 有值）→ price 用汇率换算 ──
def test_rub_draft_uses_exchange_rate(monkeypatch):
    result = _call_endpoint(
        "11111111-1111-1111-1111-111111111111",
        {"token": "sk-x", "currency_code": "RUB", "exchange_rate": 12.0},
        monkeypatch,
        payload=copy.deepcopy(VALID_ENVELOPE),
    )
    for key, expected in EXPECTED_RUB.items():
        assert result[key] == expected, f"{key}: 期望 {expected}，实际 {result[key]}"


# ── 3. 草稿不存在 / 跨租户 → 404 ──
def test_draft_not_found_404(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _call_endpoint(
            "11111111-1111-1111-1111-111111111111",
            {"token": "sk-x"},
            monkeypatch,
            payload=None,
        )
    assert ei.value.status_code == 404


def test_cross_tenant_404(monkeypatch):
    """A 租户 token 访问 B 租户草稿 → 404（租户隔离）。"""
    with pytest.raises(HTTPException) as ei:
        _call_endpoint(
            "11111111-1111-1111-1111-111111111111",
            {"token": "sk-x"},
            monkeypatch,
            payload=copy.deepcopy(VALID_ENVELOPE),
            tenant="other-tenant",
            auth_tenant="local_dev",
        )
    assert ei.value.status_code == 404


# ── 4. 无 token → 401 ──
def test_no_token_401(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _call_endpoint(
            "11111111-1111-1111-1111-111111111111",
            {"token": ""},
            monkeypatch,
            payload=copy.deepcopy(VALID_ENVELOPE),
        )
    assert ei.value.status_code == 401


# ── 5. 防漂移锁定：pricing_node 必须引用 compute_price（公式单处定义）──
def test_pricing_node_references_compute_price():
    import inspect
    from graphs.nodes import pricing_node as pn

    src = inspect.getsource(pn)
    assert "compute_price" in src, "pricing_node 主定价路径必须调用 utils.pricing_estimate.compute_price"


# ── 6. compute_price 纯函数：exchange_rate=None → 按 CNY 处理 ──
def test_compute_price_none_rate_treated_as_cny():
    from utils.pricing_estimate import compute_price

    result = compute_price(17.5, 0.25, 0.10, 0.05, "RUB", None)
    for key, expected in EXPECTED_CNY.items():
        if key in ("logistics_cost_cny", "currency"):
            continue
        assert result[key] == expected, f"{key}: 期望 {expected}，实际 {result[key]}"


def test_compute_price_rub_uses_fx_and_rate():
    from utils.pricing_estimate import compute_price

    result = compute_price(17.5, 0.25, 0.10, 0.05, "RUB", 12.0)
    for key, expected in EXPECTED_RUB.items():
        if key in ("logistics_cost_cny", "currency"):
            continue
        assert result[key] == expected, f"{key}: 期望 {expected}，实际 {result[key]}"


# ── 7. 只读：端点不修改草稿 payload ──
def test_payload_readonly(monkeypatch):
    before = copy.deepcopy(VALID_ENVELOPE)
    result = _call_endpoint(
        "11111111-1111-1111-1111-111111111111",
        {"token": "sk-x"},
        monkeypatch,
        payload=copy.deepcopy(VALID_ENVELOPE),
    )
    assert result["price"] > 0
    assert VALID_ENVELOPE == before, "端点不得修改 draft payload（纯读派生数据）"


# ── 8. 任务 2.3: 真实类目佣金（mock 缓存表分段 → cache:{band}）──
def test_estimate_returns_commission_source(monkeypatch):
    """无显式佣金 + 类目佣金缓存命中 → 响应含 commission_rate + commission_source。

    Given: 信封无 commission_rate（extensions 移除）+ draft.ozon_category 指定类目
    When:  mock get_category_commission 返回分段（fbs_leq_1500=8.0%），调用 estimate
    Then:  响应 commission_rate=0.08, commission_source="cache:leq_1500"
           （CNY 无汇率 → 售价段走保守 leq_1500，与 pricing_node 同源解析链）
    """
    from services import estimate_service

    envelope = copy.deepcopy(VALID_ENVELOPE)
    envelope["extensions"].pop("commission_rate", None)
    envelope["draft"]["ozon_category"] = {
        "description_category_id": "17028929",
        "type_id": "504866264",
    }
    monkeypatch.setattr(
        estimate_service,
        "get_category_commission",
        lambda dc_id: {
            "fbs_leq_1500": 8.0,
            "fbs_leq_5000": 10.5,
            "fbs_gt_5000": 12.0,
            "fbo_leq_1500": 7.0,
            "fbo_leq_5000": 9.0,
            "fbo_gt_5000": 11.0,
            "source": "what_to_sell",
        },
    )
    result = _call_endpoint(
        "11111111-1111-1111-1111-111111111111",
        {"token": "sk-x"},
        monkeypatch,
        payload=envelope,
    )
    assert result["commission_source"] == "cache:leq_1500"
    assert result["commission_rate"] == 0.08


# ══════════════════════════════════════════════════════════════════
# v0.60 三档双价格体系（estimate 服务接入）：TDD RED→GREEN
# ══════════════════════════════════════════════════════════════════
# 三档手算（total_cost=5.5+10.0+2.0=17.5，佣金 0.10 显式）：
#   日常 price = ceil(17.5×2.5/(1-0.10-0.155)) = ceil(43.75/0.745) = ceil(58.72) = 59
#   划线 old   = ceil(17.5×3.0/0.745) = ceil(70.47) = 71
#   促销 promo = ceil(17.5×1.6/(1-0.10-0.245)) = ceil(28.0/0.655) = ceil(42.75) = 43
# 净利（销售净利率口径）= price×(1-0.10-0.155)-17.5 = 59×0.745-17.5 = 26.455
#   → compute_price 实际返回 round(26.4549…,2)=26.45 / round(26.455/59,4)=0.4484
EXPECTED_THREE_TIER = {
    "price": 59,
    "old_price": 71,
    "promo_price": 43,
    "profit_cny": 26.45,
    "profit_rate": 0.4484,
    "logistics_cost_cny": 10.0,
    "currency": "CNY",
    "commission_rate": 0.10,
    "commission_source": "explicit",
    "margin_anchor": 2.0,
    "margin_floor": 0.6,
    "variable_cost_rate": 0.155,
}

THREE_TIER_OVERRIDES = {"margin_rate": 1.5, "margin_floor": 0.6, "margin_anchor": 2.0}

THREE_TIER_ENVELOPE = copy.deepcopy(VALID_ENVELOPE)
THREE_TIER_ENVELOPE["extensions"].update(
    {"margin_rate": 1.5, "margin_floor": 0.6, "margin_anchor": 2.0}
)


def _assert_three_tier(result):
    for key, expected in EXPECTED_THREE_TIER.items():
        assert result[key] == expected, f"{key}: 期望 {expected}，实际 {result[key]}"


# ── 9. 请求体三档覆盖（margin_rate+margin_floor+margin_anchor）→ 日常/划线/促销 ──
def test_three_tier_via_request_overrides(monkeypatch):
    body = {"token": "sk-x", **THREE_TIER_OVERRIDES}
    result = _call_endpoint(
        "11111111-1111-1111-1111-111111111111",
        body,
        monkeypatch,
        payload=copy.deepcopy(VALID_ENVELOPE),
    )
    _assert_three_tier(result)


# ── 10. 无 margin_floor → 旧单档行为（无 promo_price 键，price=25 等既有断言）──
def test_legacy_single_tier_when_no_margin_floor(monkeypatch):
    result = _call_endpoint(
        "11111111-1111-1111-1111-111111111111",
        {"token": "sk-x"},
        monkeypatch,
        payload=copy.deepcopy(VALID_ENVELOPE),
    )
    assert "promo_price" not in result, "无 margin_floor 时不得出现 promo_price 键"
    assert "margin_anchor" not in result, "旧行为不返回三档配置键"
    assert result["price"] == EXPECTED_CNY["price"] == 25
    assert result["old_price"] == EXPECTED_CNY["old_price"] == 30


# ── 11. extensions 里带三档参数（不传请求覆盖）→ 同样生效 ──
def test_three_tier_via_extensions(monkeypatch):
    result = _call_endpoint(
        "11111111-1111-1111-1111-111111111111",
        {"token": "sk-x"},
        monkeypatch,
        payload=copy.deepcopy(THREE_TIER_ENVELOPE),
    )
    _assert_three_tier(result)
