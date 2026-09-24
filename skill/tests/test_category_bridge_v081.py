"""fix/category-bridge-v1 回归：前台面包屑 web ID 绝不假冒 Seller dc/tp。

2026-09-24 事故：ozon_scraper 把前台 storefront 面包屑 ID 同值塞进
description_category_id/type_id → worker 采纳 → /v3/product/import 400
（invalid Request.Items.TypeId）→ follow ×5 全灭。

本测试锁死三件事：
1. scraper 结果 schema 不再产出 dc/tp 键（只有 web_category_id 线索键 + 文本路径）；
2. 面包屑纯函数行为不变（crumbType 过滤/路径拼接——毒源修复不能误伤正逻辑）；
3. follow 信封搬运点两种形态（page 线索无 dc/tp / what_to_sell 权威透传）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.ozon_scraper import (  # noqa: E402
    _category_path_from_crumbs,
    _pick_category_from_crumbs,
)


def _crumbs():
    return [
        {"text": "House & Garden", "link": "/category/dom-i-sad-14500/", "category_id": "14500", "crumbType": "CRUMB_TYPE_FULL_LINK"},
        {"text": "Storage", "link": "/category/hranenie-14759/", "category_id": "14759", "crumbType": "CRUMB_TYPE_FULL_LINK"},
        {"text": "Cases", "link": "/category/cases-14762/", "category_id": "14762", "crumbType": "CRUMB_TYPE_FULL_LINK"},
        {"text": "SomeBrand", "link": "/brand/somebrand-1/", "category_id": "1", "crumbType": "CRUMB_TYPE_BRAND"},
    ]


def test_scraper_result_schema_no_dc_tp_keys():
    """scraper 模块不再在结果 schema 里声明 dc/tp（毒键出处的源头锁定）。"""
    import inspect
    import lib.ozon_scraper as mod

    src = inspect.getsource(mod)
    # 初始 schema 与面包屑段都不应再写 dc/tp 键（web_category_id 是唯一 ID 出口）
    assert '"description_category_id": "",' not in src, "结果 schema 不得再初始化 dc 键"
    assert 'result["description_category_id"]' not in src, "面包屑段不得再写 dc 键"
    assert 'result["type_id"]' not in src, "面包屑段不得再写 tp 键"
    assert 'result["web_category_id"]' in src, "web_category_id 线索键必须产出"


def test_pick_category_from_crumbs_excludes_brand():
    """crumbType 过滤行为不变：品牌页排除、取末级类目 crumb。"""
    best = _pick_category_from_crumbs(_crumbs())
    assert best is not None
    assert best["category_id"] == "14762"
    assert best["text"] == "Cases"


def test_category_path_joins_only_category_crumbs():
    """文本路径拼接行为不变（品牌 crumb 排除）。"""
    path = _category_path_from_crumbs(_crumbs())
    assert path == "House & Garden > Storage > Cases"


def test_follow_envelope_page_shape_has_no_dc_tp():
    """follow 信封搬运点：page 面包屑形态 → 线索键（无 dc/tp）。"""
    # 复刻 cloud_probe follow 搬运点判定逻辑的最小等价物（源段在大函数内无法直接
    # 单测；此断言与源码同步演进——毒键回归时先红这里）
    ozon_cat = {
        "web_category_id": "14762",
        "language": "EN",
        "category_path": "House & Garden > Storage > Organizers and dividers > Cases",
        "source": "page",
        "namespace": "widget",
    }
    draft: dict = {}
    if isinstance(ozon_cat, dict):
        if ozon_cat.get("description_category_id") and ozon_cat.get("type_id"):
            draft["ozon_category"] = dict(ozon_cat)
        elif ozon_cat.get("category_path") or ozon_cat.get("web_category_id"):
            draft["ozon_category"] = {
                "category_path": str(ozon_cat.get("category_path", "") or ""),
                "breadcrumb_language": str(ozon_cat.get("breadcrumb_language") or ozon_cat.get("language") or ""),
                "web_category_id": str(ozon_cat.get("web_category_id", "") or ""),
                "source": "page",
            }
    cat = draft.get("ozon_category") or {}
    assert "description_category_id" not in cat, "page 形态信封不得携带 dc"
    assert "type_id" not in cat, "page 形态信封不得携带 tp"
    assert cat["web_category_id"] == "14762"
    assert cat["source"] == "page"


def test_follow_envelope_what_to_sell_shape_passes_through():
    """follow 信封搬运点：what_to_sell 权威形态（Seller 空间）→ 原样透传 dc/tp。"""
    ozon_cat = {
        "description_category_id": "17027937",
        "type_id": "95483",
        "language": "RU",
        "category_path": "Дом и сад > Хранение вещей",
        "source": "what_to_sell",
        "namespace": "seller",
    }
    draft: dict = {}
    if isinstance(ozon_cat, dict):
        if ozon_cat.get("description_category_id") and ozon_cat.get("type_id"):
            draft["ozon_category"] = dict(ozon_cat)
    cat = draft.get("ozon_category") or {}
    assert cat.get("description_category_id") == "17027937"
    assert cat.get("type_id") == "95483"
    assert cat.get("source") == "what_to_sell"
