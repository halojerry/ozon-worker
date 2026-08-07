# -*- coding: utf-8 -*-
"""
v0.29.x 语言路由回归测试 — 按输入来源选择字典查询语言（用户明确定义）

规则（简单逻辑, 用测试锁定防回归）:
- 输入是 1688 的中文属性值 / 中文标题 → /values/search 用 language=ZH_HANS 直查
  (dictionary_value_id 跨语言通用, 中文查即命中, 不翻译成 RU)
- 输入是 Ozon 的俄语信息(如类目树末级名 type_name) → language=RU 查询
- 属性值/描述残留中文 → 不得写入 final_attributes（中文零容忍）

运行: cd worker && PYTHONPATH=src python3 tests/test_language_routing.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import patch

from graphs.nodes.assemble_ozon_product_node import (
    _build_items_deterministically,
    _validate_and_enrich_items,
)


def _schema():
    # 真实环境: assemble 拉属性 schema 用 ZH_HANS(中文属性名, v0.5.0 起)
    return [
        {"id": 8229, "name": "类型", "dictionary_id": 504, "is_required": True},
        {"id": 9782, "name": "危险等级", "dictionary_id": 77, "is_required": True},
        {"id": 9999, "name": "材质", "dictionary_id": 13, "is_required": False},
        {"id": 85, "name": "品牌", "dictionary_id": 11, "is_required": True},
    ]


def _dict_lookup():
    # ZH_HANS 缓存值（预热只存中文, 与真实环境一致）
    return {
        9999: [{"id": 300, "value": "塑料"}],
        85: [{"id": 126745801, "value": "Нет бренда"}],
    }


def _draft_1688():
    """1688 选品来的产品: 属性值是中文。"""
    return {
        "item_id": "lang001",
        "title": "家用杀虫剂 蟑螂药 蚂蚁药",
        "images": ["http://img.test/1.jpg"],
        "weight": 200,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {
            "材质": "塑料",          # 1688 中文值
            "类型": "杀虫剂",        # 1688 中文值
        },
        "sku_id": "lang001",
        "price": "1990",
        "original_price": "2390",
    }


def _run_1688_pipeline(ozon_post_calls):
    """跑 1688 全链路, 捕获所有 ozon_post 调用(断言语言路由)。"""
    draft = _draft_1688()
    items = _build_items_deterministically(
        draft=draft,
        description_category_id=17028747,
        type_id=99385,
        attr_list=_schema(),
        dict_lookup=_dict_lookup(),
        images=draft["images"],
        ozon_client_id="test_client",
        ozon_api_key="test_key",
        weight_grams=int(draft["weight"]),
        dimensions=draft["dimensions"],
        price_rub=str(draft["price"]),
        old_price_rub=str(draft["original_price"]),
        currency_code="RUB",
        token="sk-test",
    )
    with patch("utils.ozon_client.ozon_post") as mock_post:
        mock_post.return_value = {"result": []}
        items = _validate_and_enrich_items(
            items=items,
            attr_list=_schema(),
            dict_lookup=_dict_lookup(),
            images=draft["images"],
            ozon_client_id="test_client",
            ozon_api_key="test_key",
            description_category_id=17028747,
            type_id=99385,
            weight_grams=int(draft["weight"]),
            dimensions=draft["dimensions"],
            draft_title=draft["title"],
            supplier="深圳市杀虫剂有限公司",
            ru_category_path="Товары для дома>Средства от насекомых>Инсектициды",
        )
        ozon_post_calls.extend(mock_post.call_args_list)
    return items[0]["attributes"]


def test_1688_chinese_value_uses_zhhans_lookup():
    """1688 中文属性值 → /values/search 必须用 language=ZH_HANS（不是 RU）。

    排除 attr=8229(类目名 type_name 俄语, 单独测试); 其余 1688 中文值/标题
    的搜索必须 ZH_HANS。
    """
    calls = []
    _run_1688_pipeline(calls)
    search_calls = [
        c for c in calls
        if c.kwargs.get("endpoint", "").endswith("values/search")
        and c.kwargs.get("body", {}).get("attribute_id") != 8229
    ]
    assert search_calls, "应有 /values/search 调用（1688 中文值 dict 未命中时）"
    langs = {c.kwargs.get("language") for c in search_calls}
    assert "ZH_HANS" in langs, f"1688 中文值必须 ZH_HANS 查询, 实际: {langs}"
    assert "RU" not in langs, f"1688 中文值不应走 RU 查询: {langs}"


def test_8229_ozon_category_name_uses_ru():
    """8229 补填: type_name 来自 Ozon 类目树(俄语, 如 Инсектициды) → RU 查询;
    属性名路径(中文「类型」) → ZH_HANS。两条路径语言必须匹配搜索词。"""
    calls = []
    _run_1688_pipeline(calls)
    search_calls = [
        c for c in calls
        if c.kwargs.get("endpoint", "").endswith("values/search")
        and c.kwargs.get("body", {}).get("attribute_id") == 8229
    ]
    if search_calls:
        # 俄语搜索词(类目名) → RU; 中文搜索词(属性名/标题) → ZH_HANS
        import re as _re
        for c in search_calls:
            val = str(c.kwargs.get("body", {}).get("value", ""))
            lang = c.kwargs.get("language")
            if _re.search(r"[\u4e00-\u9fff]", val):
                assert lang == "ZH_HANS", f"中文搜索词必须 ZH_HANS: {val} → {lang}"
            elif any(ch in val.lower() for ch in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"):
                assert lang == "RU", f"俄语搜索词(类目名)必须 RU: {val} → {lang}"


def test_no_chinese_value_in_final_attributes():
    """中文零容忍: final_attributes 不得含中文字符值（防「属性8229含中文字符」）。"""
    import re
    calls = []
    attrs = _run_1688_pipeline(calls)
    for a in attrs:
        if not isinstance(a, dict):
            continue
        values = a.get("values") or []
        for v in values:
            val = str(v.get("value", ""))
            assert not re.search(r"[\u4e00-\u9fff]", val), \
                f"final_attributes 不得含中文: attr={a.get('id')} value={val}"


def test_8229_value_not_left_chinese_when_matched():
    """8229 用 mock 命中场景: 若补填成功, 值应是俄语(来自 API 返回)而非中文。"""
    # mock ozon_post 对 8229 返回俄语字典值 → 断言写入的值无中文
    draft = _draft_1688()
    items = _build_items_deterministically(
        draft=draft,
        description_category_id=17028747,
        type_id=99385,
        attr_list=_schema(),
        dict_lookup=_dict_lookup(),
        images=draft["images"],
        ozon_client_id="test_client",
        ozon_api_key="test_key",
        weight_grams=200,
        dimensions=draft["dimensions"],
        price_rub="1990",
        old_price_rub="2390",
        currency_code="RUB",
        token="sk-test",
    )

    def _fake_post(client_id=None, api_key=None, endpoint="", body=None, language="RU"):
        if endpoint.endswith("values/search") and body.get("attribute_id") == 8229:
            return {"result": [{"id": 99385, "value": "Инсектициды"}]}
        return {"result": []}

    with patch("utils.ozon_client.ozon_post", side_effect=_fake_post):
        items = _validate_and_enrich_items(
            items=items,
            attr_list=_schema(),
            dict_lookup=_dict_lookup(),
            images=draft["images"],
            ozon_client_id="test_client",
            ozon_api_key="test_key",
            description_category_id=17028747,
            type_id=99385,
            weight_grams=200,
            dimensions=draft["dimensions"],
            draft_title=draft["title"],
            supplier="深圳市杀虫剂有限公司",
            ru_category_path="Товары для дома>Средства от насекомых>Инсектициды",
        )
    am = {int(a["id"]): a for a in items[0]["attributes"] if isinstance(a, dict) and a.get("id")}
    if 8229 in am:
        vals = am[8229].get("values") or []
        assert vals and vals[0].get("dictionary_value_id") == 99385
        assert "Инсектициды" == vals[0].get("value")
        assert "杀虫剂" not in str(vals[0].get("value"))


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: 异常")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
