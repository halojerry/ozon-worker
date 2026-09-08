#!/usr/bin/env python3
"""discover 提交前页面真值富化（路径先验 + what_to_sell > search_kw）回归。

背景：discover 管线（Ozon 现成商品）信封此前缺 Ozon 地面真值（特征属性、
重量尺寸）——build_envelope_from_discovery 组装时不读 candidate.ozon_url，
what_to_sell 类目又经常空 {}。⚠️ 实测（Wave D）：页面面包屑里的 ID 是
**Web 前台类目 ID**，不是 Seller 树的 description_category_id/type_id——
拿它当 dc 传 worker 后 schema 验证 400、树查询全不命中。page 真值从此
只承载「路径先验 + 特征属性 + 物理真值」，绝不伪造 dc/tp。本文件锁定：

1. page 面包屑 → category_path/breadcrumb_language 写入 draft.ozon_category
   同 dict（worker 名称解析先验）；web 前台 ID 只放 web_category_id 作排查
   线索，绝不出现 description_category_id/type_id 键
2. what_to_sell 权威 dc/tp 在场 → 语义完全不动，page 只并路径先验
3. 两者都无 dc/tp → 除 ozon_url/ozon_title 外与现状逐字段一致
4. draft.ozon_attributes / ozon_attributes_category / ozon_url / ozon_title 到位；
   build_graph_envelope_with_retry 抛异常的降级信封同样带页面真值
5. 无 Chrome（scrape 抛异常）→ 不崩、走兜底
6. P-D：follow_sell=True 信封 extensions 带 follow_type=discover（discover
   变体标记，worker 据此跳过 import-by-sku）；已有值不覆盖

全部 mock _cached_ozon_scrape，禁止真实网络/浏览器。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discover_page_truth.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402
from scripts import cloud_probe  # noqa: E402

OZON_URL = "https://www.ozon.ru/product/avtopoilka-4767514314/"

# 模拟 _cached_ozon_scrape 成功结果（ozon_scraper.scrape_ozon_product_via_cdp 形状）
PAGE_SCRAPE = {
    "success": True,
    "product_id": "4767514314",
    "title": "Автопоилка для кошек 2л",
    "images": ["https://cdn.ozon.ru/1.jpg"],
    "price": "1290 ₽",
    "description_category_id": "17028929",
    "type_id": "504866264",
    "breadcrumb_language": "RU",
    "category_path": "Зоотовары > Кошки > Автопоилки",
    "attributes": {"Цвет": "Белый", "Вес товара, г": "270 г"},
    "characteristics": [{"title": "Габариты упаковки", "value": "190*64*230 мм"}],
}

WTS_CATEGORY = {
    "description_category_id": "99999999",
    "type_id": "88888888",
    "source": "what_to_sell",
    "namespace": "seller",
}


def _mk_candidate(**overrides) -> ProductCandidate:
    base = dict(
        ozon_product_id="4767514314",
        ozon_title="Автопоилка для кошек 2л",
        ozon_price=1290.0,
        ozon_url=OZON_URL,
        match_1688_url="https://detail.1688.com/offer/980815374096.html",
        competing_sellers=4,
        weight_g=0,
        dimensions_mm={},
        ozon_category={},
    )
    base.update(overrides)
    c = ProductCandidate(
        ozon_product_id=base["ozon_product_id"],
        ozon_title=base["ozon_title"],
        ozon_price=base["ozon_price"],
    )
    c.ozon_url = base["ozon_url"]
    c.match_1688_url = base["match_1688_url"]
    c.competing_sellers = base["competing_sellers"]
    c.weight_g = base["weight_g"]
    c.dimensions_mm = base["dimensions_mm"]
    c.ozon_category = base["ozon_category"]
    return c


def _mock_envelope() -> dict:
    """mock build_graph_envelope_with_retry 返回（1688 主链路信封）。"""
    return {
        "token": "sk-test",
        "ozon_client_id": "123",
        "ozon_api_key": "key",
        "envelope": {
            "draft": {
                "item_id": "980815374096", "title": "x", "images": [],
                "weight": 0, "dimensions": {"length": 0, "width": 0, "height": 0},
            },
            "source": {"purchase_url": "u", "purchase_cost": 1.0},
            "extensions": {},
        },
    }


def _build(cand, *, scrape_result=None, scrape_error=None,
           retry_envelope=None, retry_error=False):
    """统一 mock 入口：_cached_ozon_scrape + build_graph_envelope_with_retry。"""

    def _scrape(url, **kwargs):
        if scrape_error is not None:
            raise scrape_error
        return dict(scrape_result) if isinstance(scrape_result, dict) else scrape_result

    if retry_error:
        retry_patch = mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                                        side_effect=RuntimeError("1688 CDP 降级"))
    else:
        retry_patch = mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                                        return_value=(retry_envelope or _mock_envelope()))
    with mock.patch.object(cloud_probe, "_cached_ozon_scrape", side_effect=_scrape), \
         retry_patch, \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        return cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})


def test_what_to_sell_dc_kept_and_path_hint_merged():
    """page 面包屑不再伪造 dc/tp：what_to_sell 权威 dc/tp 原样保留，
    面包屑只贡献 category_path/breadcrumb_language 路径先验（并入同 dict）。"""
    cand = _mk_candidate(ozon_category=dict(WTS_CATEGORY))
    res = _build(cand, scrape_result=PAGE_SCRAPE)
    assert res is not None
    cat = res["envelope"]["draft"]["ozon_category"]
    # what_to_sell 语义完全不动
    assert cat["description_category_id"] == "99999999"
    assert cat["type_id"] == "88888888"
    assert cat["source"] == "what_to_sell"
    assert cat["namespace"] == "seller"
    # 路径先验并入同 dict
    assert cat["category_path"] == "Зоотовары > Кошки > Автопоилки"
    assert cat["breadcrumb_language"] == "RU"
    # web 前台 ID 绝不冒充 dc/tp（v0.71 起作为独立排查线索键并入 cat，语义不变）
    assert "17028929" not in (cat.get("description_category_id") or "")
    assert cat.get("web_category_id") == "17028929"
    assert cat["description_category_id"] != cat["web_category_id"]


def test_page_category_is_hint_only_no_dc_fabrication():
    """无 what_to_sell + page 面包屑在场 → draft.ozon_category 只含路径先验
    （source=page/namespace=widget），绝不出现 dc/tp 键（P-A' 核心回归）。"""
    cand = _mk_candidate(ozon_category={})
    res = _build(cand, scrape_result=PAGE_SCRAPE)
    assert res is not None
    cat = res["envelope"]["draft"]["ozon_category"]
    assert cat["source"] == "page"
    assert cat["namespace"] == "widget"
    assert cat["category_path"] == "Зоотовары > Кошки > Автопоилки"
    assert cat["breadcrumb_language"] == "RU"
    assert "description_category_id" not in cat
    assert "type_id" not in cat


def test_discover_page_truth_shape_no_dc():
    """_discover_page_truth 直测：面包屑输出路径+语言+web_category_id（排查线索），
    不含 description_category_id/type_id；属性/物理真值/标题注入不变。"""
    with mock.patch.object(cloud_probe, "_cached_ozon_scrape",
                           return_value=dict(PAGE_SCRAPE)):
        truth = cloud_probe._discover_page_truth(OZON_URL)
    cat = truth["ozon_category"]
    assert cat["web_category_id"] == "17028929"
    assert cat["source"] == "page"
    assert cat["namespace"] == "widget"
    assert cat["category_path"] == "Зоотовары > Кошки > Автопоилки"
    assert cat["breadcrumb_language"] == "RU"
    assert "description_category_id" not in cat
    assert "type_id" not in cat
    # 属性/重量尺寸/标题注入保持不变
    assert truth["ozon_attributes"]["Цвет"] == "Белый"
    assert truth["weight_g"] == 270
    assert truth["dimensions_mm"] == {"length": 190, "width": 64, "height": 230}
    assert truth["ozon_title"] == "Автопоилка для кошек 2л"


def test_page_failure_keeps_what_to_sell():
    """page 抓取未成功 + what_to_sell 有值 → 保留 what_to_sell。"""
    cand = _mk_candidate(ozon_category=dict(WTS_CATEGORY))
    res = _build(cand, scrape_result={"success": False, "error": "403"})
    assert res is not None
    cat = res["envelope"]["draft"]["ozon_category"]
    assert cat["description_category_id"] == "99999999"
    assert cat["type_id"] == "88888888"
    assert cat["source"] == "what_to_sell"


def test_no_sources_matches_current_behavior():
    """page/what_to_sell 都无 → 除 ozon_url/ozon_title 外与现状逐字段一致（回归）。"""
    cand = _mk_candidate(ozon_category={})
    res = _build(cand, scrape_result={"success": False, "error": "no chrome"})
    assert res is not None
    draft = res["envelope"]["draft"]
    ext = res["envelope"]["extensions"]
    # 无真值不造空壳
    assert "ozon_category" not in draft
    assert "ozon_attributes" not in draft
    assert "ozon_attributes_category" not in draft
    assert "competitor_weight_g" not in ext
    assert "competitor_dimensions_mm" not in ext
    # T1.2: Ozon 上下文永不丢弃
    assert draft["ozon_url"] == OZON_URL
    assert draft["ozon_title"] == "Автопоилка для кошек 2л"
    # 现有字段逐字保持
    assert draft["item_id"] == "980815374096"
    assert draft["ozon_product_id"] == "4767514314"
    assert ext["follow_sell"] is True
    assert res["token"] == "sk-test"
    assert res["ozon_client_id"] == "123"
    assert res["envelope"]["source"] == {"purchase_url": "u", "purchase_cost": 1.0}


def test_page_attrs_and_context_injected():
    """页面特征属性 → draft.ozon_attributes + ozon_attributes_category；ozon_url/title 到位。"""
    cand = _mk_candidate()
    res = _build(cand, scrape_result=PAGE_SCRAPE)
    draft = res["envelope"]["draft"]
    assert draft["ozon_attributes"]["Цвет"] == "Белый"
    assert draft["ozon_attributes"]["Вес товара, г"] == "270 г"
    assert draft["ozon_attributes"]["Габариты упаковки"] == "190*64*230 мм"
    assert draft["ozon_attributes_category"] == 17028929
    assert draft["ozon_url"] == OZON_URL
    assert draft["ozon_title"] == "Автопоилка для кошек 2л"


def test_degraded_envelope_carries_page_truth():
    """build_graph_envelope_with_retry 抛异常 → 降级信封同样带 ozon_url 与页面真值。"""
    cand = _mk_candidate(weight_g=0, dimensions_mm={})
    res = _build(cand, scrape_result=PAGE_SCRAPE, retry_error=True)
    assert res is not None
    draft = res["envelope"]["draft"]
    ext = res["envelope"]["extensions"]
    assert draft["ozon_url"] == OZON_URL
    assert draft["ozon_title"] == "Автопоилка для кошек 2л"
    assert draft["ozon_category"]["source"] == "page"
    assert "description_category_id" not in draft["ozon_category"]
    assert draft["ozon_attributes"]["Цвет"] == "Белый"
    assert draft["ozon_attributes_category"] == 17028929
    # 页面物理真值补 extensions（候选无 what_to_sell 数据）
    assert ext["competitor_weight_g"] == 270
    assert ext["competitor_dimensions_mm"] == {"length": 190, "width": 64, "height": 230}


def test_no_chrome_scrape_raises_no_crash():
    """无 Chrome（scrape 抛异常）→ 不崩，走兜底（行为与今天一致）。"""
    cand = _mk_candidate(ozon_category={})
    res = _build(cand, scrape_error=ConnectionRefusedError("connection refused"))
    assert res is not None
    draft = res["envelope"]["draft"]
    assert "ozon_category" not in draft
    assert "ozon_attributes" not in draft
    assert draft["ozon_url"] == OZON_URL


def test_what_to_sell_weight_wins_page_fills_gap():
    """what_to_sell 重量/尺寸在场 → 页面值不覆盖；候选缺 → 页面值补。"""
    cand = _mk_candidate(weight_g=227, dimensions_mm={"length": 120, "width": 80, "height": 60})
    res = _build(cand, scrape_result=PAGE_SCRAPE)
    ext = res["envelope"]["extensions"]
    assert ext["competitor_weight_g"] == 227
    assert ext["competitor_dimensions_mm"] == {"length": 120, "width": 80, "height": 60}


def test_no_ozon_url_skips_scrape():
    """候选无 ozon_url → 不发起页面抓取（零开销）。"""
    cand = _mk_candidate(ozon_url="")
    with mock.patch.object(cloud_probe, "_cached_ozon_scrape") as m, \
         mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=_mock_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        res = cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})
    assert res is not None
    m.assert_not_called()
    assert "ozon_url" not in res["envelope"]["draft"]


# ── P-D: discover 变体标记 extensions.follow_type ─────────────────────────

def test_follow_type_discover_injected_when_follow_sell():
    """follow_sell=True → extensions.follow_type=discover（discover 变体标记）。"""
    cand = _mk_candidate()  # competing_sellers=4 → follow_sell=True
    res = _build(cand, scrape_result={"success": False, "error": "no chrome"})
    ext = res["envelope"]["extensions"]
    assert ext["follow_sell"] is True
    assert ext["follow_type"] == "discover"


def test_follow_type_absent_without_follow_sell():
    """follow_sell=False → 不注入 follow_type（真实跟卖才由 follow 管线打标）。"""
    cand = _mk_candidate(competing_sellers=0)
    res = _build(cand, scrape_result={"success": False, "error": "no chrome"})
    ext = res["envelope"]["extensions"]
    assert not ext.get("follow_sell")
    assert "follow_type" not in ext


def test_follow_type_existing_value_not_overwritten():
    """extensions 已有 follow_type（上游/模板注入）→ 不覆盖。"""
    cand = _mk_candidate()
    env = _mock_envelope()
    env["envelope"]["extensions"]["follow_type"] = "hand"
    res = _build(cand, retry_envelope=env,
                 scrape_result={"success": False, "error": "no chrome"})
    assert res["envelope"]["extensions"]["follow_type"] == "hand"


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
