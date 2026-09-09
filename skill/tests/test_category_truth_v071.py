"""v0.71 类目真值进信封单测：widget 面包屑派生 + discover 真值合并语义。

背景：discover 采集阶段 widget JS 不读 breadCrumbs → 候选类目恒空；
_apply_discover_page_truth 的 _has_dc 只看 candidate，draft 里 graph 阶段
search_kw 猜出的数字 dc/tp 会被 page Web-ID 空壳整体覆盖（数字丢失）。
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_widget import _derive_category_from_breadcrumbs  # noqa: E402
from scripts.cloud_probe import _apply_discover_page_truth  # noqa: E402


def _crumb(text, link, ctype="CRUMB_TYPE_FULL_LINK"):
    return {"text": text, "link": link, "crumbType": ctype}


def test_derive_category_from_breadcrumbs():
    result = {"breadcrumbs": [
        _crumb("Дом и сад", "/category/дом-и-сад-14500/"),
        _crumb("Мебель", "/category/mebel-15600/"),
        _crumb(" SomeBrand ", "/brand/brand-12345/"),  # 品牌段须排除
    ]}
    _derive_category_from_breadcrumbs(result)
    assert result["category_path"] == "Дом и сад > Мебель"
    assert result["web_category_id"] == "15600"
    assert result["breadcrumb_language"] == "RU"


def test_derive_category_no_breadcrumbs_noop():
    result = {"title": "x"}
    _derive_category_from_breadcrumbs(result)
    assert "category_path" not in result
    _derive_category_from_breadcrumbs({"breadcrumbs": "junk"})


def _cand(**kw):
    base = dict(
        ozon_url="https://www.ozon.ru/product/123",
        ozon_title="Полка",
        ozon_category={},
        weight_g=0,
        dimensions_mm={},
        page_category_path="",
        page_web_category_id="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_page_truth_keeps_draft_search_kw_numbers():
    """draft 已有 search_kw 数字 dc/tp → page 只补路径先验，数字不丢（覆盖 bug 回归）。"""
    draft = {"ozon_category": {
        "description_category_id": "88581504", "type_id": "93408",
        "source": "search_kw", "namespace": "seller",
    }}
    page_truth = {"ozon_category": {
        "category_path": "Дом и сад > Мебель", "breadcrumb_language": "RU",
        "web_category_id": "15600", "source": "page", "namespace": "widget",
    }}
    _apply_discover_page_truth(draft, {}, _cand(), page_truth)
    cat = draft["ozon_category"]
    assert cat["description_category_id"] == "88581504"  # 数字保留
    assert cat["type_id"] == "93408"
    assert cat["source"] == "search_kw"  # 语义不变（非权威直通）
    assert cat["category_path"] == "Дом и сад > Мебель"  # 路径先验补上


def test_page_truth_takes_candidate_numbers_over_draft():
    """candidate（what_to_sell）数字优先于 draft search_kw。"""
    draft = {"ozon_category": {
        "description_category_id": "111", "type_id": "222",
        "source": "search_kw", "namespace": "seller",
    }}
    cand = _cand(ozon_category={
        "description_category_id": "85282223", "type_id": "970988646",
        "source": "what_to_sell", "namespace": "seller",
    })
    _apply_discover_page_truth(draft, {}, cand, {"ozon_category": {
        "category_path": "Хозтовары", "breadcrumb_language": "RU",
    }})
    cat = draft["ozon_category"]
    assert cat["description_category_id"] == "85282223"
    assert cat["source"] == "what_to_sell"


def test_page_truth_shell_when_no_numbers():
    """无任何数字 → page 空壳（source=page，worker 按 hint 路径解析）。"""
    draft = {}
    _apply_discover_page_truth(draft, {}, _cand(), {"ozon_category": {
        "category_path": "Дом > Кухня", "breadcrumb_language": "RU",
        "web_category_id": "15600",
    }})
    cat = draft["ozon_category"]
    assert cat["source"] == "page"
    assert "description_category_id" not in cat
    assert cat["category_path"] == "Дом > Кухня"


def test_candidate_breadcrumb_fallback_without_truth_scrape():
    """真值二次抓取失败时，候选级面包屑（采集阶段派生）兜底为路径先验。"""
    draft = {}
    cand = _cand(
        page_category_path="Дом и сад > Хранение",
        page_web_category_id="15600",
    )
    _apply_discover_page_truth(draft, {}, cand, {})
    cat = draft["ozon_category"]
    assert cat["source"] == "page"
    assert cat["category_path"] == "Дом и сад > Хранение"
    assert cat["web_category_id"] == "15600"


def test_draft_numbers_kept_without_any_page_info():
    """真值与候选面包屑全无 → draft search_kw 数字原样保留（原 elif 分支语义）。"""
    draft = {"ozon_category": {
        "description_category_id": "88581504", "type_id": "93408",
        "source": "search_kw", "namespace": "seller",
    }}
    _apply_discover_page_truth(draft, {}, _cand(), {})
    assert draft["ozon_category"]["description_category_id"] == "88581504"


# ── F-B04: R1 单字回退（官方译名一字之差不误杀）──

def test_guess_consistent_near_synonym_rescued():
    """'保暖杯'（官方译名）vs 来源'保温杯'：bigram 零重叠但单字重叠 2/3 → 一致（不再错杀）。"""
    from scripts.cloud_probe import _category_guess_consistent
    assert _category_guess_consistent(
        "保暖杯", "Термос 0.5л для напитков", "日用餐厨饮具 > 饮水用具 > 保温杯") is True


def test_guess_consistent_poison_still_blocked():
    """毒类目防线不放松：金属管（尾字'管'）vs 金属桶 → R2 尾字拦；跨语言 → R3 拦。"""
    from scripts.cloud_probe import _category_guess_consistent
    assert _category_guess_consistent(
        "金属管", "трубa стальная", "包装 > 金属包装容器 > 金属桶") is False
    assert _category_guess_consistent(
        "Труба металлическая", "汽油桶 加厚", "包装 > 金属包装容器 > 金属桶") is False
