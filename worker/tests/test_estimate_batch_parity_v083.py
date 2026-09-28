"""v0.83 批① 黄金对账：pricing_node ≡ estimate_service ≡ /estimate/batch 逐字段相等。

依据 docs/PLAN-v083-quality-campaign-v1.md §3 批①「黄金对账」：
- 骨架复用 test_pricing_alignment_v080 的 mock 组（物流/汇率/佣金缓存表到同组值）；
- 三处消费同一 utils/pricing_core 后，price/old_price/promo_price/profit/commission_rate/
  commission_source 必须逐字段相等（pricing_node 的 commission_source 经包装 core 捕获
  audit_out——上传面 freshness 语义下不落 pricing_info，故包装取审计值）；
- 一组零业务 mock 的精确数值断言（先例 test_estimate_endpoint EXPECTED_CNY/RUB）；
- 契约锁：batch 恒 estimate_source=="worker"；dc=None+segments 命中时
  commission_source.startswith("segments")。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_estimate_batch_parity_v083.py -q
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from api.schemas import EstimateBatchIn
from graphs.nodes import pricing_node as pn
from routes import estimate_routes
from services import estimate_service
from utils import pricing_core as pc


# ── 同组 mock：物流/汇率/佣金缓存表（与 test_pricing_alignment_v080._mock_heavy 同款）──

_BAND_CACHE_ROW = {
    "fbs_leq_1500": 8.0,
    "fbs_leq_5000": 25.0,
    "fbs_gt_5000": 30.0,
    "source": "what_to_sell",
    "updated_at": time.time(),
}

_DUAL_EXT = {"margin_rate": 0.25, "margin_anchor": 2.0}

_DRAFT = {"cost_cny": 68.0, "weight": 227, "dimensions": {"length": 120, "width": 80, "height": 60}}
_PURCHASE_COST = 68.0
_DC = "17028830"
_RATE = 12.0


class _DummyRuntime:
    class _DummyContext:
        pass

    context = _DummyContext()


def _make_state(draft=None, extensions=None, currency="RUB", dc_id=_DC):
    return SimpleNamespace(
        draft=draft or dict(_DRAFT),
        extensions=extensions if extensions is not None else dict(_DUAL_EXT),
        supabase_url="http://supabase.local",
        supabase_key="k",
        currency_code=currency,
        ozon_client_id="1",
        ozon_api_key="k",
        description_category_id=dc_id,
        task_id="",
        tenant_id="",
        token="sk-test",
        envelope=None,
        variants=None,
    )


def _envelope(draft=None, extensions=None):
    return {
        "draft": draft or dict(_DRAFT),
        "extensions": extensions if extensions is not None else dict(_DUAL_EXT),
    }


def _mock_heavy(monkeypatch, cat_commission=_BAND_CACHE_ROW, logistics_cost=10.0, rate=_RATE):
    """同时 mock 三处重依赖：物流（三处）/汇率（节点+fx 链）/佣金缓存表（节点+estimate+batch）。"""
    from utils import fx_rate_service, logistics_quote

    monkeypatch.setattr(
        logistics_quote, "query_logistics_cost",
        lambda *a, **k: (logistics_cost, "mock_channel", {}),
    )
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config", lambda *a, **k: ("RETS", "Standard"),
    )
    # 节点侧汇率（float 契约 mock）
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: rate, raising=False)
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: cat_commission, raising=False)
    # estimate/batch 侧：物流 + 佣金（模块属性注入点）+ fx 三级链
    monkeypatch.setattr(
        estimate_service, "query_logistics_cost",
        lambda *a, **k: (logistics_cost, "mock_channel", {}),
    )
    monkeypatch.setattr(estimate_service, "get_category_commission", lambda *a, **k: cat_commission)
    monkeypatch.setattr(
        fx_rate_service, "resolve_cny_rub_rate", lambda: (rate, "pg_cache"),
    )


def _capture_node_audit(monkeypatch):
    """包装 pricing_core.compute_pricing_core，捕获 pricing_node 的 audit_out。"""
    captured: dict = {}
    orig = pc.compute_pricing_core

    def _wrap(*a, **k):
        res = orig(*a, **k)
        audit = k.get("audit_out")
        if isinstance(audit, dict):
            captured.update(audit)
        return res

    monkeypatch.setattr(pc, "compute_pricing_core", _wrap)
    return captured


def _batch_item(currency="RUB", cost=_PURCHASE_COST, dc=_DC, segments=None):
    return {
        "purchase_cost": cost,
        "weight_g": 227,
        "dims_mm": {"length": 120, "width": 80, "height": 60},
        "currency_code": currency,
        "commission_segments": segments,
        "dc": dc,
    }


def _run_batch(items):
    body = EstimateBatchIn.model_validate({"items": items})
    return estimate_routes._run_estimate_batch(body, "tenant-A")


# ── 1. 三处逐字段相等（RUB 三档默认口径，dc+缓存分段命中 cache:leq_5000）──
# ⚠️ batch 请求体（PLAN §3 批①）不含 margin 字段 → 恒 worker 默认三档
# （margin 1.5 / anchor 2.0 / floor 0.6）；故此用例取 extensions={}
# （未配置 margin 的店，node/estimate 亦按 is_dual_margin 默认三档）三处才同源。

def test_three_way_parity(monkeypatch):
    _mock_heavy(monkeypatch)
    audit = _capture_node_audit(monkeypatch)

    out = pn.pricing_node(_make_state(extensions={}), None, _DummyRuntime())
    assert out.error_message == "", f"pricing_node 不应失败: {out.error_message}"
    pi = out.pricing_info

    est = estimate_service.estimate_from_envelope(_envelope(extensions={}), currency_code="RUB")
    batch = _run_batch([_batch_item()])
    assert batch["failed"] == [], batch["failed"]
    b0 = batch["items"][0]

    # 逐字段相等（pricing_node price int vs estimate int）
    assert est["price"] == pi["price"], f"estimate({est['price']}) vs graph({pi['price']})"
    assert b0["price"] == pi["price"], f"batch({b0['price']}) vs graph({pi['price']})"
    assert est["old_price"] == pi["old_price"] == b0["old_price"], "old_price 三处分叉"
    assert est["promo_price"] == pi["promo_price"] == b0["promo_price"], "promo_price 三处分叉"
    assert est["profit_cny"] == pi["profit_estimation"]["profit_cny"] == b0["profit_cny"], "profit 三处分叉"
    assert est["profit_rate"] == pi["profit_estimation"]["profit_rate"] == b0["profit_rate"], "profit_rate 三处分叉"
    assert est["commission_rate"] == pi["commission_rate"] == b0["commission_rate"], "commission_rate 三处分叉"
    # commission_source：节点经 audit 捕获（freshness 语义下不落 pricing_info）
    assert (
        audit["commission_source"] == est["commission_source"] == b0["commission_source"]
        == "cache:leq_5000"
    ), f"commission_source 三处分叉: {audit.get('commission_source')} / {est['commission_source']} / {b0['commission_source']}"

    # 契约锁：batch 恒 estimate_source=="worker"
    assert b0["estimate_source"] == "worker"
    assert b0["ok"] is True
    assert b0["index"] == 0


# ── 1b. 配置了 margin 的店：pricing_node ≡ estimate_service（信封同源）──
# batch 无 margin 入参（PLAN schema），配置店的上架/展示预估走 /estimate(envelope)，
# 此处锁定两端同源（否则配置店 graph 价与展示价漂移）。

def test_node_estimate_parity_configured_margin(monkeypatch):
    _mock_heavy(monkeypatch)
    out = pn.pricing_node(_make_state(extensions=dict(_DUAL_EXT)), None, _DummyRuntime())
    assert out.error_message == ""
    pi = out.pricing_info
    est = estimate_service.estimate_from_envelope(_envelope(extensions=dict(_DUAL_EXT)), currency_code="RUB")
    assert est["price"] == pi["price"]
    assert est["old_price"] == pi["old_price"]
    assert est["promo_price"] == pi["promo_price"]
    assert est["profit_cny"] == pi["profit_estimation"]["profit_cny"]
    assert est["commission_rate"] == pi["commission_rate"]


# ── 2. 零业务 mock 精确数值（三档默认口径，独立手算期望；先例 EXPECTED_THREE_TIER）──
# total_cost = 5.5(采购) + 10.0(mock物流) + 2.0(包装) = 17.5
# batch 无 margin 入参 → worker 默认三档（margin 1.5 / anchor 2.0 / floor 0.6 /
# vcr 0.155 / pvcr 0.245）；佣金 fallback 0.10（无 dc/segments/缓存行）:
#   price  = ceil(17.5 × 2.5 / (1-0.10-0.155)) = ceil(58.72) = 59
#   old    = max(ceil(17.5×3.0/0.745)=71, enforce(59)=max(71,79)=79) = 79
#   promo  = ceil(17.5 × 1.6 / (1-0.10-0.245)) = ceil(42.75) = 43
#   profit = 59×0.745 - 17.5 = 26.455 → 26.45 ; rate = 26.455/59 = 0.4484
def test_batch_exact_numbers_three_tier(monkeypatch):
    _mock_heavy(monkeypatch, cat_commission=None)  # 无缓存行 → fallback 0.10
    item = _batch_item(currency="CNY", cost=5.5, dc=None)
    item["commission_segments"] = None
    item["attributes"] = None
    res = _run_batch([item])
    assert res["failed"] == [], res["failed"]
    b = res["items"][0]
    assert b["price"] == 59, b["price"]
    assert b["old_price"] == 79, b["old_price"]
    assert b["promo_price"] == 43, b["promo_price"]
    assert b["profit_cny"] == pytest.approx(26.45)
    assert b["profit_rate"] == pytest.approx(0.4484)
    assert b["commission_source"] == "fallback", b["commission_source"]  # 无 dc/segments → fallback 0.10
    assert b["commission_rate"] == pytest.approx(0.10)
    assert b["logistics_source"] == "default_rets"
    assert b["estimate_source"] == "worker"


# ── 3. 契约锁：dc=None + segments 命中 → commission_source.startswith("segments") ──

def test_batch_dc_none_segments_source(monkeypatch):
    _mock_heavy(monkeypatch, cat_commission=None)  # 无缓存行 → 走 segments
    item = _batch_item(
        currency="CNY", cost=20.0, dc=None,
        segments={"fbs": {"leq_1500": 8.0, "leq_5000": 10.5, "gt_5000": 12.0}},
    )
    res = _run_batch([item])
    assert res["failed"] == [], res["failed"]
    b = res["items"][0]
    assert b["commission_source"].startswith("segments"), b["commission_source"]
    assert b["commission_rate"] == pytest.approx(0.105)


def test_batch_fbo_segments_fallback(monkeypatch):
    """fbs 缺失、仅 fbo 分段 → 回退 fbo（实锤 C），source 带 :fbo 后缀。"""
    _mock_heavy(monkeypatch, cat_commission=None)
    item = _batch_item(
        currency="CNY", cost=20.0, dc=None,
        segments={"fbo": {"leq_1500": 7.0, "leq_5000": 9.0, "gt_5000": 11.0}},
    )
    res = _run_batch([item])
    b = res["items"][0]
    assert b["commission_source"] == "segments:leq_5000:fbo", b["commission_source"]
    assert b["commission_rate"] == pytest.approx(0.09)


# ── 4. 部分失败不整体 4xx：坏项落 failed，好项仍 ok ──

def test_batch_partial_failure(monkeypatch):
    _mock_heavy(monkeypatch)

    bad = _batch_item(currency="CNY", cost=10.0)
    with monkeypatch.context() as m:
        # 让 estimate 对该 item 抛错（模拟内部异常）
        calls = {"n": 0}
        orig = estimate_service.estimate_from_envelope

        def _maybe_boom(env, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return orig(env, **k)

        m.setattr(estimate_service, "estimate_from_envelope", _maybe_boom)
        res = _run_batch([bad, _batch_item(currency="CNY", cost=12.0)])
    assert res["failed"] and res["failed"][0]["index"] == 0
    assert res["items"] and res["items"][0]["index"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. v0.83 gate 批①：envelope 形态凭证 → 物流与 pricing_node 同源
#    （预估↔卡价 +5.9% 遗留：envelope 此前不支持 credential → 恒走默认 RETS ¥7.52，
#     节点走店铺 3PL ¥6.76）。本组覆盖：
#     - envelope 带 ozon_client_id（skill 侧 client_id）→ worker 反查凭证 → store 3PL；
#     - envelope 带 credential_id（worker 内部 UUID，batch 同名字段）→ store 3PL；
#     - envelope 无凭证 / 有 client_id 但无 tenant → default_rets 且标注不变。
# ═══════════════════════════════════════════════════════════════════════════

_STORE_TPL = ("Yandex", "Express")
_STORE_COST = 6.76
_DEFAULT_COST = 7.52


def _mock_store_logistics(monkeypatch, store_tpl=_STORE_TPL):
    """pricing_node 与 estimate 双路物流同源 mock：店铺 3PL 命中 → ¥6.76，缺省 → ¥7.52。

    覆盖 ``_mock_heavy`` 的常量物流 mock（后者把三处压成同值，测不出 store/default 分叉）。
    """
    from services import credential_service
    from utils import logistics_quote

    def _cost(weight, depth, width, height, tpl="RETS", svc="Standard"):
        return (_STORE_COST if tpl == store_tpl[0] else _DEFAULT_COST, "mock_channel", {"tpl": tpl})

    monkeypatch.setattr(logistics_quote, "get_store_logistics_config", lambda *a, **k: store_tpl)
    monkeypatch.setattr(estimate_service, "get_store_logistics_config", lambda *a, **k: store_tpl)
    monkeypatch.setattr(logistics_quote, "query_logistics_cost", _cost)
    monkeypatch.setattr(estimate_service, "query_logistics_cost", _cost)
    # 凭证反查/解密：ozon_client_id="4718259" 命中（其余 miss → None）
    monkeypatch.setattr(
        credential_service, "find_credential_id_by_client",
        lambda tenant, client: "cred-1" if client == "4718259" else None,
    )
    monkeypatch.setattr(
        credential_service, "get_decrypted", lambda tenant, cid: ("4718259", "k")
    )


def test_envelope_with_ozon_client_id_parity_with_pricing_node(monkeypatch):
    """envelope.extensions.ozon_client_id → worker 反查凭证 → 物流/价格与 pricing_node 逐字段相等。"""
    _mock_heavy(monkeypatch)
    _mock_store_logistics(monkeypatch)

    out = pn.pricing_node(_make_state(extensions={}), None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    pi = out.pricing_info

    est = estimate_service.estimate_from_envelope(
        _envelope(extensions={"ozon_client_id": "4718259"}),
        currency_code="RUB", tenant_id="tenant-A",
    )
    assert est["logistics_source"] == "store"
    assert est["logistics_cost_cny"] == _STORE_COST
    # 物流项 + 价格逐字段与 pricing_node 相等（本批验收：预估↔卡价 ±3%）
    assert est["logistics_cost_cny"] == pi["logistics_cost_cny"]
    assert est["price"] == pi["price"], f"estimate({est['price']}) vs graph({pi['price']})"
    assert est["old_price"] == pi["old_price"]
    assert est["promo_price"] == pi["promo_price"]
    assert est["profit_cny"] == pi["profit_estimation"]["profit_cny"]
    assert est["profit_rate"] == pi["profit_estimation"]["profit_rate"]


def test_envelope_with_credential_id_direct_uses_store(monkeypatch):
    """envelope.extensions.credential_id（worker 内部 UUID，batch 同名字段）直接生效。"""
    _mock_heavy(monkeypatch)
    _mock_store_logistics(monkeypatch)

    est = estimate_service.estimate_from_envelope(
        _envelope(extensions={"credential_id": "cred-1"}),
        currency_code="RUB", tenant_id="tenant-A",
    )
    assert est["logistics_source"] == "store"
    assert est["logistics_cost_cny"] == _STORE_COST


def test_envelope_without_credential_default_rets_unchanged(monkeypatch):
    """无凭证 / 有 client_id 但无 tenant → default_rets 回落，标注不变（回归锁）。"""
    _mock_heavy(monkeypatch)
    _mock_store_logistics(monkeypatch)

    # 无任何凭证线索
    est = estimate_service.estimate_from_envelope(_envelope(extensions={}), currency_code="RUB")
    assert est["logistics_source"] == "default_rets"
    assert est["logistics_cost_cny"] == _DEFAULT_COST

    # 带 ozon_client_id 但无 tenant → 不反查（避免越租户），仍 default_rets
    est2 = estimate_service.estimate_from_envelope(
        _envelope(extensions={"ozon_client_id": "4718259"}), currency_code="RUB",
    )
    assert est2["logistics_source"] == "default_rets"
    assert est2["logistics_cost_cny"] == _DEFAULT_COST


def test_envelope_unknown_client_id_falls_back_default(monkeypatch):
    """ozon_client_id 无对应凭证行 → 反查 None → default_rets（未知店铺不误探）。"""
    _mock_heavy(monkeypatch)
    _mock_store_logistics(monkeypatch)

    est = estimate_service.estimate_from_envelope(
        _envelope(extensions={"ozon_client_id": "no-such-client"}),
        currency_code="RUB", tenant_id="tenant-A",
    )
    assert est["logistics_source"] == "default_rets"
    assert est["logistics_cost_cny"] == _DEFAULT_COST


# ═══════════════════════════════════════════════════════════════════════════
# 6. v0.83.2 终态：重量链含体积重兜底 → 卡面/物流/定价三者同口径
#    PR #99（批①第二轮）曾把 estimate 向下对齐 pricing_node 的「未抬重」链（100g→价 17）；
#    但 prepare 上架链对低密度件按兜底后重量（121g）声明卡面并计费，定价按 100g 算 →
#    低密度单系统性少收运费差。用户拍板「价格按公式来」：三处统一开启体积兜底。
#    gate key-box（743614284287：100g / 96×70×45mm / margin 0.25 / commission 0.10）：
#    100g→121g（密度 0.331<0.40）→ 物流 3.12+0.0364×121=7.5244 → 价 18（修复前向下对齐 17）。
# ═══════════════════════════════════════════════════════════════════════════

_GATE_DRAFT = {
    "cost_cny": 3.37, "weight": 100,
    "dimensions": {"length": 96, "width": 70, "height": 45},
}


def _mock_weight_rate_logistics(monkeypatch):
    """重量相关物流 mock：cost = 3.12 + 0.0364×billable_weight（对齐 PG 费率行 RETS/Standard）。"""
    from services import credential_service
    from utils import logistics_quote

    def _cost(weight, depth_cm, width_cm, height_cm, tpl="RETS", svc="Standard"):
        return (3.12 + 0.0364 * float(weight), "mock_channel", {"weight": weight})

    monkeypatch.setattr(estimate_service, "query_logistics_cost", _cost)
    monkeypatch.setattr(estimate_service, "get_store_logistics_config", lambda *a, **k: ("RETS", "Standard"))
    monkeypatch.setattr(logistics_quote, "query_logistics_cost", _cost)
    monkeypatch.setattr(logistics_quote, "get_store_logistics_config", lambda *a, **k: ("RETS", "Standard"))
    monkeypatch.setattr(credential_service, "find_credential_id_by_client", lambda t, c: "cred-1")
    monkeypatch.setattr(credential_service, "get_decrypted", lambda t, c: ("4718259", "k"))


def test_envelope_weight_chain_matches_pricing_node(monkeypatch):
    """预估 == pricing_node：同一含体积兜底的重量链，物流项与价格逐字段相等（gate key-box）。"""
    _mock_heavy(monkeypatch)
    _mock_weight_rate_logistics(monkeypatch)

    state = _make_state(draft=dict(_GATE_DRAFT), extensions={"margin_rate": 0.25, "commission_rate": 0.10}, currency="CNY")
    out = pn.pricing_node(state, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    pi = out.pricing_info

    est = estimate_service.estimate_from_envelope(
        {"draft": dict(_GATE_DRAFT),
         "extensions": {"margin_rate": 0.25, "commission_rate": 0.10, "ozon_client_id": "4718259"}},
        currency_code="CNY", tenant_id="tenant-A",
    )

    # 100g→121g 兜底后计费 → 7.5244；两处逐字相等（estimate 响应物流费四舍五入到分）
    assert pi["logistics_cost_cny"] == pytest.approx(7.5244), pi["logistics_cost_cny"]
    assert est["logistics_cost_cny"] == round(pi["logistics_cost_cny"], 2)
    assert est["price"] == pi["price"] == 18, (est["price"], pi["price"])
    assert est["old_price"] == pi["old_price"]
    assert est["profit_cny"] == pi["profit_estimation"]["profit_cny"]


def test_envelope_volume_floor_applied_on_low_density(monkeypatch):
    """v0.83.2：低密度件兜底已触发（此前 PR #99 锁「不触发」，语义翻转）→ 按 121g 计费。

    本组 extensions 无 margin 键 → 三档默认（margin 1.5）+ 缓存佣金 25%：
    total = 3.37+7.5244+2 = 12.8944 → ceil(12.8944×2.5/0.595) = 55。
    """
    _mock_heavy(monkeypatch)
    _mock_weight_rate_logistics(monkeypatch)

    est = estimate_service.estimate_from_envelope(
        {"draft": dict(_GATE_DRAFT), "extensions": {"ozon_client_id": "4718259"}},
        currency_code="CNY", tenant_id="tenant-A",
    )
    assert est["logistics_cost_cny"] == pytest.approx(7.52)
    assert est["price"] == 55, est["price"]

