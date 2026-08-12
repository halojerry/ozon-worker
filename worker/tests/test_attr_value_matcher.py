"""Phase 1: 属性值匹配纯函数层单测（test_attr_value_matcher.py）。

镜像三处既有锁定行为（v0.13/v0.30/v0.32 纪律）：
- match_attr_name：精确/包含/jieba/同义词/负例
- match_dict_value：归一化精确/包含、多候选全返回、空值
- unique_or_none：唯一命中/多候选降级/危险品安全默认/is_aspect
- clean_dict_value：中文清零
- lang_route：CJK→ZH_HANS / RU→RU
纯函数测试，无需 PG/GPU/网络。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.attr_value_matcher import (  # noqa: E402
    clean_dict_value,
    has_chinese,
    lang_route,
    match_attr_name,
    match_dict_value,
    normalize_text,
    resolve_cached,
    unique_or_none,
)


def _v(vid, value):
    return {"id": vid, "value": value}


# ── normalize_text / has_chinese ──

def test_normalize_text():
    assert normalize_text(" 白 色 ") == "白 色"
    assert normalize_text("White") == "white"
    assert normalize_text("") == ""


def test_has_chinese():
    assert has_chinese("白色") is True
    assert has_chinese("Белый") is False
    assert has_chinese("") is False
    assert has_chinese(None) is False


# ── lang_route ──

def test_lang_route():
    assert lang_route("白色") == "ZH_HANS"
    assert lang_route("杀虫") == "ZH_HANS"
    assert lang_route("Белый") == "RU"
    assert lang_route("insecticide") == "RU"


# ── match_attr_name ──

def test_match_attr_name_exact():
    assert match_attr_name("颜色", {"颜色": "白色"}) == "颜色"


def test_match_attr_name_contains():
    assert match_attr_name("商品颜色", {"颜色": "白色"}) == "颜色"
    assert match_attr_name("颜色", {"商品颜色": "白色"}) == "商品颜色"


def test_match_attr_name_jieba():
    assert match_attr_name("主要材质", {"商品材质": "塑料"}) == "商品材质"


def test_match_attr_name_synonym():
    syn = {
        "material": {
            "zh_keywords": ["材料", "材质"],
            "ozon_name_keywords": ["材料", "材质"],
            "value_map": {},
        }
    }
    assert match_attr_name("材质", {"材料": "塑料"}, syn) == "材料"


def test_match_attr_name_no_match():
    assert match_attr_name("重量", {"颜色": "白色"}) is None
    assert match_attr_name("", {"颜色": "白色"}) is None
    assert match_attr_name("用途", {"颜色": "白色"}, {}) is None


# ── match_dict_value ──

def test_match_dict_value_exact():
    hits = match_dict_value(10096, "白色", [_v(61571, "白色"), _v(61577, "透明")])
    assert len(hits) == 1 and hits[0]["id"] == 61571


def test_match_dict_value_exact_normalized():
    hits = match_dict_value(10096, "白色", [_v(61571, "白色 "), _v(61577, "透明")])
    assert len(hits) == 1 and hits[0]["id"] == 61571


def test_match_dict_value_contains():
    hits = match_dict_value(10096, "黑", [_v(61574, "黑色"), _v(970671251, "哑光黑色")])
    assert len(hits) == 2  # 包含匹配返回全部候选


def test_match_dict_value_empty_value():
    assert match_dict_value(10096, "", [_v(61571, "白色")]) == []
    assert match_dict_value(10096, " ", [_v(61571, "白色")]) == []
    assert match_dict_value(10096, "白色", []) == []


def test_match_dict_value_no_match():
    assert match_dict_value(10096, "绿色", [_v(61571, "白色")]) == []


# ── unique_or_none ──

def test_unique_hit():
    res = unique_or_none(10096, "商品颜色", [_v(61571, "白色")])
    assert res.status == "matched"
    assert res.match_layer == "unique"
    assert res.dictionary_value_id == 61571
    assert res.value == ""  # 中文值清零（dict_id 权威）


def test_multi_candidate_no_blind_first():
    """多候选绝不取第一个（v0.13 套娃教训）→ llm_eligible 待消歧。"""
    res = unique_or_none(8229, "类型", [_v(148495146, "套娃"), _v(99385, "杀虫剂")])
    assert res.status == "llm_eligible"
    assert res.dictionary_value_id == 0
    assert len(res.candidates) == 2


def test_no_match_skip():
    res = unique_or_none(10096, "商品颜色", [])
    assert res.status == "skipped"
    assert res.reason == "no_match"


def test_hazard_safe_only():
    """9782 危险品：只挑非危险安全默认，绝不取第一个。"""
    cands = [_v(26026953, "Класс 1 爆炸物"), _v(26026954, "не опасен")]
    res = unique_or_none(9782, "产品危险等级", cands)
    assert res.status == "matched"
    assert res.match_layer == "hazard_safe"
    assert res.dictionary_value_id == 26026954
    assert "взрыв" not in res.value.lower()  # 非爆炸类安全默认


def test_hazard_no_safe_default_skip():
    cands = [_v(26026953, "Класс 1 爆炸物"), _v(26026955, "Класс 2 易燃")]
    res = unique_or_none(9782, "产品危险等级", cands)
    assert res.status == "skipped"
    assert res.reason == "hazard_no_safe_default"


def test_aspect_skip():
    res = unique_or_none(9048, "型号名称（合并为一张商品卡片）", [_v(1, "x")], aspect_skip=True)
    assert res.status == "aspect_skipped"


# ── clean_dict_value ──

def test_clean_dict_value_chinese_zeroed():
    """字典属性 id>0 且值含中文 → 清空（dict_id 权威，中文会被 Ozon 拒）。"""
    assert clean_dict_value(61571, "白色") == ""
    assert clean_dict_value(61571, "Белый") == "Белый"
    assert clean_dict_value(0, "白色") == "白色"  # 自由文本保留


# ── resolve_cached ──

def test_resolve_cached_full_path():
    res = resolve_cached(10096, "商品颜色", "白色", [_v(61571, "白色")])
    assert res.status == "matched" and res.dictionary_value_id == 61571


def test_resolve_cached_no_source():
    res = resolve_cached(10096, "商品颜色", "", [_v(61571, "白色")])
    assert res.status == "no_source"
