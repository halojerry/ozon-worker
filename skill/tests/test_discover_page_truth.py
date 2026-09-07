#!/usr/bin/env python3
"""discover 提交前页面真值富化（page > what_to_sell > search_kw）回归。

背景：discover 管线（Ozon 现成商品）信封此前缺 Ozon 地面真值（类目 dc/tp、
特征属性）——build_envelope_from_discovery 组装时不读 candidate.ozon_url，
类目只靠 1688 中文词文本猜（source=search_kw），what_to_sell 类目又经常
空 {}。本文件锁定：

1. page 真值存在 → draft.ozon_category.source=="page"，压过 what_to_sell；
   page 与 what_to_sell dc 分歧时告警并取 page
2. page 失败 + what_to_sell 有值 → 保留 what_to_sell
3. 两者都无 → 除 ozon_url/ozon_title（规定永不丢弃）外与现状逐字段一致
4. draft.ozon_attributes / ozon_attributes_category / ozon_url / ozon_title 到位；
   build_graph_envelope_with_retry 抛异常的降级信封同样带页面真值
5. 无 Chrome（scrape 抛异常）→ 不崩、走兜底

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


def test_page_truth_overrides_what_to_sell():
    """page 真值存在 → source=page 压过 what_to_sell。"""
    cand = _mk_candidate(ozon_category=dict(WTS_CATEGORY))
    res = _build(cand, scrape_result=PAGE_SCRAPE)
    assert res is not None
    cat = res["envelope"]["draft"]["ozon_category"]
    assert cat["source"] == "page"
    assert cat["namespace"] == "widget"
    assert cat["description_category_id"] == "17028929"
    assert cat["type_id"] == "504866264"
    assert cat["category_path"] == "Зоотовары > Кошки > Автопоилки"


def test_dc_mismatch_warns_and_takes_page():
    """page 与 what_to_sell dc 不一致 → 告警记录两源，取 page。"""
    cand = _mk_candidate(ozon_category=dict(WTS_CATEGORY))

    def _scrape(url, **kwargs):
        return dict(PAGE_SCRAPE)

    with mock.patch.object(cloud_probe, "_cached_ozon_scrape", side_effect=_scrape), \
         mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=_mock_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"), \
         mock.patch.object(cloud_probe.logger, "warning") as warn:
        res = cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})
    assert res["envelope"]["draft"]["ozon_category"]["description_category_id"] == "17028929"
    warn_text = str(warn.call_args_list)
    assert "99999999" in warn_text and "17028929" in warn_text


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


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{9 - failed}/9 passed")
    sys.exit(1 if failed else 0)
