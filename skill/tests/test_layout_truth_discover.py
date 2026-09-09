"""data-pool parity Task 6.2：layout_tracking 真值进 discover 类目先验。

背景：fetch_product_info（Task 6.1）新增可选 ``layout_tracking`` 三键
``{categoryId, category_path, breadcrumbs}``（key 缺席 = 无真值；categoryId
可为 None）。接线点 = discover 采集单口 ``_analyze_product``：面包屑派生
（category_path/web_category_id）缺位时回填，走既有
``_apply_discover_page_truth`` 候选级 page 先验通道（最弱 page 派生，
绝不覆盖既有真值）。
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import (  # noqa: E402
    _analyze_product,
    _layout_truth_to_page_prior,
)


# ── 1. 提取/归一 helper 纯单测 ────────────────────────────────────────────


def test_layout_prior_joins_category_path_with_page_separator():
    """category_path 列表按 page 先验同分隔符 ' > ' 拼接，空段剔除。"""
    prior = _layout_truth_to_page_prior({
        "categoryId": 15600,
        "category_path": ["Дом и сад", "Мебель", "", "  "],
        "breadcrumbs": [{"text": "应被忽略", "link": "/x"}],
    })
    assert prior == {
        "page_category_path": "Дом и сад > Мебель",
        "web_category_id": 15600,
    }


def test_layout_prior_category_id_none_gives_none_field():
    """categoryId=None（Task 6.1 已知形态）→ web_category_id 字段 None，不抛。"""
    prior = _layout_truth_to_page_prior({
        "categoryId": None,
        "category_path": ["Дом", "Кухня"],
        "breadcrumbs": [],
    })
    assert prior is not None
    assert prior["page_category_path"] == "Дом > Кухня"
    assert prior["web_category_id"] is None


def test_layout_prior_rejects_non_positive_category_id():
    """仅正 int 作旁证：0/负数/非数字串/bool 一律 None 字段（数字串宽容转 int）。"""
    for bad in (0, -5, "abc", "", None, True):
        prior = _layout_truth_to_page_prior({
            "categoryId": bad, "category_path": ["A"], "breadcrumbs": []})
        assert prior is not None, f"路径仍在，不应整体 None（bad={bad!r}）"
        assert prior["web_category_id"] is None, f"bad={bad!r}"
    prior = _layout_truth_to_page_prior({
        "categoryId": "15600", "category_path": [], "breadcrumbs": []})
    assert prior == {"page_category_path": "", "web_category_id": 15600}


def test_layout_prior_breadcrumbs_fallback():
    """category_path 空 → breadcrumbs 兜底（dict 取 text，字符串原样）。"""
    prior = _layout_truth_to_page_prior({
        "categoryId": None,
        "category_path": [],
        "breadcrumbs": [
            {"text": "Дом и сад", "link": "/category/dom-14500/"},
            "Мебель",
            {"text": "", "link": "/category/empty-1/"},
        ],
    })
    assert prior == {
        "page_category_path": "Дом и сад > Мебель",
        "web_category_id": None,
    }


def test_layout_prior_none_on_no_usable_truth():
    """无可用真值（非 dict / 全空 / 缺键）→ None，调用方零改动。"""
    assert _layout_truth_to_page_prior(None) is None
    assert _layout_truth_to_page_prior("junk") is None
    assert _layout_truth_to_page_prior({}) is None
    assert _layout_truth_to_page_prior({
        "categoryId": None, "category_path": [], "breadcrumbs": []}) is None
    assert _layout_truth_to_page_prior({
        "categoryId": 0, "category_path": [""], "breadcrumbs": [" "],
    }) is None


# ── 2. 集成（mock fetch_product_info）：_analyze_product 接线 ─────────────


def _info(**kw):
    base = dict(
        title="Полка настенная", price="1500", cardPrice="1500",
        originalPrice="", images=["https://cdnoz/1.jpg"], brand="X",
        rating=4.8, reviewCount=10, category_path="", web_category_id="",
    )
    base.update(kw)
    return base


_SELLERS = {"count": 0, "min_price": 0, "sellers": []}


def _run_analyze(monkeypatch, info, sellers=None):
    monkeypatch.setattr("scripts.lib.ozon_widget.fetch_product_info",
                        lambda *a, **k: info)
    monkeypatch.setattr("scripts.lib.ozon_widget.fetch_competing_sellers",
                        lambda *a, **k: (sellers or _SELLERS))
    return _analyze_product("", None, "123")


def test_analyze_fills_page_prior_from_layout_when_breadcrumbs_absent(monkeypatch):
    """面包屑派生缺位 → layout 真值回填候选 page 先验字段。"""
    cand = _run_analyze(monkeypatch, _info(layout_tracking={
        "categoryId": 15600,
        "category_path": ["Дом и сад", "Хранение"],
        "breadcrumbs": [],
    }))
    assert cand.status == "ok"
    assert cand.page_category_path == "Дом и сад > Хранение"
    assert cand.page_web_category_id == "15600"


def test_analyze_keeps_breadcrumb_truth_over_layout(monkeypatch):
    """面包屑派生已在 → layout 不覆盖既有真值（最弱 page 派生纪律）。"""
    cand = _run_analyze(monkeypatch, _info(
        category_path="Дом и сад > Мебель", web_category_id="15600",
        layout_tracking={
            "categoryId": 999,
            "category_path": ["Другое", "Совсем другое"],
            "breadcrumbs": [],
        }))
    assert cand.page_category_path == "Дом и сад > Мебель"
    assert cand.page_web_category_id == "15600"


def test_analyze_backfills_missing_web_id_from_layout(monkeypatch):
    """面包屑路径在但 web id 派生失败 → layout 正 int categoryId 旁证回填。"""
    cand = _run_analyze(monkeypatch, _info(
        category_path="Дом и сад > Мебель",
        layout_tracking={
            "categoryId": 15600, "category_path": ["Иной", "Путь"],
            "breadcrumbs": [],
        }))
    assert cand.page_category_path == "Дом и сад > Мебель"  # 路径不覆盖
    assert cand.page_web_category_id == "15600"             # 旁证补缺口


def test_analyze_zero_change_without_layout_tracking(monkeypatch):
    """layout_tracking 缺席 → 行为与接线前逐字一致（无新值无异常）。"""
    cand = _run_analyze(monkeypatch, _info())
    assert cand.status == "ok"
    assert cand.page_category_path == ""
    assert cand.page_web_category_id == ""


def test_analyze_category_id_none_never_forwarded(monkeypatch):
    """categoryId=None 绝不透传成 'None' 串（Task 6.1 评审红线）。"""
    cand = _run_analyze(monkeypatch, _info(layout_tracking={
        "categoryId": None, "category_path": ["Дом", "Кухня"],
        "breadcrumbs": [],
    }))
    assert cand.page_category_path == "Дом > Кухня"
    assert cand.page_web_category_id == ""


# ── 3. 既有优先级阶梯：layout 派生候选走 _apply_discover_page_truth 通道 ──


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


def test_layout_derived_candidate_joins_page_truth_channel():
    """layout 回填的候选字段经既有通道落 draft（source=page，worker 按 hint）。"""
    from scripts.cloud_probe import _apply_discover_page_truth

    draft = {}
    cand = _cand(page_category_path="Дом и сад > Хранение",
                 page_web_category_id="15600")
    _apply_discover_page_truth(draft, {}, cand, {})
    cat = draft["ozon_category"]
    assert cat["source"] == "page"
    assert cat["category_path"] == "Дом и сад > Хранение"
    assert cat["web_category_id"] == "15600"
    assert "description_category_id" not in cat  # 绝不伪造 dc/tp


def test_layout_derive_never_overrides_authoritative_page_truth():
    """真值二次抓取已有类目 → layout 派生候选字段整体让位（既有优先级不动）。"""
    from scripts.cloud_probe import _apply_discover_page_truth

    draft = {}
    cand = _cand(page_category_path="Дом и сад > Хранение",
                 page_web_category_id="15600")
    _apply_discover_page_truth(draft, {}, cand, {"ozon_category": {
        "category_path": "Хозтовары > Другое", "breadcrumb_language": "RU",
        "web_category_id": "777", "source": "page", "namespace": "widget",
    }})
    cat = draft["ozon_category"]
    assert cat["category_path"] == "Хозтовары > Другое"
    assert cat["web_category_id"] == "777"
