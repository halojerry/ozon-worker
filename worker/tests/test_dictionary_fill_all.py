"""v0.26 P1-3 字典属性全量填满回归测试。

背景：原逻辑只对「非必填」字典属性做同义词搜索；必填兜底失败 + 可选字典属性大量未填。
修复：对 schema 中 dictionary_id>0 且未填的属性，三阶段填满：
  ① 缓存字典值精确匹配（dictionary_value_cache）
  ② /values/search（RU 关键词）
  ③ /values 列表包含匹配（多值属性取全部匹配；单值属性取精确/首值）
匹配不到 → 跳过（不盲补）。

运行（Docker 内）：
    docker run --rm -v /Volumes/os/dev/ozon-worker/worker:/app -w /app \
      -e PYTHONPATH=/app/src -e APP_WORKSPACE_PATH=/app -e GRSAI_API_KEY= \
      ozon-worker:latest python tests/test_dictionary_fill_all.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SYNONYMS = {
    "color": {
        "zh_keywords": ["颜色", "颜色分类", "color"],
        "ozon_name_keywords": ["цвет", "color"],
        "value_map": {"黑色": "Черный", "白色": "Белый"},
    },
    "season": {
        "zh_keywords": ["季节", "适用季节"],
        "ozon_name_keywords": ["сезон"],
        "value_map": {"四季": "Всесезонный"},
    },
    "material": {
        "zh_keywords": ["材质", "材料"],
        "ozon_name_keywords": ["материал"],
        "value_map": {"金属": "Металл"},
    },
}

SCHEMA = [
    # 10096 颜色为真实多值属性（is_collection=True）
    {"id": 10096, "name": "Цвет товара", "dictionary_id": 1494, "is_required": True, "is_collection": True},
    {"id": 22391, "name": "Сезон", "dictionary_id": 9999, "is_required": False},
    {"id": 22392, "name": "Свободный текст", "dictionary_id": 0, "is_required": False},
    # 22393 材质为单值属性（无 is_collection/is_multivalue）
    {"id": 22393, "name": "Материал", "dictionary_id": 7777, "is_required": False},
]


class _State:
    ozon_client_id = "5381204"
    ozon_api_key = "k"
    token = "t"
    description_category_id = "41777465"
    type_id = "93040"
    dictionary_values = {
        "10096": [{"id": 61571, "value": "Черный"}, {"id": 61572, "value": "Белый"}],
    }


def _setup_synonyms():
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "config"), exist_ok=True)
    with open(os.path.join(tmp, "config", "attr_synonyms.json"), "w", encoding="utf-8") as f:
        json.dump(SYNONYMS, f)
    return tmp


def _call(draft_attrs, dict_values_override=None, search_return=None, list_return=None):
    import graphs.nodes.prepare_ozon_upload_node as mod
    import utils.ozon_dict_values as odv

    state = _State()
    if dict_values_override is not None:
        state.dictionary_values = dict_values_override
    items = [{"offer_id": "x", "name": "Панама", "attributes": []}]
    tmp = _setup_synonyms()
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": tmp}), \
         mock.patch.object(odv, "search_dictionary_values", return_value=search_return or []) as _sdv, \
         mock.patch.object(odv, "list_dictionary_values", return_value=list_return or []) as _ldv:
        out = mod._fill_optional_dict_attrs(items, SCHEMA, {"attributes": draft_attrs}, state)
    return out, _sdv, _ldv


def test_cache_exact_match():
    """① 缓存字典值精确匹配（黑色→Черный id=61571）。"""
    out, _sdv, _ldv = _call({"颜色分类": "黑色"})
    g = next(a for a in out[0]["attributes"] if a.get("id") == 10096)
    assert g["values"] == [{"dictionary_value_id": 61571, "value": "Черный"}]
    assert _sdv.call_count == 0, "缓存命中不应调 search"


def test_search_fallback():
    """② 缓存无值 → /values/search 兜底。"""
    out, _sdv, _ldv = _call(
        {"颜色分类": "白色"},
        dict_values_override={"10096": []},
        search_return=[{"id": 61572, "value": "Белый"}],
    )
    g = next(a for a in out[0]["attributes"] if a.get("id") == 10096)
    assert g["values"] == [{"dictionary_value_id": 61572, "value": "Белый"}]
    assert _sdv.call_count >= 1


def test_list_contain_match_multivalue():
    """③ search 空 → 列表包含匹配，多值属性取全部匹配（Черный + Черный матовый）。"""
    out, _sdv, _ldv = _call(
        {"颜色分类": "黑色"},
        dict_values_override={"10096": []},
        search_return=[],
        list_return=[{"id": 61571, "value": "Черный"}, {"id": 61574, "value": "Черный матовый"},
                     {"id": 61573, "value": "Синий"}],
    )
    g = next(a for a in out[0]["attributes"] if a.get("id") == 10096)
    # "Черный" 包含在 "Черный матовый" 中 → 应取两个
    assert len(g["values"]) == 2, f"多值应取全部匹配，实际 {g['values']}"
    texts = {v["value"] for v in g["values"]}
    assert texts == {"Черный", "Черный матовый"}, texts


def test_single_value_attr_takes_exact_only():
    """③ 单值属性（无 is_collection/is_multivalue）：列表包含多命中只取精确匹配，
    防 Ozon ATTRIBUTE_VALUE_COUNT_EXCEEDED（"Металл" + "Металл матовый" 都命中 → 只取精确的 1 个）。"""
    out, _sdv, _ldv = _call(
        {"材质": "金属"},
        dict_values_override={"22393": []},
        search_return=[],
        list_return=[{"id": 7001, "value": "Металл"}, {"id": 7002, "value": "Металл матовый"},
                     {"id": 7003, "value": "Дерево"}],
    )
    g = next(a for a in out[0]["attributes"] if a.get("id") == 22393)
    assert g["values"] == [{"dictionary_value_id": 7001, "value": "Металл"}], g["values"]


def test_no_match_skipped():
    """匹配不到 → 跳过（不盲补首值）。"""
    out, _sdv, _ldv = _call(
        {"颜色分类": "荧光粉"},
        dict_values_override={"10096": []},
        search_return=[],
        list_return=[{"id": 61573, "value": "Синий"}],
    )
    assert out[0]["attributes"] == [], f"不应盲补，实际 {out[0]['attributes']}"


def test_free_text_attr_untouched():
    """dictionary_id=0 的自由文本属性不受影响。"""
    out, _sdv, _ldv = _call({"颜色分类": "黑色"}, dict_values_override={"10096": []},
                            search_return=[], list_return=[])
    # 10096 无匹配跳过；22392 自由文本跳过
    assert out[0]["attributes"] == []


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
