# -*- coding: utf-8 -*-
"""
v0.32 属性名词汇分歧匹配回归测试（TDD: RED → GREEN）

背景：1688 中文属性名与 Ozon schema（ZH_HANS）属性名存在词汇分歧——
1688「适用场景」vs Ozon「用途」、1688「材料」vs Ozon「材质」、
1688「风格」vs Ozon「款式」。assemble `_match_product_attr` 原匹配
（精确 / 包含 / 空格 split 分词）对无空格中文完全失效，且同义词表
attr_synonyms.json 只被 prepare `_fill_optional_dict_attrs` 消费，
不进 assemble → 日志实证「属性映射数=0」。

修复链路：
1. utils/attr_synonyms.py：共享同义词加载器 load_attr_synonyms()
2. utils/attribute_utils.py：match_attr_name_synonym()（同组双向包含）
3. assemble `_match_product_attr`：jieba 分词子串重叠层 + 同义词层
4. prepare `_fill_optional_dict_attrs`：改用共享加载器
5. config/attr_synonyms.json：扩充高频分歧词对

运行：cd worker && PYTHONPATH=src python3 tests/test_attr_synonym_match.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── 同义词组双向包含匹配（match_attr_name_synonym） ──

def test_synonym_use_divergence():
    """Ozon「用途」vs 1688「适用场景」：use 组双向命中 → 返回 1688 属性名"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("用途", ["适用场景"], syn) == "适用场景"


def test_synonym_material_divergence():
    """Ozon「材质」vs 1688「材料」：非子串同义词 → 命中（material 组）"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("材质", ["材料"], syn) == "材料"
    assert match_attr_name_synonym("材料", ["主要材质"], syn) == "主要材质"


def test_synonym_season_divergence():
    """Ozon「季节」vs 1688「适用季节」：season 组 → 命中"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("季节", ["适用季节"], syn) == "适用季节"


def test_synonym_style_divergence():
    """Ozon「款式」vs 1688「风格」：style 组 ozon 侧需含「款式」→ 命中"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("款式", ["风格"], syn) == "风格"


def test_synonym_color_divergence():
    """Ozon「颜色」vs 1688「颜色分类」：color 组（新增）→ 命中"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("颜色", ["颜色分类"], syn) == "颜色分类"


def test_synonym_type_divergence():
    """Ozon「型号」vs 1688「产品类型」：type/модель 组（新增）→ 命中"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("型号", ["产品类型"], syn) == "产品类型"


def test_synonym_quantity_divergence():
    """Ozon「数量」vs 1688「件数」：quantity 组（新增）→ 命中"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("数量", ["件数"], syn) == "件数"


# ── 负例：宽松单侧命中不匹配（防错误值） ──

def test_no_match_cross_group():
    """Ozon「材质」(material) vs 1688「风格」(style)：跨组单侧命中 → None"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("材质", ["风格"], syn) is None


def test_no_match_one_sided():
    """Ozon「材质」命中 material ozon 关键词，但 1688 名属于其他组 → None"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("材质", ["适用性别"], syn) is None


def test_no_match_unrelated():
    """完全无关 → None"""
    from utils.attribute_utils import match_attr_name_synonym
    from utils.attr_synonyms import load_attr_synonyms
    syn = load_attr_synonyms()
    assert match_attr_name_synonym("重量", ["品牌"], syn) is None


# ── 管道级：_build_items_deterministically 词汇分歧属性进入输出 ──

def _pipeline_attr_map(draft_attrs, schema):
    from graphs.nodes.assemble_ozon_product_node import _build_items_deterministically
    draft = {
        "item_id": "test002",
        "title": "测试产品",
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": draft_attrs,
        "sku_id": "test002",
        "price": "1990",
        "original_price": "2390",
    }
    items = _build_items_deterministically(
        draft=draft,
        description_category_id=17028830,
        type_id=971206780,
        attr_list=schema,
        dict_lookup={},
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
    return {int(a["id"]): a for a in items[0]["attributes"]}


def test_pipeline_synonym_material():
    """1688「材料」→ schema「材质」（非子串分歧）经同义词组匹配进入输出"""
    schema = [{"id": 55555, "name": "材质", "dictionary_id": 0, "is_required": False}]
    am = _pipeline_attr_map({"材料": "ABS"}, schema)
    assert 55555 in am, "词汇分歧属性应经同义词组匹配进入输出"
    assert am[55555]["values"][0]["value"] == "ABS"


def test_pipeline_jieba_token_overlap():
    """jieba 分词层：schema「商品材质」vs 1688「主要材质」——既非子串又无同义词组，
    靠共享 token「材质」命中（jieba 对无空格中文替代失效的 .split()）"""
    schema = [{"id": 55556, "name": "商品材质", "dictionary_id": 0, "is_required": False}]
    am = _pipeline_attr_map({"主要材质": "ABS"}, schema)
    assert 55556 in am, "jieba 共享 token 应匹配"
    assert am[55556]["values"][0]["value"] == "ABS"


def test_pipeline_negative_divergent_still_skipped():
    """无关属性名不被引入：1688「重量」vs schema「用途」→ 不匹配（保持跳过）"""
    schema = [{"id": 55557, "name": "用途", "dictionary_id": 0, "is_required": False}]
    am = _pipeline_attr_map({"重量": "500g"}, schema)
    assert 55557 not in am, "无关属性名不应被错误匹配"


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
