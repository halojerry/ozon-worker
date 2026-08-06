"""v0.26 P1-1 帽类 9163 性别修复回归测试。

背景：帽类目（dc=41777465）9163 字典值只有 Мужской/Женский/Девочки/Мальчики，
无中性词 → 原中性兜底（Унисекс/Универсальный）永远匹配不到 → 9163 空值
→ error_attribute_values_empty → pending/declined（Ozon 实证 Панама Шапка pending）。

修复：无中性词时取「男+女」双值兜底，保证必填不空。

运行（Docker 内）：
    docker run --rm -v /Volumes/os/dev/ozon-worker/worker:/app -w /app \
      -e PYTHONPATH=/app/src -e APP_WORKSPACE_PATH=/app -e GRSAI_API_KEY= \
      ozon-worker:latest python tests/test_hat_gender_fallback.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 帽类目 9163 的真实字典值（无中性词）
HAT_GENDER_VALUES = [
    {"id": 22880, "value": "Мужской"},
    {"id": 22881, "value": "Женский"},
    {"id": 22882, "value": "Девочки"},
    {"id": 22883, "value": "Мальчики"},
]

# 有中性词的类目字典值（对照）
NEUTRAL_GENDER_VALUES = HAT_GENDER_VALUES + [{"id": 99999, "value": "Унисекс"}]


class _State:
    ozon_client_id = "5381204"
    ozon_api_key = "k"
    token = "t"
    description_category_id = "41777465"
    type_id = "93040"
    dictionary_values = {}


def _call_prepare(items, schema, values, *, aid=9163, name="Пол"):
    """直接调用 prepare 节点的 _fill_missing_required_dict_attrs。"""
    import graphs.nodes.prepare_ozon_upload_node as mod
    import utils.ozon_dict_values as odv

    schema_attr = {
        "id": aid, "name": name, "dictionary_id": 320,
        "is_required": True, "type": "String",
    }
    items_in = [
        {"offer_id": "x", "name": "Панама", "attributes": []},
    ]

    with mock.patch.object(odv, "list_dictionary_values", return_value=values) as _ldv, \
         mock.patch.object(odv, "search_dictionary_values", return_value=[]) as _sdv:
        out = mod._fill_missing_required_dict_attrs(
            items_in, [schema_attr], {"title": "帽子"}, _State(),
        )
        return out, _ldv, _sdv


def test_hat_no_neutral_uses_male_female():
    """帽类目无中性词 → 男+女双值兜底（9163 必填不空）。"""
    out, _ldv, _sdv = _call_prepare(None, None, HAT_GENDER_VALUES)
    attrs = out[0].get("attributes", [])
    g = next((a for a in attrs if a.get("id") == 9163), None)
    assert g is not None, "9163 应被补齐"
    vals = g["values"]
    assert len(vals) == 2, f"应为男+女双值，实际 {vals}"
    texts = {v["value"] for v in vals}
    assert texts == {"Мужской", "Женский"}, texts
    ids = {v["dictionary_value_id"] for v in vals}
    assert ids == {22880, 22881}, ids


def test_hat_with_neutral_still_uses_neutral():
    """有中性词类目 → 仍走中性兜底（不破坏原有逻辑）。"""
    out, _ldv, _sdv = _call_prepare(None, None, NEUTRAL_GENDER_VALUES)
    attrs = out[0].get("attributes", [])
    g = next((a for a in attrs if a.get("id") == 9163), None)
    assert g is not None
    vals = g["values"]
    # 中性词命中时走 search 路径（search 返回空 → 落到列表中性匹配）
    texts = {v["value"] for v in vals}
    assert texts == {"Унисекс"} or "Унисекс" in texts, texts


def test_gender_pair_format_is_multivalue():
    """男+女兜底必须是多值格式（values 数组含 2 个 dictionary_value_id）。"""
    out, _, _ = _call_prepare(None, None, HAT_GENDER_VALUES)
    attrs = out[0].get("attributes", [])
    g = next((a for a in attrs if a.get("id") == 9163), None)
    assert len(g["values"]) == 2
    for v in g["values"]:
        assert v["dictionary_value_id"] > 0
        assert isinstance(v["value"], str) and v["value"]


def test_other_gender_attr_uses_male_female():
    """非 9163 但属性名含「Пол」的必填性别属性（如 4180 Пол получателя）
    无中性词时同样走男+女双值兜底（v0.26 通用化）。"""
    out, _, _ = _call_prepare(None, None, HAT_GENDER_VALUES, aid=4180, name="Пол получателя")
    attrs = out[0].get("attributes", [])
    g = next((a for a in attrs if a.get("id") == 4180), None)
    assert g is not None, "4180 应被补齐"
    texts = {v["value"] for v in g["values"]}
    assert texts == {"Мужской", "Женский"}, texts
    ids = {v["dictionary_value_id"] for v in g["values"]}
    assert ids == {22880, 22881}, ids


def test_non_gender_attr_not_touched():
    """非性别属性（如 4295 尺码）不受性别双值逻辑影响。"""
    out, _, _ = _call_prepare(None, None, HAT_GENDER_VALUES, aid=4295, name="Размер")
    attrs = out[0].get("attributes", [])
    g = next((a for a in attrs if a.get("id") == 4295), None)
    # 尺码无匹配默认 → 不补齐（不进性别分支）
    assert g is None, "非性别属性不应被性别双值逻辑补齐"


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
