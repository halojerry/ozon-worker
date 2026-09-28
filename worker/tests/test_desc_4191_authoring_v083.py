# -*- coding: utf-8 -*-
"""v0.83 批②「描述/Rich 重造（4191 撰写链）」回归。

实锤 A：`/v3/product/import` 契约无顶层 description / description_json（Ozon 静默
忽略），卡面描述唯一载体 = 属性 4191（Аннотация）；内容评级 >100 字符 +25、>500 +25。
本批把 4191 从「通用句」重造为「事实锚定的俄语描述」，撰写链成为唯一来源。

覆盖：
  1. author_annotation 编排（撰写 / 数字锚定 / 降级链 / box_reviewed / 永久错误）；
  2. select_vision_images 图源优先级（COS 镜像 > AI 图 > none）；
  3. enforce_number_anchoring / collect_evidence_keys 纯函数；
  4. build_enrich_update_body 「可替换」分支；
  5. prepare 集成（撰写落 4191、编造数字被剥、跟卖卡不写、box_reviewed 不重写、无 description_json）；
  6. retry DESCRIPTION_DECLINE 靶位迁 4191（_apply_description_repair）；
  7. validate 4191 等价检查（长度/结构）；
  8. fetch_back / card_audit 跟卖豁免。

运行：cd worker && PYTHONPATH=src python -m pytest tests/test_desc_4191_authoring_v083.py -q
"""
import os
import sys
import types
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import content_enrich as ce  # noqa: E402

COS_MIRROR = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/a.jpg"
COS_AI = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/gen.jpg"
EXT = "https://cbu01.alicdn.com/img/x.jpg"


# ═══════════════════════════════════════════════════════════
# 1. author_annotation
# ═══════════════════════════════════════════════════════════

_LONG_LLM_OK = (
    "<p>Универсальный товар для дома и повседневного использования, подходит для всей семьи.</p>"
    "<b>Характеристики:</b><ul>"
    "<li>Материал: пластик</li>"
    "<li>Производительность: 5000 Вт</li>"
    "<li>Вес: 300 г</li></ul>"
    "<p>Удобный и надёжный в использовании, легко помещается в руке и не занимает много места.</p>"
)


def test_author_llm_happy_path_passes_vision_and_evidence():
    calls = {}

    def fake_llm(system, user, imgs):
        calls["system"] = system
        calls["user"] = user
        calls["imgs"] = imgs
        return _LONG_LLM_OK

    html, marks = ce.author_annotation(
        title_ru="Кофемолка",
        draft_attrs={"材质": "пластик"},
        final_attributes=[{"attribute_id": 4191, "value": ""}],
        draft_description="",
        weight_g=300,
        dims_mm={"length": 100, "width": 100, "height": 50},
        token="tok",
        image_urls=[COS_AI],
        vision_source=ce.VISION_SOURCE_AI,
        llm=fake_llm,
    )
    assert marks["description_source"] == "llm_authored"
    assert marks["description_fallback"] is False
    assert marks["vision_image_source"] == ce.VISION_SOURCE_AI
    assert calls["imgs"] == [COS_AI]
    assert "5000" not in html, "编造数字（证据外）应被锚定闸剥除"
    assert "300" in html, "证据内数字应保留"
    assert marks["numbers_stripped"] == ["5000"]


def test_author_llm_returns_short_falls_back_to_build_annotation():
    def fake_llm(system, user, imgs):
        return "<p>Коротко.</p>"

    html, marks = ce.author_annotation(
        title_ru="Товар", draft_attrs={"材质": "металл"}, token="tok",
        llm=fake_llm, sanitize=lambda t: t,
    )
    assert marks["description_source"] == "build_annotation"
    assert marks["description_fallback"] is True
    assert ce._plain_len(html) >= ce.ANNOTATION_HARD_MIN


def test_author_content_violation_degrades_not_raises():
    from utils.mxou_api import MxouContentViolationError

    def fake_llm(system, user, imgs):
        raise MxouContentViolationError("violation")

    html, marks = ce.author_annotation(
        title_ru="Товар", draft_attrs={"材质": "металл"}, token="tok",
        llm=fake_llm, sanitize=lambda t: t,
    )
    assert marks["description_source"] == "build_annotation"
    assert marks["description_fallback"] is True
    assert html


def test_author_out_of_quota_propagates():
    import pytest
    from utils.mxou_api import MxouOutOfQuotaError

    def fake_llm(system, user, imgs):
        raise MxouOutOfQuotaError("OUT_OF_QUOTA")

    with pytest.raises(MxouOutOfQuotaError):
        ce.author_annotation(
            title_ru="Товар", token="tok", llm=fake_llm, sanitize=lambda t: t,
        )


def test_author_llm_fail_translates_source_then_flags_fallback():
    def fake_llm(system, user, imgs):
        return ""

    translated = "Подробное описание товара из источника на русском языке, " * 4

    html, marks = ce.author_annotation(
        title_ru="Товар",
        draft_description="1688 详情中文原文",
        token="tok",
        llm=fake_llm,
        translate=lambda t: translated,
        sanitize=lambda t: t,
    )
    assert marks["description_source"] == "translated_source"
    assert marks["description_fallback"] is True
    assert "Подробное" in html


def test_author_box_reviewed_user_text_never_rewritten():
    llm_calls = []

    def fake_llm(system, user, imgs):
        llm_calls.append(user[:20])
        return _LONG_LLM_OK

    user_ru = "Пользовательское описание товара, " * 5
    html, marks = ce.author_annotation(
        title_ru="Товар",
        draft_description=user_ru,
        box_reviewed=True,
        token="tok",
        llm=fake_llm,
        sanitize=lambda t: t,
    )
    assert marks["description_source"] == "user_text"
    assert llm_calls == [], "box_reviewed + 用户描述 → 绝不 LLM 重写"
    assert "Пользовательское" in html


def test_author_box_reviewed_without_description_allows_authoring():
    llm_calls = []

    def fake_llm(system, user, imgs):
        llm_calls.append(1)
        return _LONG_LLM_OK

    _html, marks = ce.author_annotation(
        title_ru="Товар", draft_description="", box_reviewed=True,
        token="tok", llm=fake_llm, sanitize=lambda t: t,
    )
    assert llm_calls == [1], "box_reviewed 但用户没写描述 → 允许撰写"
    assert marks["description_source"] == "llm_authored"


# ═══════════════════════════════════════════════════════════
# 2. select_vision_images / 锚定闸 / 证据集
# ═══════════════════════════════════════════════════════════

def test_select_vision_prefers_cos_mirror_over_ai():
    urls, src = ce.select_vision_images([EXT, COS_MIRROR], [COS_AI])
    assert src == ce.VISION_SOURCE_MIRROR
    assert urls == [COS_MIRROR]


def test_select_vision_falls_back_to_ai():
    urls, src = ce.select_vision_images([EXT], [COS_AI, EXT])
    assert src == ce.VISION_SOURCE_AI
    assert urls == [COS_AI]


def test_select_vision_none_when_all_external():
    urls, src = ce.select_vision_images([EXT], [EXT])
    assert src == ce.VISION_SOURCE_NONE
    assert urls == []


def test_number_anchoring_strips_li_and_p_sentences():
    evidence = ce.collect_evidence_keys(
        draft_attrs={"数量": "12"},
        weight_g=300,
        dims_mm={"length": 100, "width": 80, "height": 40},
    )
    html = (
        "<p>Товар качественный.</p>"
        "<b>Характеристики:</b><ul><li>Вес: 300 г</li><li>Срок службы: 25 лет</li></ul>"
        "<p>Подходит для дома.</p><p>Гарантия 99 месяцев.</p>"
    )
    out, stripped = ce.enforce_number_anchoring(html, evidence)
    assert "300" in out and "300" not in stripped
    assert "25" not in out and "25" in stripped
    assert "99" not in out and "99" in stripped
    assert "<p>Подходит для дома.</p>" in out


def test_evidence_keys_include_all_sources():
    keys = ce.collect_evidence_keys(
        draft_attrs={"a": "5 штук"},
        final_attributes=[{"attribute_id": 8229, "value": "Тип: 7"}],
        weight_g=300,
        dims_mm={"length": 100},
        description="原文 42 см",
    )
    for tok in ("5", "7", "300", "100", "42"):
        assert ce._num_key(tok) in keys


def test_annotation_needs_regen():
    assert ce.annotation_needs_regen("") is True
    assert ce.annotation_needs_regen("<p>коротко</p>") is True
    assert ce.annotation_needs_regen("<p>" + ("а" * 600) + "</p>") is False


# ═══════════════════════════════════════════════════════════
# 3. build_enrich_update_body 可替换分支
# ═══════════════════════════════════════════════════════════

_STORED = {
    "id": 111,
    "name": "Кофемолка",
    "offer_id": "sku-1",
    "description_category_id": 17027907,
    "type_id": 92359,
    "weight": 500, "weight_unit": "g",
    "depth": 200, "width": 150, "height": 100, "dimension_unit": "mm",
    "images": [{"file_name": COS_AI, "index": 0, "default": True}],
    "attributes": [],
}


def test_enrich_replace_short_annotation_when_allowed():
    stored = dict(_STORED)
    stored["attributes"] = [{"id": 4191, "values": [{"dictionary_value_id": 0, "value": "<p>коротко</p>"}]}]
    body, audit = ce.build_enrich_update_body(
        111, stored, [], {"数量": "30包"}, {},
        price="254", old_price="305", allow_annotation_replace=True,
    )
    assert body is not None
    assert ce.ANNOTATION_ATTR_ID in audit["filled"]
    assert ce.ANNOTATION_ATTR_ID in audit.get("replaced", [])


def test_enrich_keeps_short_annotation_when_not_allowed():
    stored = dict(_STORED)
    stored["attributes"] = [{"id": 4191, "values": [{"dictionary_value_id": 0, "value": "<p>коротко</p>"}]}]
    body, audit = ce.build_enrich_update_body(
        111, stored, [], {}, {},
        price="254", old_price="305", allow_annotation_replace=False,
    )
    assert body is None
    assert audit.get("reason") == "nothing_to_fill"


# ═══════════════════════════════════════════════════════════
# 4. prepare 集成
# ═══════════════════════════════════════════════════════════

from graphs.state import PrepareOzonUploadInput  # noqa: E402
from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node  # noqa: E402


def _draft(**kw):
    d = {
        "item_id": "test4191",
        "title": "Кофемолка электрическая",  # 俄语标题，避免标题翻译分支
        "images": [EXT],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {"材质": "пластик"},
        "sku_id": "test4191",
        "price": "1290",
        "original_price": "1490",
    }
    d.update(kw)
    return d


def _state(final_attributes=None, extensions=None, draft=None):
    return PrepareOzonUploadInput(
        draft=draft or _draft(),
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=final_attributes or [],
        attributes_schema=[{"id": 4191, "name": "Аннотация", "dictionary_id": 0, "is_required": False}],
        dictionary_values={},
        token="sk-test",
        original_images=[EXT],
        main_image=COS_AI,
        extensions=extensions or {},
    )


def _payload_attr_map(output):
    attrs = []
    for item in (output.ozon_payload or {}).get("items", []):
        attrs.extend(item.get("attributes", []))
    return {int(a["id"]): a for a in attrs if isinstance(a, dict) and a.get("id")}


@contextmanager
def _patches(*extra):
    base = [
        patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title", return_value="Товар"),
        patch("utils.mxou_api.call_mxou_chat_api", return_value=""),
    ]
    with ExitStack() as st:
        for p in base + list(extra):
            st.enter_context(p)
        yield


def test_prepare_authors_4191_and_strips_fabricated_number():
    with _patches(
        patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=_LONG_LLM_OK),
    ):
        out = prepare_ozon_upload_node(_state(), None, None)
    attrs = _payload_attr_map(out)
    assert 4191 in attrs
    val = attrs[4191]["values"][0]["value"]
    assert "5000" not in val
    assert "300" in val
    assert out.ozon_payload["_content_audit"]["description_source"] == "llm_authored"
    assert out.ozon_payload["_content_audit"]["vision_image_source"] == ce.VISION_SOURCE_AI
    # 死字段已删除
    assert "description_json" not in out.ozon_payload["items"][0]


def test_prepare_follow_card_writes_no_content():
    draft = _draft(ozon_product_id="3852000144", images=[COS_AI])
    with _patches(
        patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=_LONG_LLM_OK),
    ):
        out = prepare_ozon_upload_node(_state(draft=draft, extensions={"follow_sell": True}), None, None)
    attrs = _payload_attr_map(out)
    assert 4191 not in attrs, "跟卖卡绝不写 4191（竞品卡面一个字节都不写）"
    assert 11254 not in attrs
    assert out.ozon_payload["_content_audit"]["description_source"] == "follow_card_skip"


def test_prepare_box_reviewed_user_desc_not_rewritten():
    user_ru = "Пользовательский текст описания товара, " * 4
    draft = _draft(title="Кофемолка", description=user_ru)
    llm_mock = patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description")
    with _patches() as _:
        with llm_mock as m:
            m.return_value = _LONG_LLM_OK
            out = prepare_ozon_upload_node(
                _state(draft=draft, extensions={"box_reviewed": True}), None, None)
    assert m.call_count == 0, "box_reviewed + 用户描述 → 绝不触发 LLM 撰写"
    attrs = _payload_attr_map(out)
    assert 4191 in attrs
    val = attrs[4191]["values"][0]["value"]
    assert "Пользовательский" in val
    assert out.ozon_payload["_content_audit"]["description_source"] == "user_text"


# ═══════════════════════════════════════════════════════════
# 5. retry DESCRIPTION_DECLINE 靶位
# ═══════════════════════════════════════════════════════════

def test_apply_description_repair_writes_4191():
    from graphs.validation_retry_loop import _apply_description_repair

    st = types.SimpleNamespace(
        extensions={},
        ozon_payload={"items": [{"attributes": [{"id": 85, "values": [{"dictionary_value_id": 1, "value": "X"}]}]}]},
        final_attributes=[{"attribute_id": 85, "value": "X", "dictionary_value_id": 1}],
    )
    _apply_description_repair(st, "<p>Исправленное описание товара.</p>")
    payload_4191 = [a for a in st.ozon_payload["items"][0]["attributes"] if a.get("id") == 4191]
    assert payload_4191 and payload_4191[0]["values"][0]["value"] == "<p>Исправленное описание товара.</p>"
    fa_4191 = [a for a in st.final_attributes if int(a.get("attribute_id") or 0) == 4191]
    assert fa_4191 and fa_4191[0]["value"] == "<p>Исправленное описание товара.</p>"


def test_apply_description_repair_box_reviewed_skips():
    from graphs.validation_retry_loop import _apply_description_repair

    st = types.SimpleNamespace(
        extensions={"box_reviewed": True},
        ozon_payload={"items": [{"attributes": []}]},
        final_attributes=[],
    )
    _apply_description_repair(st, "<p>LLM 重写</p>")
    assert st.ozon_payload["items"][0]["attributes"] == []
    assert st.final_attributes == []


# ═══════════════════════════════════════════════════════════
# 6. validate 4191 等价检查
# ═══════════════════════════════════════════════════════════

def _run_validate(payload):
    from graphs.state import OzonValidateInput
    from graphs.nodes.ozon_validate_node import ozon_validate_node

    st = OzonValidateInput(ozon_payload=payload, ozon_client_id="cid", ozon_api_key="key")
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(st, {}, runtime)


def _min_payload(attrs):
    return {"items": [{
        "name": "Кофемолка электрическая",
        "offer_id": "sku-1",
        "price": "254",
        "weight": 300, "weight_unit": "g",
        "depth": 100, "width": 100, "height": 50, "dimension_unit": "mm",
        "images": [COS_AI], "primary_image": COS_AI,
        "description_category_id": 17028830, "type_id": 971206780,
        "description": "<p>Описание</p>",
        "attributes": attrs,
    }]}


def test_validate_flags_short_4191():
    out = _run_validate(_min_payload([
        {"id": 4191, "values": [{"dictionary_value_id": 0, "value": "<p>коротко</p>"}]},
    ]))
    assert any("4191" in str(e) for e in (out.validation_errors or []))


def test_validate_accepts_long_structured_4191():
    long_html = "<p>" + ("Подробное описание товара на русском языке. " * 12) + "</p><ul><li>Вес: 300 г</li></ul>"
    out = _run_validate(_min_payload([
        {"id": 4191, "values": [{"dictionary_value_id": 0, "value": long_html}]},
    ]))
    assert not any("4191" in str(e) for e in (out.validation_errors or []))


# ═══════════════════════════════════════════════════════════
# 7. fetch_back / card_audit 跟卖豁免
# ═══════════════════════════════════════════════════════════

def test_fetch_back_skips_follow_card():
    from graphs.nodes.fetch_back_node import _content_rating_enhance
    from graphs.state import FetchBackInput

    st = FetchBackInput(product_id="111", is_follow_sell=True, draft={}, pricing_info={})
    out = _content_rating_enhance(st, "111", _STORED)
    assert out["action"] == "skipped"
    assert out["reason"] == "follow_card_skip"


def test_card_audit_rating_gap_skips_follow_card():
    from services.card_audit_service import _check_rating_gap

    state = {"summary": {"follow_skipped": 0}, "follow_ids": {"111"}}
    rating_p = {"rating": 60, "groups": [{"name": "g", "improve_attributes": [{"id": 4191, "name": "Аннотация"}]}]}
    _check_rating_gap(state, "111", rating_p, _STORED, {}, {})
    assert state["summary"]["follow_skipped"] == 1
    # report_only / auto_fixed 都不该出现（未落 finding、未写卡）
    assert state["summary"].get("findings_open") is None
