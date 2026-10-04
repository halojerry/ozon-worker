# -*- coding: utf-8 -*-
"""follow_clone 模式 v0.85 — worker 侧实现锁定（PLAN-follow-clone-v1 B2）。

B0 探针实录（2026-10-03，worker/scripts/probe_clone_card.py 四轮）驱动的回归：
- 信封新键 follow_clone / clone_card（envelope_contract 登记，extra=forbid 之内）；
- follow_sell_import：follow_type=clone 走 import-by-sku（官方复制）；不可复制 →
  clone_card 类目/属性逐字采（零字典解析零 LLM，模式本意）；
- route_after_assemble：follow_clone → 「克隆」（跳过生图链——零 LLM 零生图）；
- prepare 图覆写纯函数：UPDATE images=[] 铁锁（不动复制卡图）/ CREATE 回退 CDN 直传；
- 图片闸：对外链恒拒的**全局语义零变化**（回归锁）+ allow_competitor_cdn 模式
  作用域口仅放行 Ozon 自家 CDN 原尺寸图；
- pricing 锚价覆盖：draft.competitor_price（前 20 均值，恒 RUB，选品时物化）×
  factor（FOLLOW_CLONE_PRICE_FACTOR 缺省 1.0）；低于 core 底线价 → 如实拒。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_follow_clone_v085.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.graph import route_after_assemble  # noqa: E402
from utils.clone_card_builder import (  # noqa: E402
    build_clone_import_item,
    extract_clone_images,
    normalize_clone_attributes,
    validate_clone_card,
)
from utils.image_source import enforce_upload_policy  # noqa: E402

# ═══════════════════ 1. 信封契约 ═══════════════════


def test_01_envelope_new_keys_accepted():
    from utils.envelope_contract import EnvelopeExtensions

    m = EnvelopeExtensions(follow_clone=True,
                           clone_card={"product_id": "123", "dc": "1", "tp": "2"})
    assert m.follow_clone is True
    assert isinstance(m.clone_card, dict)


def test_02_contract_keys_registered():
    from utils.envelope_contract import EXTENSION_KEY_ORIGINS, _KEY_DESCRIPTIONS

    for k in ("follow_clone", "clone_card"):
        assert k in EXTENSION_KEY_ORIGINS, f"{k} 未登记来源标注"
        assert k in _KEY_DESCRIPTIONS, f"{k} 未登记描述"


# ═══════════════════ 2. clone_card_builder ═══════════════════

# B0-D 实录形状：v4 回显属性主键 id、dictionary_value_id snake_case
_V4_ATTRS = [
    {"id": 85, "complex_id": 0, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
    {"id": 9048, "complex_id": 0, "values": [{"dictionary_value_id": 0, "value": "630526852010"}]},
    {"id": 21841, "complex_id": 0, "values": [{"dictionary_value_id": 1, "value": "video"}]},
    {"id": None, "complex_id": 0, "values": [{"value": "垃圾"}]},
    {"id": 23171, "complex_id": 0, "values": []},
]
_CDN_IMG = "https://ir-20.ozone.ru/s3/multimedia-1-e/11833245914.jpg"


def test_11_normalize_maps_id_to_attribute_id():
    out, skipped = normalize_clone_attributes(_V4_ATTRS)
    ids = [a["attribute_id"] for a in out]
    assert 85 in ids and 9048 in ids
    assert 21841 not in ids, "媒体类属性必须跳过"
    assert 23171 not in ids, "空值属性必须跳过"
    assert skipped == {"media": 1, "no_id": 1, "no_values": 1}
    by_id = {a["attribute_id"]: a for a in out}
    assert by_id[85]["values"] == [{"dictionary_value_id": 126745801}]
    assert by_id[9048]["values"] == [{"value": "630526852010"}]


def test_12_extract_images_quality_line():
    urls = [_CDN_IMG,
            _CDN_IMG,  # 去重
            "https://ir-20.ozone.ru/s3/multimedia/x.webp",  # webp 恒拒
            "https://cdn.ozon.ru/s3/multimedia/wc120/1_300x300.jpg",  # 缩略恒拒
            "https://cbu01.alicdn.com/img/ibank/1.jpg"]  # 非 Ozon 域拒
    out = extract_clone_images(urls)
    assert out == [_CDN_IMG]


def test_13_validate_requires_name_dc_tp():
    ok, why = validate_clone_card({"name": "x", "dc": "10", "tp": "20"})
    assert ok, why
    ok, why = validate_clone_card({"dc": "10", "tp": "20"})
    assert not ok and "name" in why
    ok, why = validate_clone_card({"name": "x", "dc": "abc", "tp": "20"})
    assert not ok and "dc" in why


def test_14_build_item_full_shape():
    cc = {"name": "Рыбацкая шляпа", "dc": "41777465", "tp": "93546",
          "attributes": _V4_ATTRS, "images": [_CDN_IMG],
          "weight_g": 120, "depth_mm": 300, "width_mm": 280, "height_mm": 50}
    item = build_clone_import_item(cc, offer_id="probe-1", price=22, old_price=29)
    assert item["name"] == "Рыбацкая шляпа"
    assert item["description_category_id"] == 41777465
    assert item["type_id"] == "93546"
    assert item["images"] == [_CDN_IMG]
    assert item["weight"] == 120 and item["depth"] == 300
    assert item["old_price"] == "29"
    assert {a["attribute_id"] for a in item["attributes"]} == {85, 9048}


def test_15_build_item_rejects_empty_attrs():
    cc = {"name": "x", "dc": "1", "tp": "2", "attributes": [], "images": []}
    assert build_clone_import_item(cc, offer_id="o", price=5) is None


# ═══════════════════ 3. 图片闸模式作用域 ═══════════════════


def test_21_policy_global_gate_still_rejects_cdn():
    """回归锁：不传 allow_competitor_cdn，Ozon CDN 外链恒拒（全局语义零变化）。"""
    ok, violations = enforce_upload_policy([_CDN_IMG])
    assert not ok and any("external" in v for v in violations)


def test_22_policy_clone_lane_allows_cdn_original_only():
    ok, _ = enforce_upload_policy([_CDN_IMG], allow_competitor_cdn=True)
    assert ok
    # webp/缩略/非 Ozon 域即便开模式口也恒拒
    ok, violations = enforce_upload_policy(
        ["https://ir-20.ozone.ru/s3/multimedia/x.webp",
         "https://cbu01.alicdn.com/img/ibank/1.jpg"],
        allow_competitor_cdn=True)
    assert not ok and len(violations) == 2


# ═══════════════════ 4. prepare 图覆写纯函数 ═══════════════════


def test_31_apply_images_update_lock():
    from graphs.nodes.prepare_ozon_upload_node import _apply_follow_clone_images
    payload = {"items": [{"offer_id": "1", "product_id": 6515871405,
                          "images": ["https://cos/img.jpg"], "primary_image": "https://cos/p.jpg"}]}
    mode = _apply_follow_clone_images(payload, {"follow_clone": True})
    assert mode == "update"
    assert payload["items"][0]["images"] == [], "UPDATE 铁锁：绝不动复制卡图"
    assert "primary_image" not in payload["items"][0]


def test_32_apply_images_create_fallback_cdn():
    from graphs.nodes.prepare_ozon_upload_node import _apply_follow_clone_images
    payload = {"items": [{"offer_id": "1", "images": []}]}
    mode = _apply_follow_clone_images(
        payload, {"follow_clone": True, "clone_card": {"images": [_CDN_IMG]}})
    assert mode == "create"
    assert payload["items"][0]["images"] == [_CDN_IMG]


def test_33_apply_images_skip_when_not_clone():
    from graphs.nodes.prepare_ozon_upload_node import _apply_follow_clone_images
    payload = {"items": [{"offer_id": "1", "images": ["https://cos/a.jpg"]}]}
    assert _apply_follow_clone_images(payload, {}) == "skip"
    assert payload["items"][0]["images"] == ["https://cos/a.jpg"], "非 clone 载荷零动作"


# ═══════════════════ 5. 路由 ═══════════════════


def _route_state(**kw):
    base = {"failed_stage": "", "error_message": "", "match_confidence": 1.0,
            "envelope": {}}
    base.update(kw)
    return SimpleNamespace(**base)


def test_41_route_clone_branch():
    st = _route_state(envelope={"extensions": {"follow_clone": True}})
    assert route_after_assemble(st) == "克隆"


def test_42_route_gate_priority_over_clone():
    """低置信/类目阻断优先于 clone 分支——质量闸永不因模式让路。"""
    assert route_after_assemble(_route_state(
        match_confidence=0.0,
        envelope={"extensions": {"follow_clone": True}})) == "失败"
    assert route_after_assemble(_route_state(
        failed_stage="category_match",
        envelope={"extensions": {"follow_clone": True}})) == "失败"


def test_43_route_no_marker_unaffected():
    assert route_after_assemble(_route_state()) == "成功"
    assert route_after_assemble(
        _route_state(envelope={"extensions": {"follow_sell": True}})) == "成功", \
        "follow_sell（非 clone）仍走生图链——现状语义零变化"


# ═══════════════════ 6. follow_sell_import clone 模式 ═══════════════════

_CLONE_CARD = {
    "product_id": "2062059459",
    "name": "Организатор",
    "dc": "92417201",
    "tp": "92532",
    "attributes": [
        {"id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
        {"id": 4191, "values": [{"value": "Организатор для хранения"}]},
    ],
    "images": [_CDN_IMG],
    "weight_g": 300,
}


class _FakeState:
    def __init__(self, envelope):
        self.envelope = envelope
        self.ozon_client_id = "1"
        self.ozon_api_key = "k"
        self.currency_code = "CNY"
        self.token = "sk-t"


class _PostRecorder:
    def __init__(self, unmatched=False, imported=False):
        self.calls = []
        self.unmatched = unmatched
        self.imported = imported

    def __call__(self, client_id, api_key, endpoint, body=None, timeout=30, **kw):
        self.calls.append(endpoint)
        if "import-by-sku" in endpoint:
            return {"result": {"task_id": "7",
                               "unmatched_sku_list": [111] if self.unmatched else []}}
        if "import/info" in endpoint:
            items = [{"product_id": 999888777, "status": "imported"}] if self.imported else []
            return {"result": {"items": items}}
        if "product/info/list" in endpoint:
            return {"items": [{"id": 999888777, "description_category_id": 17027933,
                               "type_id": 970742618}]}
        if "product/info/attributes" in endpoint:
            return {"result": []}
        return {}


def _run_follow(envelope, recorder):
    import graphs.nodes.follow_sell_import_node as fsin
    import utils.attr_defaults as attr_defaults_mod
    import utils.category_mapping_learn as cml_mod
    import utils.ozon_category_query as ocq_mod

    with mock.patch.object(ocq_mod, "get_category_query", return_value=None), \
         mock.patch.object(fsin, "_verify_category_schema", return_value=False), \
         mock.patch.object(fsin, "_resolve_category_by_id", return_value=("", "")), \
         mock.patch.object(fsin, "_gated_category_arbitration", return_value=("", "")), \
         mock.patch.object(cml_mod, "lookup_mapping", return_value=None), \
         mock.patch.object(attr_defaults_mod, "build_follow_attr_merge",
                           return_value=[]), \
         mock.patch("time.sleep", lambda s: None), \
         mock.patch.object(fsin, "ozon_post", recorder):
        return fsin.follow_sell_import_node(_FakeState(envelope))


def _clone_envelope(clone_card=True):
    draft = {
        "ozon_product_id": "2062059459",
        "title": "Организатор",
        "images": [],
        "ozon_category": {},
        "competitor_price": "240.00",
        "purchase_cost": 15.0,
        "purchase_url": "https://detail.1688.com/offer/9.html",
        "item_id": "9",
    }
    ext = {"follow_sell": True, "follow_type": "clone", "follow_clone": True}
    if clone_card:
        ext["clone_card"] = _CLONE_CARD
    return {"draft": draft, "extensions": ext}


def test_51_follow_clone_uses_import_by_sku_and_succeeds():
    rec = _PostRecorder(unmatched=False, imported=True)
    result = _run_follow(_clone_envelope(), rec)
    assert "/v1/product/import-by-sku" in rec.calls, "clone 必须走官方复制通道"
    assert str(result.get("product_id")) == "999888777"
    # 复制成功 → 类目随官方复制带出（反查回填 17027933）
    assert result.get("description_category_id") == "17027933"


def test_52_follow_clone_fallback_adopts_clone_card():
    rec = _PostRecorder(unmatched=True)
    result = _run_follow(_clone_envelope(), rec)
    assert not result.get("product_id"), "不可复制 → 无 product_id（走 CREATE 回退）"
    assert result.get("description_category_id") == "92417201", "clone_card dc 逐字采"
    assert result.get("type_id") == "92532"
    attrs = result.get("final_attributes") or []
    by_id = {a["id"]: a for a in attrs}
    assert 85 in by_id and 4191 in by_id, "属性逐字透传（含 4191 俄语描述）"
    assert by_id[4191]["values"][0]["value"] == "Организатор для хранения"


def test_53_follow_clone_fallback_without_clone_card_no_magic_category():
    """无 clone_card → 不采类目（绝不编造），走既有失败链（诚实失败）。"""
    rec = _PostRecorder(unmatched=True)
    result = _run_follow(_clone_envelope(clone_card=False), rec)
    assert "类目解析失败" in str(result.get("error_message") or "") or \
        not result.get("description_category_id")


# ═══════════════════ 7. pricing 锚价覆盖 ═══════════════════


class _DummyRuntime:
    class _DummyContext:
        pass

    context = _DummyContext()


def _pricing_state(extensions, competitor_price):
    from types import SimpleNamespace as NS
    return NS(
        draft={"cost_cny": 5.5, "purchase_cost": 5.5, "weight": 227,
               "dimensions": {"length": 120, "width": 80, "height": 60},
               "competitor_price": competitor_price},
        extensions=extensions,
        supabase_url="http://supabase.local", supabase_key="k",
        currency_code="CNY", ozon_client_id="1", ozon_api_key="k",
        description_category_id="17028830", task_id="t", tenant_id="tn",
        user_id="tn", token="sk", envelope={}, variants=[],
    )


def _patch_pricing(monkeypatch):
    from graphs.nodes import pricing_node as pn
    from utils import logistics_quote

    monkeypatch.setattr(logistics_quote, "query_logistics_cost",
                        lambda *a, **k: (10.0, "mock_channel", {}))
    monkeypatch.setattr(logistics_quote, "get_store_logistics_config",
                        lambda *a, **k: ("RETS", "Standard"))
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None)
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: 12.0)
    return pn


def test_61_pricing_anchor_override_default_factor(monkeypatch):
    """锚价 1200 RUB ÷ 12（CNY 店汇率）×1.0 → 100 CNY；来源审计键齐。"""
    pn = _patch_pricing(monkeypatch)
    st = _pricing_state({"currency_code": "CNY", "follow_clone": True}, "1200.00")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert out.pricing_info["price_source"] == "follow_clone_anchor"
    assert int(out.price) == 100, out.price
    assert out.pricing_info["anchor_price_rub"] == 1200
    assert int(out.old_price) >= 100


def test_62_pricing_anchor_below_floor_rejected(monkeypatch):
    """锚价 24 RUB → 2 CNY < core 底线价 → LOCAL_PRICING_FAILED（利润闸，宁缺毋滥）。"""
    pn = _patch_pricing(monkeypatch)
    st = _pricing_state({"currency_code": "CNY", "follow_clone": True}, "24.00")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_code == "LOCAL_PRICING_FAILED"
    assert "follow_clone" in out.error_message
    assert out.price == ""


def test_63_pricing_factor_env(monkeypatch):
    """FOLLOW_CLONE_PRICE_FACTOR=1.2 → 1200×1.2÷12 = 120 CNY。"""
    pn = _patch_pricing(monkeypatch)
    monkeypatch.setenv("FOLLOW_CLONE_PRICE_FACTOR", "1.2")
    st = _pricing_state({"currency_code": "CNY", "follow_clone": True}, "1200.00")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert int(out.price) == 120


def test_64_pricing_no_marker_untouched(monkeypatch):
    """非 clone 信封即便带 competitor_price 也不覆盖（三档主链语义零变化）。"""
    pn = _patch_pricing(monkeypatch)
    st = _pricing_state({"currency_code": "CNY"}, "1200.00")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert "price_source" not in out.pricing_info or \
        out.pricing_info.get("price_source") != "follow_clone_anchor"


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn.__wrapped__ if hasattr(fn, "__wrapped__") else None
        except Exception:
            pass
    import inspect
    for name, fn in fns:
        needs_mp = "monkeypatch" in inspect.signature(fn).parameters
        if needs_mp:
            print(f"SKIP(pytest-only) {name}")
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
