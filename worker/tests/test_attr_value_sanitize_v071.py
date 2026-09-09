"""v0.71 值数出口闸单测：cap_attribute_values / resolve_value_cap / 路由与 validate。

背景（2026-09-08 gate 实证）：8229（Тип）schema 标 is_collection 但 Ozon 实际
限 1 值；_fill_optional_dict_attrs 对 multivalue_ids 全量塞命中 →
ATTRIBUTE_VALUE_COUNT_EXCEEDED 拒单。本批引入按 schema max_value_count 的
出口闸，并让 retry 不再把多值误路由成类目错。
"""
import sys
from unittest import mock

sys.path.insert(0, "src")

from utils.attr_value_sanitize import (  # noqa: E402
    build_schema_index,
    cap_attribute_values,
    resolve_value_cap,
)


def _v(dvid, text):
    return {"dictionary_value_id": dvid, "value": text}


def _a(aid, vals):
    return {"id": aid, "values": vals}


def test_non_collection_multi_value_trimmed_to_one():
    schema = [{"id": 10096, "is_collection": False}]  # 无 max_value_count
    attrs = [_a(10096, [_v(1, "a"), _v(2, "b"), _v(3, "c")])]
    out, marks = cap_attribute_values(attrs, schema)
    assert len(out[0]["values"]) == 1
    assert out[0]["values"][0]["dictionary_value_id"] == 1  # 保前排
    assert marks and marks[0]["attr_id"] == 10096 and marks[0]["cap"] == 1


def test_collection_with_max_value_count_trimmed():
    schema = [{"id": 5075, "is_collection": True, "max_value_count": 2}]
    attrs = [_a(5075, [_v(1, "a"), _v(2, "b"), _v(3, "c")])]
    out, _ = cap_attribute_values(attrs, schema)
    assert len(out[0]["values"]) == 2


def test_8229_always_capped_to_one():
    """8229 契约：dictionary_value_id == type_id 单值——即使缓存行缺 max_value_count。"""
    schema = [{"id": 8229, "is_collection": True}]  # 旧缓存行无 max_value_count
    attrs = [_a(8229, [_v(111, "Тип A"), _v(222, "Тип B")])]
    out, _ = cap_attribute_values(attrs, schema)
    assert len(out[0]["values"]) == 1
    assert resolve_value_cap({"id": 8229, "is_collection": True}) == 1


def test_collection_without_max_untouched():
    schema = [{"id": 5075, "is_collection": True}]  # 集合且未声明上限 → 不设限
    vals = [_v(i, f"v{i}") for i in range(1, 6)]
    attrs = [_a(5075, vals)]
    out, marks = cap_attribute_values(attrs, schema)
    assert len(out[0]["values"]) == 5
    assert marks == []


def test_unknown_schema_attr_untouched():
    attrs = [_a(99999, [_v(1, "a"), _v(2, "b")])]
    out, marks = cap_attribute_values(attrs, [{"id": 10096, "is_collection": False}])
    assert len(out[0]["values"]) == 2
    assert marks == []


def test_duplicate_values_collapsed():
    schema = [{"id": 10096, "is_collection": True, "max_value_count": 5}]
    attrs = [_a(10096, [_v(1, "a"), _v(1, "a"), _v(2, "b"), _v(1, "a")])]
    out, _ = cap_attribute_values(attrs, schema)
    assert len(out[0]["values"]) == 2  # (1,'a') + (2,'b')


def test_malformed_input_safe():
    assert cap_attribute_values([], []) == ([], [])
    assert cap_attribute_values([None, "x", {"id": 0, "values": "notalist"}], None) == (
        [None, "x", {"id": 0, "values": "notalist"}], [])
    out, _ = cap_attribute_values([{"id": "abc", "values": []}], [])
    assert out == [{"id": "abc", "values": []}]
    assert build_schema_index(None) == {}
    assert build_schema_index([{"id": None}, "junk", {"id": 5}]) == {5: {"id": 5}}


def test_schema_index_and_cap_resolution():
    schema = [
        {"id": 1, "is_collection": False, "max_value_count": 3},
        {"id": 2, "is_collection": True},
        {"id": 3},
    ]
    idx = build_schema_index(schema)
    assert resolve_value_cap(idx[1]) == 3   # max_value_count 优先
    assert resolve_value_cap(idx[2]) is None  # 集合无声明 → 不设限
    assert resolve_value_cap(idx[3]) == 1     # 缺 is_collection 键 → 默认单值
    assert resolve_value_cap(None) is None


def test_category_mismatch_not_triggered_by_value_count():
    """多值超限不再被误判类目错（曾把 8229+out_of_range 推进 R4 整卡重配）。"""
    from graphs.validation_retry_loop import _looks_like_category_mismatch

    st = mock.Mock()
    st.error_code = "ATTRIBUTE_VALUE_COUNT_EXCEEDED"
    st.attribute_id = 8229
    st.error_message = "Превышено максимальное количество значений у коллекционного атрибута"
    st.errors = []
    assert _looks_like_category_mismatch(st) is False


def test_fix_via_attributes_update_caps_values():
    """增量修复重发前按 schema 裁多值（此前原样重发继续被拒，烧轮次）。"""
    import graphs.validation_retry_loop as vrl

    captured = {}

    def _post(client_id, api_key, endpoint, body=None, timeout=30, **kw):
        captured["body"] = body
        return {"result": {"task_id": "t1"}}

    st = mock.Mock()
    st.ozon_payload = {"items": [{
        "offer_id": "off1",
        "type_id": 93408,
        # payload 快照里 8229 已是多值（被拒现场），4180 单值
        "attributes": [
            {"id": 8229, "values": [_v(111, "Тип A"), _v(222, "Тип B")]},
            {"id": 4180, "values": [_v(5, "one")]},
        ],
    }]}
    st.final_attributes = []
    st.product_id = "123"
    st.ozon_client_id = "cid"
    st.ozon_api_key = "key"
    st.attributes_schema = [
        {"id": 8229, "is_collection": True, "max_value_count": 1},
        {"id": 4180, "is_collection": False},
    ]
    with mock.patch.object(vrl, "ozon_post", side_effect=_post):
        ok = vrl._fix_via_attributes_update(st)
    assert ok is True
    sent = {a["id"]: a["values"] for a in captured["body"]["items"][0]["attributes"]}
    assert len(sent[8229]) == 1  # 多值被裁
    assert sent[8229][0]["dictionary_value_id"] == 111  # 保前排
    assert len(sent[4180]) == 1
