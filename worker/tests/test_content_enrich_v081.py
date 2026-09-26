"""v0.81 内容评分闭环（feat/content-rating-loop-v081）回归。

四件套：
- utils/content_enrich.py：build_annotation / build_rich_json / fillable_improves /
  clamp_old_price / build_enrich_update_body（复检闭环与清扫脚本唯一构造入口）；
- prepare 出口恒填闸 _ensure_content_attrs_in_payload（4191/11254，最终图定型后）；
- fetch_back 过审后复检闭环（rating-by-sku → 可填缺口 → 全量回显 UPDATE，非致命）；
- scripts/content_rating_sweep.py 存量清扫 --dry-run。

实机锚点（2026-09-26）：rating<40 几乎不展示；import 补 4191+11254 实测卡分
42-57 → 90+；v3 import dimensions 扁平且不得为 0；price/old_price 字符串且
差价 <400 时至少 20；attributes 全量替换语义（必须回显防洗卡）。
"""
from __future__ import annotations

import json
import sys
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.content_enrich import (
    ANNOTATION_ATTR_ID,
    MEDIA_ATTR_IDS,
    RATING_THRESHOLD,
    RICH_CONTENT_ATTR_ID,
    build_annotation,
    build_rich_json,
    build_enrich_update_body,
    clamp_old_price,
    enrichment_enabled,
    extract_improve_attrs,
    fillable_improves,
    parse_rating_products,
    pick_product,
    rating_groups_summary,
)

# ── build_annotation ─────────────────────────────────────────


def test_annotation_full_evidence():
    """标题 + 数量/材料/尺寸证据 → 2-3 句 RU HTML，无中文残留。"""
    out = build_annotation("Деревянные одноразовые ложки", {"数量": "100只装", "材料": "竹木", "尺寸": "20×10×5 cm"})
    assert out.startswith("<p>") and out.endswith("</p>")
    assert "Деревянные одноразовые ложки" in out
    assert "100 шт в упаковке" in out
    assert "бамбук" in out  # 竹木 → бамбук
    assert "Размер: 20×10×5 cm" in out
    assert not any("\u4e00" <= c <= "\u9fff" for c in out)


def test_annotation_malformed_inputs_no_raise():
    """None 标题 / 非 dict 属性 / 空值 → 不 raise，仍产出最小合法 HTML。"""
    for title, attrs in ((None, None), ("", {"材料": 123}), ("Товар", "not-a-dict"), (None, [{"x": 1}])):
        out = build_annotation(title, attrs)
        assert out.startswith("<p>") and out.endswith("</p>")
        assert out.strip() != "<p></p>"


def test_annotation_chinese_title_stripped():
    """标题混中文 → 中文剥除不残留（Ozon 中文零容忍）。"""
    out = build_annotation("木质衣架 Деревянные вешалки", {})
    assert "Деревянные вешалки" in out
    assert not any("\u4e00" <= c <= "\u9fff" for c in out)


def test_annotation_unmapped_material_not_fabricated():
    """材料映射不到 → 不编造 Материал 句（宁缺毋滥）。"""
    out = build_annotation("Ложки", {"材料": "太空级碳纤维复合材料"})
    assert "Материал" not in out


# ── build_rich_json ──────────────────────────────────────────


def test_rich_json_structure_field_validated():
    """真实卡验证结构：raShowcase/chess/img 块（src/srcMobile/alt/position）。"""
    imgs = ["https://cos/1.jpg", "https://cos/2.jpg", "https://cos/3.jpg"]
    out = build_rich_json(imgs, "Деревянные ложки")
    assert out is not None
    assert "Деревянные" in out  # ensure_ascii=False：西里尔原文不入转义
    d = json.loads(out)
    assert set(d.keys()) == {"content"}
    widget = d["content"][0]
    assert widget["widgetName"] == "raShowcase"
    assert widget["type"] == "chess"
    blocks = widget["blocks"]
    assert len(blocks) == 3
    b0 = blocks[0]
    assert b0["img"]["src"] == imgs[0]
    assert b0["img"]["srcMobile"] == imgs[0]
    assert b0["img"]["position"] == "to_the_edge"
    assert b0["img"]["positionMobile"] == "to_the_edge"
    assert "alt" in b0["img"]


def test_rich_json_caps_at_four_images():
    """只取前 4 张图。"""
    imgs = [f"https://cos/{i}.jpg" for i in range(10)]
    d = json.loads(build_rich_json(imgs, "Товар"))
    assert len(d["content"][0]["blocks"]) == 4


def test_rich_json_empty_or_sparse_images_none():
    """空图列表 / 单图（chess 最低 2 blocks）/ 畸形条目 → None（不强造）。"""
    assert build_rich_json([], "Товар") is None
    assert build_rich_json(["https://only1.jpg"], "Товар") is None
    assert build_rich_json([None, "", 123, "  "], "Товар") is None


def test_rich_json_dedupes_urls():
    """重复 URL 去重。"""
    d = json.loads(build_rich_json(["https://a/1.jpg", "https://a/1.jpg", "https://a/2.jpg"], "Т"))
    assert len(d["content"][0]["blocks"]) == 2


# ── fillable_improves ────────────────────────────────────────

_SCHEMA = {
    8962: {"id": 8962, "type": "Integer", "dictionary_id": 0},
    85: {"id": 85, "type": "String", "dictionary_id": 28732849},
    4383: {"id": 4383, "type": "Decimal", "dictionary_id": 0},
    5011: {"id": 5011, "type": "String", "dictionary_id": 0},
    123: {"id": 123, "type": "String", "dictionary_id": 0},
}
_DRAFT = {"数量": "30包", "材料": "不锈钢", "型号": "MX-200"}


def test_fillable_video_and_dedicated_attrs_excluded():
    """视频/图片 id（21841/21845/4195）与 4191/11254 恒排除。"""
    improves = [{"id": 21841, "name": "Видео"}, {"id": 21845, "name": "ВидеоYouTube"},
                {"id": 4195, "name": "Фото"}, {"id": 4191, "name": "Аннотация"},
                {"id": 11254, "name": "Rich-контент JSON"}]
    schema = {aid: {"id": aid, "type": "String", "dictionary_id": 0} for aid in (21841, 21845, 4195, 4191, 11254)}
    assert fillable_improves(improves, _DRAFT, schema) == {}
    assert MEDIA_ATTR_IDS == frozenset({21841, 21845, 4195})


def test_fillable_dictionary_and_type_guards():
    """字典型（dictionary_id>0）/Decimal/无 schema → 跳过；Integer 提取数字。"""
    improves = [
        {"id": 85, "name": "Бренд"},          # 字典属性无字典 id 证据 → 跳过
        {"id": 4383, "name": "Вес товара"},   # Decimal 不在 String/Integer 白名单 → 跳过
        {"id": 777, "name": "Материал"},      # schema 缺失 → 跳过（保守）
        {"id": 8962, "name": "Количество в упаковке, шт"},  # Integer ← 30包 → 30
    ]
    out = fillable_improves(improves, _DRAFT, _SCHEMA)
    assert out == {8962: "30"}


def test_fillable_semantic_match_and_translation():
    """俄语属性名 → 语义槽位 → draft 中文键；映射值译俄；无语义键跳过。"""
    improves = [{"id": 5011, "name": "Материал"}, {"id": 123, "name": "Модель"},
                {"id": 555, "name": "Мощность, Вт"}]
    schema = dict(_SCHEMA)
    schema[555] = {"id": 555, "type": "Integer", "dictionary_id": 0}
    draft = dict(_DRAFT)
    draft["功率"] = "1500瓦"
    out = fillable_improves(improves, draft, schema)
    assert out[5011] == "нержавеющая сталь"
    assert out[123] == "MX-200"
    assert out[555] == "1500"


def test_fillable_unmapped_cjk_string_skipped():
    """中文自由文本映射不到俄语 → 跳过（绝不把中文送上卡）。"""
    improves = [{"id": 5011, "name": "Материал"}]
    assert fillable_improves(improves, {"材料": "太空级凝胶"}, _SCHEMA) == {}


def test_fillable_no_draft_or_bad_input():
    """无 draft 证据 / 畸形输入 → 全跳过不 raise（清扫脚本路径）。"""
    improves = [{"id": 8962, "name": "Количество"}, "garbage", {"id": "x", "name": "y"}]
    assert fillable_improves(improves, {}, _SCHEMA) == {}
    assert fillable_improves(improves, None, _SCHEMA) == {}


# ── clamp_old_price ──────────────────────────────────────────


def test_clamp_old_price_gap_rules():
    """差价 <400 时至少 20；≥400 原样；old≤price 抬到 price+20。"""
    assert clamp_old_price(100, 110) == 120   # 差 10 <20 → 120
    assert clamp_old_price(100, 115) == 120
    assert clamp_old_price(100, 350) == 350   # 差 250 ∈ [20,400) → 保持
    assert clamp_old_price(100, 600) == 600   # 差 500 ≥400 → 保持
    assert clamp_old_price(100, 100) == 120   # old ≤ price → 抬
    assert clamp_old_price(100, 80) == 120


# ── build_enrich_update_body（复检/清扫唯一构造入口）───────────

_IMPROVES = [
    {"id": 21841, "name": "Видео"},
    {"id": 4191, "name": "Аннотация"},
    {"id": 11254, "name": "Rich-контент JSON"},
    {"id": 8962, "name": "Количество в упаковке, шт"},
    {"id": 85, "name": "Бренд"},
]
_STORED = {
    "id": 6443821910,
    "name": "Деревянные ложки",
    "offer_id": "1053294385785",
    "description_category_id": 17027907,
    "type_id": 92359,
    "weight": 500,
    "weight_unit": "g",
    "depth": 200,
    "width": 150,
    "height": 100,
    "dimension_unit": "mm",
    "images": [
        {"file_name": "https://cos/2.jpg", "index": 1},
        {"file_name": "https://cos/1.jpg", "index": 0, "default": True},
        {"file_name": "https://cos/3.jpg", "index": 2},
    ],
    "attributes": [
        {"id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
        {"id": 23536, "values": [{"dictionary_value_id": 0, "value": "auto"}]},
        {"id": 4191, "values": [{"dictionary_value_id": 0, "value": ""}]},
    ],
}


def test_enrich_body_full_construction():
    """全量回显 + 新填 4191/11254/可填属性 + 扁平 dims + 字符串价格。"""
    body, audit = build_enrich_update_body(
        6443821910, _STORED, _IMPROVES, _DRAFT, _SCHEMA,
        price=254, old_price=300, currency_code="CNY",
    )
    assert body is not None
    item = body["items"][0]
    assert item["product_id"] == 6443821910
    assert item["offer_id"] == "1053294385785"
    assert item["description_category_id"] == 17027907 and item["type_id"] == 92359
    # 扁平 dimensions（契约坑：不得嵌套、不得为 0）
    assert (item["weight"], item["weight_unit"]) == (500, "g")
    assert (item["depth"], item["width"], item["height"], item["dimension_unit"]) == (200, 150, 100, "mm")
    # 字符串价格 + currency
    assert item["price"] == "254" and item["old_price"] == "300" and item["currency_code"] == "CNY"
    # images 回显按 index 排序，default 为 primary
    assert item["images"][0] == "https://cos/1.jpg"
    assert item["primary_image"] == "https://cos/1.jpg"
    ids = [a["id"] for a in item["attributes"]]
    assert ids.count(4191) == 1 and ids.count(11254) == 1
    assert 85 in ids          # 回显保留现卡特征（防洗卡）
    assert 23536 not in ids   # Ozon 自动设置属性不回发
    assert 21841 not in ids   # 媒体缺口不构造
    assert 8962 in ids        # 可填 Integer
    rich_val = next(a for a in item["attributes"] if a["id"] == 11254)["values"][0]["value"]
    assert json.loads(rich_val)["content"][0]["blocks"][0]["img"]["position"] == "to_the_edge"
    assert audit["filled"] and 4191 in audit["filled"] and 11254 in audit["filled"]
    assert audit["media_gap"] == [21841]
    assert 85 in audit["skipped"]  # 卡上已有值不覆盖


def test_enrich_body_keeps_existing_annotation_when_not_requested():
    """卡上 4191 非空且不在 improve 列表 → 不重建。"""
    stored = dict(_STORED)
    stored["attributes"] = [
        {"id": 4191, "values": [{"dictionary_value_id": 0, "value": "<p>Уже есть описание товара.</p>"}]},
    ]
    body, audit = build_enrich_update_body(
        1, stored, [{"id": 11254, "name": "Rich-контент JSON"}], {}, {},
        price=100, old_price=150,
    )
    ids = [a["id"] for a in body["items"][0]["attributes"]]
    assert ids.count(4191) == 1
    assert 4191 not in audit["filled"]
    assert 11254 in ids  # 缺 11254 仍补


def test_enrich_body_abort_paths():
    """无价 / 零维度 / 空图回显 / 无回显 → 不动作且 reason 如实。"""
    b, a = build_enrich_update_body(1, _STORED, _IMPROVES, _DRAFT, _SCHEMA, price=0)
    assert b is None and a["reason"] == "no_price"
    stored_zero = dict(_STORED, weight=0)
    b, a = build_enrich_update_body(1, stored_zero, _IMPROVES, _DRAFT, _SCHEMA, price=10)
    assert b is None and a["reason"] == "zero_dims"
    stored_noimg = dict(_STORED, images=[])
    b, a = build_enrich_update_body(1, stored_noimg, _IMPROVES, _DRAFT, _SCHEMA, price=10)
    assert b is None and a["reason"] == "no_images"
    b, a = build_enrich_update_body(1, None, _IMPROVES, _DRAFT, _SCHEMA, price=10)
    assert b is None and a["reason"] == "no_card_echo"


def test_enrich_body_old_price_clamped_in_payload():
    """old_price 差价 <20 在 payload 内被抬到 20。"""
    body, _ = build_enrich_update_body(
        1, _STORED, [{"id": 4191, "name": "Аннотация"}], {}, {},
        price=100, old_price=110,
    )
    assert body["items"][0]["old_price"] == "120"


# ── 评级响应解析 ──────────────────────────────────────────────


def test_parse_rating_products_and_helpers():
    resp = {"products": [
        {"sku": 111, "rating": 55.0, "groups": [
            {"name": "Текстовое описание", "rating": 40, "weight": 50,
             "improve_attributes": [{"id": 4191, "name": "Аннотация"}]},
            {"name": "Медиа", "rating": 0, "weight": 30,
             "improve_attributes": [{"id": 21841, "name": "Видео"}, {"id": 21841, "name": "Видео"}]},
        ]},
        {"sku": 222, "rating": 95.0, "groups": []},
    ]}
    products = parse_rating_products(resp)
    assert len(products) == 2
    p = pick_product(products, 111)
    assert p["rating"] == 55.0
    assert pick_product(products, 999)["sku"] == 111  # 未命中回退首个
    assert parse_rating_products(None) == [] and parse_rating_products({}) == []
    improves = extract_improve_attrs(p)
    assert improves == [{"id": 4191, "name": "Аннотация"}, {"id": 21841, "name": "Видео"}]
    assert rating_groups_summary(p) == {"Текстовое описание": 40.0, "Медиа": 0.0}
    assert RATING_THRESHOLD == 90.0


# ── prepare 出口恒填闸 ────────────────────────────────────────


def _payload_item(images=None, primary=None, attrs=None, name="Деревянные ложки"):
    return {
        "name": name,
        "offer_id": "SKU-1",
        "attributes": attrs if attrs is not None else [],
        "images": images if images is not None else [],
        "primary_image": primary or "",
    }


def test_prepare_gate_fills_both_attrs_after_policy():
    """4191/11254 恒填：LLM 描述纯文本优先，11254 用最终 images（出口闸语义）。"""
    from graphs.nodes.prepare_ozon_upload_node import _ensure_content_attrs_in_payload

    payload = {"items": [_payload_item(
        images=["https://cos/a.jpg", "https://cos/b.jpg", "https://cos/c.jpg"],
        primary="https://cos/main.jpg",
    )]}
    _ensure_content_attrs_in_payload(
        payload,
        title_ru="Деревянные ложки",
        draft_attrs={"数量": "100只装"},
        llm_desc_text="<p>Это очень длинное описание товара на русском языке, которое должно быть использовано как аннотация.</p>",
    )
    attrs = {a["id"]: a for a in payload["items"][0]["attributes"]}
    assert ANNOTATION_ATTR_ID in attrs
    # LLM 描述纯文本版优先（无 build_annotation 的「шт в упаковке」句式）
    assert "очень длинное описание" in attrs[ANNOTATION_ATTR_ID]["values"][0]["value"]
    assert "<p>" not in attrs[ANNOTATION_ATTR_ID]["values"][0]["value"]  # 纯文本版
    assert RICH_CONTENT_ATTR_ID in attrs
    rich = json.loads(attrs[RICH_CONTENT_ATTR_ID]["values"][0]["value"])
    srcs = [b["img"]["src"] for b in rich["content"][0]["blocks"]]
    assert srcs[0] == "https://cos/main.jpg"  # primary 在前
    assert len(srcs) == 4  # 3 张 gallery + primary


def test_prepare_gate_short_llm_text_falls_back_to_builder():
    """LLM 文本 <40 字符 → build_annotation 兜底。"""
    from graphs.nodes.prepare_ozon_upload_node import _ensure_content_attrs_in_payload

    payload = {"items": [_payload_item(images=[])]}
    _ensure_content_attrs_in_payload(payload, title_ru="Ложки деревянные", draft_attrs={"数量": "50只"}, llm_desc_text="Коротко.")
    attrs = {a["id"]: a for a in payload["items"][0]["attributes"]}
    assert "50 шт в упаковке" in attrs[ANNOTATION_ATTR_ID]["values"][0]["value"]
    assert RICH_CONTENT_ATTR_ID not in attrs  # 无图不强造


def test_prepare_gate_respects_existing_and_multi_items():
    """已有 4191/11254 不重复；多 item（数量拆分）逐个补齐。"""
    from graphs.nodes.prepare_ozon_upload_node import _ensure_content_attrs_in_payload

    existing_rich = json.dumps({"content": [{"widgetName": "raShowcase", "type": "chess", "blocks": []}]})
    payload = {"items": [
        _payload_item(
            images=["https://cos/1.jpg", "https://cos/2.jpg"],
            attrs=[
                {"complex_id": 0, "id": ANNOTATION_ATTR_ID, "values": [{"dictionary_value_id": 0, "value": "<p>готово</p>"}]},
                {"complex_id": 0, "id": RICH_CONTENT_ATTR_ID, "values": [{"dictionary_value_id": 0, "value": existing_rich}]},
            ],
        ),
        _payload_item(images=["https://cos/3.jpg", "https://cos/4.jpg"], name="Ложки, 50 шт."),
    ]}
    _ensure_content_attrs_in_payload(payload, title_ru="Ложки", draft_attrs={}, llm_desc_text="")
    first_ids = [a["id"] for a in payload["items"][0]["attributes"]]
    assert first_ids.count(ANNOTATION_ATTR_ID) == 1 and first_ids.count(RICH_CONTENT_ATTR_ID) == 1
    second_ids = [a["id"] for a in payload["items"][1]["attributes"]]
    assert ANNOTATION_ATTR_ID in second_ids and RICH_CONTENT_ATTR_ID in second_ids
    rich2 = json.loads(next(a for a in payload["items"][1]["attributes"] if a["id"] == RICH_CONTENT_ATTR_ID)["values"][0]["value"])
    assert rich2["content"][0]["blocks"][0]["img"]["src"] == "https://cos/3.jpg"  # item 自己的图


# ── 过审后复检闭环（fetch_back）───────────────────────────────


def _fb_state(**kw):
    from graphs.state import FetchBackInput

    base = dict(
        product_id="6443821910",
        ozon_client_id="cid",
        ozon_api_key="key",
        final_attributes=[],
        attributes_schema=[{"id": 8962, "name": "Количество", "type": "Integer", "dictionary_id": 0}],
        draft={"attributes": {"数量": "30包"}},
        pricing_info={"price": 254, "old_price": 305, "currency_code": "CNY"},
    )
    base.update(kw)
    return FetchBackInput(**base)


def _rating_resp(rating, improves):
    return {"products": [{"sku": 6443821910, "rating": rating,
                          "groups": [{"name": "g", "rating": 1, "improve_attributes": improves}]}]}


def test_reactive_rating_above_threshold_no_action():
    """rating ≥90 → 只记录评级，零 UPDATE。"""
    from graphs.nodes.fetch_back_node import _content_rating_enhance

    calls = []
    with mock.patch("graphs.nodes.fetch_back_node.ozon_post",
                    side_effect=lambda cid, key, ep, body, **kw: calls.append(ep) or _rating_resp(95, [{"id": 4191, "name": "Аннотация"}])):
        audit = _content_rating_enhance(_fb_state(), "6443821910", _STORED)
    assert audit["action"] == "above_threshold" and audit["rating"] == 95.0
    assert calls == ["/v1/product/rating-by-sku"]  # 没有任何 import


def test_reactive_no_fillable_gap_no_action():
    """rating <90 且无缺口（4191/11254 已在卡上、无 improve）→ 不动作。"""
    from graphs.nodes.fetch_back_node import _content_rating_enhance

    stored_complete = dict(_STORED)
    stored_complete["attributes"] = [
        {"id": 4191, "values": [{"dictionary_value_id": 0, "value": "<p>Полное описание товара на русском языке.</p>"}]},
        {"id": 11254, "values": [{"dictionary_value_id": 0, "value": json.dumps({"content": []})}]},
    ]
    calls = []
    with mock.patch("graphs.nodes.fetch_back_node.ozon_post",
                    side_effect=lambda cid, key, ep, body, **kw: calls.append(ep) or _rating_resp(60, [])):
        audit = _content_rating_enhance(_fb_state(), "6443821910", stored_complete)
    assert audit["action"] == "skipped" and audit.get("reason") == "nothing_to_fill"
    assert "/v3/product/import" not in calls


def test_reactive_builds_correct_update():
    """rating <90 + 可填缺口 → 一次全量回显 UPDATE，审计块如实。"""
    from graphs.nodes.fetch_back_node import _content_rating_enhance

    captured = {}

    def _post(cid, key, ep, body, **kw):
        captured["endpoint"] = ep
        captured["body"] = body
        if ep == "/v1/product/rating-by-sku":
            return _rating_resp(55, _IMPROVES)
        return {"result": {"task_id": 98765}}

    with mock.patch("graphs.nodes.fetch_back_node.ozon_post", side_effect=_post):
        audit = _content_rating_enhance(_fb_state(), "6443821910", _STORED)
    assert captured["endpoint"] == "/v3/product/import"
    item = captured["body"]["items"][0]
    ids = [a["id"] for a in item["attributes"]]
    assert 4191 in ids and 11254 in ids and 8962 in ids
    assert 85 in ids and 23536 not in ids  # 全量回显防洗卡
    assert item["weight"] == 500 and item["depth"] == 200  # 扁平 dims
    assert item["price"] == "254" and item["old_price"] == "305"
    assert audit["action"] == "updated" and audit["import_task_id"] == "98765"
    assert audit["media_gap"] == [21841]


def test_reactive_env_gate_off():
    """CONTENT_RATING_ENHANCE=0 → 不发任何请求。"""
    from graphs.nodes.fetch_back_node import _content_rating_enhance

    with mock.patch("graphs.nodes.fetch_back_node.ozon_post") as post, \
            mock.patch.dict("os.environ", {"CONTENT_RATING_ENHANCE": "0"}):
        assert enrichment_enabled() is False
        audit = _content_rating_enhance(_fb_state(), "6443821910", _STORED)
        post.assert_not_called()
    assert audit["action"] == "skipped" and audit["reason"] == "env_gate_off"


def test_reactive_rating_api_error_is_fatal_free():
    """评级 API 异常 → 审计块 error，不 raise（非致命红线）。"""
    with mock.patch("graphs.nodes.fetch_back_node.ozon_post", side_effect=RuntimeError("ozon down")):
        from graphs.nodes.fetch_back_node import fetch_back_node

        state = _fb_state()
        out = fetch_back_node(state, None, mock.MagicMock())
    # /v4 回读也依赖 ozon_post → 失败返回空结果走早退路径；此处验证整体不 raise
    assert out.content_rating == {}


def test_reactive_error_after_echo_recorded_not_raised():
    """回读成功但评级查询异常 → 审计 action=error，节点正常返回。"""
    from graphs.state import FetchBackInput
    from graphs.nodes.fetch_back_node import fetch_back_node

    v4 = {"result": [dict(_STORED)]}

    def _post(cid, key, ep, body, **kw):
        if ep == "/v4/product/info/attributes":
            return v4
        raise RuntimeError("rating down")

    with mock.patch("graphs.nodes.fetch_back_node.ozon_post", side_effect=_post):
        state = FetchBackInput(product_id="6443821910", ozon_client_id="c", ozon_api_key="k")
        out = fetch_back_node(state, None, mock.MagicMock())
    assert out.content_rating["action"] == "error"
    assert out.fetch_back_result["mismatches"] == []  # diff 主流程不受影响


def test_reactive_debounce_once_per_task():
    """content_rating 非空（已复检）→ 零额外请求。"""
    from graphs.nodes.fetch_back_node import fetch_back_node
    from graphs.state import FetchBackInput

    v4 = {"result": [dict(_STORED)]}
    with mock.patch("graphs.nodes.fetch_back_node.ozon_post", return_value=v4) as post:
        state = _fb_state(content_rating={"rating": 55, "action": "updated"})
        out = fetch_back_node(state, None, mock.MagicMock())
    endpoints = [c.args[2] for c in post.call_args_list]
    assert "/v1/product/rating-by-sku" not in endpoints
    assert out.content_rating == {"rating": 55, "action": "updated"}


# ── 存量清扫脚本（dry-run）───────────────────────────────────


def _load_sweep_module():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "content_rating_sweep.py"
    spec = importlib.util.spec_from_file_location("content_rating_sweep", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sweep_dry_run_plans_but_never_imports():
    """--dry-run：评级 + 打印计划，绝不提交 /v3/product/import。"""
    mod = _load_sweep_module()
    calls = []

    def _post(cid, key, ep, body, **kw):
        calls.append(ep)
        if ep == "/v1/product/rating-by-sku":
            return _rating_resp(55, [{"id": 4191, "name": "Аннотация"},
                                     {"id": 21841, "name": "Видео"}])
        if ep == "/v4/product/info/attributes":
            return {"result": [dict(_STORED)]}
        if ep == "/v3/product/info/list":
            return {"result": {"items": [{"id": 6443821910, "price": "254", "old_price": "305", "currency_code": "CNY"}]}}
        raise AssertionError(f"unexpected {ep}")

    with mock.patch.object(mod, "ozon_post", side_effect=_post), \
            mock.patch.object(mod, "list_products", return_value=[{"product_id": "6443821910", "offer_id": "SKU-1"}]), \
            mock.patch("sys.argv", ["content_rating_sweep.py", "--dry-run",
                                    "--client-id", "c", "--api-key", "k"]):
        rc = mod.main()
    assert rc == 0
    assert calls.count("/v3/product/import") == 0  # dry-run 不动卡
    assert "/v1/product/rating-by-sku" in calls and "/v4/product/info/attributes" in calls


def test_sweep_real_run_submits_import():
    """非 dry-run：对低分卡提交一次 UPDATE。"""
    mod = _load_sweep_module()
    calls = []

    def _post(cid, key, ep, body, **kw):
        calls.append(ep)
        if ep == "/v1/product/rating-by-sku":
            return _rating_resp(55, [{"id": 4191, "name": "Аннотация"}])
        if ep == "/v4/product/info/attributes":
            return {"result": [dict(_STORED)]}
        if ep == "/v3/product/info/list":
            return {"result": {"items": [{"id": 6443821910, "price": "254", "old_price": "305", "currency_code": "CNY"}]}}
        if ep == "/v3/product/import":
            return {"result": {"task_id": 42}}
        raise AssertionError(f"unexpected {ep}")

    with mock.patch.object(mod, "ozon_post", side_effect=_post), \
            mock.patch.object(mod, "list_products", return_value=[{"product_id": "6443821910", "offer_id": "SKU-1"}]), \
            mock.patch("sys.argv", ["content_rating_sweep.py", "--client-id", "c", "--api-key", "k"]):
        rc = mod.main()
    assert rc == 0
    assert calls.count("/v3/product/import") == 1


def test_sweep_missing_credentials_exits():
    """缺凭证 → exit 2，不发请求。"""
    mod = _load_sweep_module()
    with mock.patch.object(mod, "ozon_post") as post, \
            mock.patch("sys.argv", ["content_rating_sweep.py"]), \
            mock.patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("OZON_CLIENT_ID", None)
        os.environ.pop("OZON_API_KEY", None)
        rc = mod.main()
    assert rc == 2
    post.assert_not_called()


def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    total = len(fns)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
