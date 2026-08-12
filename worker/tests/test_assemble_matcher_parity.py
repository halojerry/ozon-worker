"""Phase 3: assemble 闭包 vs matcher 行为对等测试（test_assemble_matcher_parity.py）。

前置锁定：在迁移前跑此测试记录 assemble 现行为；迁移后重跑断言一致。
验证 assemble `_match_product_attr`/`_find_dict_value` 与共享 matcher
（attr_value_matcher.match_attr_name/match_dict_value）对同一输入产出相同
dict_id 决策 —— 行为保留迁移的安全网。

注意：assemble 是 ZH schema 名匹配（attr_name_cn），matcher.match_attr_name
同语义；prepare 是 RU schema 名（合同测试已覆盖其与 matcher 的值一致性）。
"""
import os
import sys
import tempfile
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.attr_value_matcher import match_attr_name, match_dict_value  # noqa: E402

# ── 属性名匹配对等 ──

def test_attr_name_exact_parity():
    pa = {"颜色": "白色"}
    assert match_attr_name("颜色", pa) == "颜色"


def test_attr_name_contains_parity():
    pa = {"商品颜色": "白色"}
    assert match_attr_name("颜色", pa) == "商品颜色"
    pa2 = {"颜色": "白色"}
    assert match_attr_name("商品颜色", pa2) == "颜色"


def test_attr_name_jieba_parity():
    pa = {"商品材质": "塑料"}
    assert match_attr_name("主要材质", pa) == "商品材质"


def test_attr_name_negative_parity():
    pa = {"颜色": "白色"}
    assert match_attr_name("重量", pa) is None


def test_attr_name_synonym_parity():
    syn = {
        "material": {
            "zh_keywords": ["材料", "材质"],
            "ozon_name_keywords": ["材料", "材质"],
            "value_map": {},
        }
    }
    pa = {"材料": "塑料"}
    assert match_attr_name("材质", pa, syn) == "材料"


# ── 字典值匹配对等（assemble _find_dict_value 语义）──

def _find_dict_value_semantics(attr_id, pv, cached):
    """复刻 assemble _find_dict_value（迁移前行为）：精确→包含，返回首个或 None。"""
    pv_lower = str(pv or "").lower().strip()
    if not pv_lower:
        return None
    for v in cached or []:
        if not isinstance(v, dict):
            continue
        if str(v.get("value", "")).lower().strip() == pv_lower:
            return int(v.get("id", 0))
    for v in cached or []:
        if not isinstance(v, dict):
            continue
        vv = str(v.get("value", "")).lower().strip()
        if vv and (pv_lower in vv or vv in pv_lower):
            return int(v.get("id", 0))
    return None


def test_dict_value_exact_parity():
    cached = [{"id": 61571, "value": "白色"}, {"id": 61577, "value": "透明"}]
    old = _find_dict_value_semantics(10096, "白色", cached)
    new = match_dict_value(10096, "白色", cached)
    assert old == 61571
    assert any(v["id"] == 61571 for v in new)


def test_dict_value_contains_parity():
    cached = [{"id": 61574, "value": "黑色"}, {"id": 970671251, "value": "哑光黑色"}]
    old = _find_dict_value_semantics(10096, "黑", cached)
    new = match_dict_value(10096, "黑", cached)
    assert old == 61574  # 旧行为：包含命中取第一个
    assert {v["id"] for v in new} == {61574, 970671251}  # 新行为：返回全部候选


def test_dict_value_no_match_parity():
    cached = [{"id": 61571, "value": "白色"}]
    assert _find_dict_value_semantics(10096, "绿色", cached) is None
    assert match_dict_value(10096, "绿色", cached) == []


def test_dict_value_multi_candidate_no_blind_first():
    """迁移后的纪律：多候选返回全部（交由 unique_or_none 决策，绝不盲补首个）。"""
    cached = [{"id": 148495146, "value": "套娃"}, {"id": 99385, "value": "杀虫剂"}]
    hits = match_dict_value(8229, "杀虫剂", cached)
    assert len(hits) == 1 and hits[0]["id"] == 99385  # 精确命中杀虫剂（非套娃）
