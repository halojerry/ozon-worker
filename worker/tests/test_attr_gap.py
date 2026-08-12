"""Phase 0: 属性缺口量化工具单测（test_attr_gap.py）。

锁定 should_fill 的否定/肯定用例 + compute_gap 会计 + source_hint 推导。
纯函数测试，无需 PG/GPU/网络。
"""
import os
import sys
import tempfile

# 测试环境路径注入（与仓库其他 worker 测试一致）
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
for _p in (_SRC,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from utils.attr_gap import (  # noqa: E402
    compute_gap,
    is_system_generated,
    should_fill,
    summarize_gaps,
)


def _attr(aid, name="x", dict_id=0, req=False, atype="String"):
    return {"id": aid, "name": name, "dictionary_id": dict_id,
            "is_required": req, "type": atype}


# ── is_system_generated / should_fill ──

def test_should_fill_negatives_customs():
    """海关编码属性不应计入应填（ID 命中）。"""
    a = _attr(22604, "欧亚经济联盟的HS编码")
    assert is_system_generated(a) is True
    assert should_fill(a) is False


def test_should_fill_negatives_customs_by_name():
    """海关编码按名称关键词命中。"""
    a = _attr(99999, "ТН ВЭД 海关编码")
    assert is_system_generated(a) is True


def test_should_fill_negatives_system_generated():
    """系统生成属性不应计入：标记码/型号/简介/名称/hashtag/危险品。"""
    for aid in (23536, 9048, 4191, 4180, 23171, 9782):
        assert should_fill(_attr(aid)) is False, f"aid={aid} 应为系统生成"


def test_should_fill_negatives_brand_country():
    """品牌（强制 Нет бренда）与原产国（硬编码 Китай）不应计入。"""
    for aid in (85, 31, 5076, 4389):
        assert should_fill(_attr(aid)) is False, f"aid={aid} 应为强制默认"


def test_should_fill_negatives_free_text_non_source():
    """制造商(23487)/型号(22390) 有专用填充路径，非 1688 属性源。"""
    for aid in (23487, 22390):
        assert should_fill(_attr(aid)) is False, f"aid={aid} 应为专用路径"


def test_should_fill_positives_regular():
    """普通属性（字典/自由文本）应计入应填。"""
    assert should_fill(_attr(10096, "商品颜色", dict_id=1494)) is True
    assert should_fill(_attr(8962, "一个商品中的件数")) is True
    assert should_fill(_attr(8050, "成分")) is True


# ── compute_gap ──

def test_compute_gap_accounting():
    """schema 34 属性混合：过滤系统生成后应填数正确，filled 计数正确。"""
    schema = [
        _attr(22604, "HS编码"),           # 系统生成（海关）
        _attr(23536, "标记码"),           # 系统生成
        _attr(85, "品牌"),                # 强制默认
        _attr(4389, "原产国"),            # 强制默认
        _attr(10096, "商品颜色", dict_id=1494),  # 应填，已填
        _attr(8962, "件数"),              # 应填，未填
        _attr(8050, "成分"),              # 应填，未填
    ]
    draft = {"title": "蓝色杀虫剂", "attributes": {"颜色": "蓝色"}}
    report = compute_gap(schema, draft, filled_ids=[10096])

    assert report.total_schema == 7
    assert report.system_generated == 4
    assert report.should_fill == 3
    assert report.filled == 1
    assert len(report.gap_list) == 2
    assert report.attempted_fill_rate == round(1 / 3, 4)


def test_compute_gap_source_hint_from_1688_attr():
    """缺口属性能从 1688 属性名匹配 → from_1688_attr。"""
    schema = [_attr(10096, "商品颜色", dict_id=1494)]
    draft = {"attributes": {"颜色": "蓝色"}}
    report = compute_gap(schema, draft, filled_ids=[])
    assert report.gap_list[0]["source_hint"] == "from_1688_attr"


def test_compute_gap_source_hint_from_title():
    """缺口属性只能从标题弱源 → from_title。"""
    schema = [_attr(8050, "材质")]
    draft = {"title": "不锈钢材质宠物碗", "attributes": {}}
    report = compute_gap(schema, draft, filled_ids=[])
    assert report.gap_list[0]["source_hint"] == "from_title"


def test_compute_gap_source_hint_no_source():
    """完全无源 → no_source。"""
    schema = [_attr(8205, "保质期（天）")]
    draft = {"title": "宠物碗", "attributes": {}}
    report = compute_gap(schema, draft, filled_ids=[])
    assert report.gap_list[0]["source_hint"] == "no_source"


def test_compute_gap_source_hint_from_ozon_attrs():
    """竞品属性匹配 → from_ozon_attrs。"""
    schema = [_attr(10096, "商品颜色", dict_id=1494)]
    draft = {"attributes": {}, "ozon_attributes": {"Цвет": "Синий"}}
    report = compute_gap(schema, draft, filled_ids=[])
    assert report.gap_list[0]["source_hint"] == "from_ozon_attrs"


def test_compute_gap_filled_excludes_none():
    """filled_ids 传 None/空集不崩，全缺口。"""
    schema = [_attr(10096, "商品颜色", dict_id=1494), _attr(8962, "件数")]
    report = compute_gap(schema, {"attributes": {}}, filled_ids=None)
    assert report.filled == 0
    assert report.should_fill == 2


# ── summarize_gaps ──

def test_summarize_gaps():
    """多产品汇总：来源分布聚合 + top 缺口属性排序。"""
    schema = [_attr(10096, "商品颜色", dict_id=1494), _attr(8050, "材质")]
    r1 = compute_gap(schema, {"title": "蓝色材质碗", "attributes": {}}, filled_ids=[])
    r2 = compute_gap(schema, {"title": "蓝色材质碗", "attributes": {}}, filled_ids=[10096])
    agg = summarize_gaps([r1, r2])

    assert agg["products"] == 2
    assert agg["avg_schema"] == 2.0
    assert agg["avg_should_fill"] == 2.0
    assert "no_source" in agg["source_distribution"] or "from_title" in agg["source_distribution"]
    assert "10096" in agg["top_gap_attrs"]  # 颜色缺口出现
    assert agg["attempted_fill_rate"] == round(1 / 4, 4)  # 2 产品 4 应填 1 已填
