#!/usr/bin/env python3
"""跨平台货源 v1 批2 — parse_platform_url 全矩阵 TDD。

覆盖:
- 四平台识别（1688 / taobao / tmall / pdd）+ canonical_url 规范化
- 1688 三种模式与 ak_1688_client.parse_product_url 逐字节兼容（同 item_id、
  同 canonical 口径：offer 路径/纯数字规范化到 detail.1688.com，query 参数
  形态 canonical 保留原值）
- 负例（非货源 URL / 缺 id / 空串）→ None

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_source_platforms_parse.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.source_platforms import (  # noqa: E402
    PlatformTarget,
    parse_platform_url,
    probe_platform_supported,
)

# ── 1688（三模式并入，byte-compat 权威 = ak_1688_client.parse_product_url）──


class TestParse1688:
    def test_offer_path(self):
        t = parse_platform_url("https://detail.1688.com/offer/980815374096.html")
        assert t is not None and t.platform == "1688"
        assert t.item_id == "980815374096"
        assert t.canonical_url == "https://detail.1688.com/offer/980815374096.html"

    def test_pure_numeric_id(self):
        t = parse_platform_url("980815374096")
        assert t is not None and t.platform == "1688"
        assert t.item_id == "980815374096"
        assert t.canonical_url == "https://detail.1688.com/offer/980815374096.html"

    def test_query_id_param_keeps_original_url_as_canonical(self):
        """query 形态 canonical=原值（与 ak_1688_client.parse_product_url 一致）。"""
        raw = "https://www.1688.com/xxx.html?id=123456789&foo=bar"
        t = parse_platform_url(raw)
        assert t is not None and t.platform == "1688"
        assert t.item_id == "123456789"
        assert t.canonical_url == raw, "1688 query 形态 canonical 必须保留原值"

    def test_m_station_offerid(self):
        t = parse_platform_url(
            "https://detail.m.1688.com/page/index.html?offerId=1234567890")
        assert t is not None and t.platform == "1688"
        assert t.item_id == "1234567890"

    def test_offer_id_param(self):
        t = parse_platform_url("https://m.1688.com/page/index.html?offer_id=998877")
        assert t is not None and t.item_id == "998877"

    def test_uppercase_domain(self):
        t = parse_platform_url("https://detail.1688.COM/offer/980815374096.html")
        assert t is not None and t.platform == "1688"
        assert t.item_id == "980815374096"

    def test_byte_compat_with_ak_1688_client(self):
        """1688 结果与 ak_1688_client.parse_product_url 逐字段一致（权威回归）。"""
        from scripts.lib.ak_1688_client import parse_product_url
        samples = [
            "https://detail.1688.com/offer/980815374096.html",
            "https://detail.1688.com/offer/679001234567.html?spm=a26352.13672862.pushfrien.1",
            "980815374096",
            "1234567890",
            "https://detail.m.1688.com/page/index.html?offerId=1234567890",
            "https://www.1688.com/xxx.html?id=123456789&foo=bar",
            "https://m.1688.com/page/index.html?offer_id=998877",
            "https://sale.1688.com/factory/hot.html",   # 1688 域但无商品 id → None
            "https://www.ozon.ru/product/1234567890",
            "",
            "not-a-url",
        ]
        for url in samples:
            expected = parse_product_url(url)
            got = parse_platform_url(url)
            if expected is None:
                # 1688 域无商品 id → parse_platform_url 也必须是 None（不得跨平台误判）
                assert got is None, f"{url!r}: 期望 None，实际 {got}"
            else:
                assert got is not None, f"{url!r}: ak 解析成功但 parse_platform_url 返回 None"
                assert got.platform == expected["platform"], url
                assert got.item_id == expected["product_id"], url
                assert got.canonical_url == expected["canonical_url"], (
                    f"{url!r} canonical 不一致: {got.canonical_url!r} != "
                    f"{expected['canonical_url']!r}")


# ── taobao ──


class TestParseTaobao:
    def test_item_taobao_id_query(self):
        raw = ("https://item.taobao.com/item.htm?spm=a1z10.1-b-s.w5003-22045675818.1"
               "&id=679836775118&scene=taobao_shop")
        t = parse_platform_url(raw)
        assert t is not None and t.platform == "taobao"
        assert t.item_id == "679836775118"
        assert t.canonical_url == "https://item.taobao.com/item.htm?id=679836775118"

    def test_world_taobao_item_path(self):
        t = parse_platform_url("https://world.taobao.com/item/679836775118.htm")
        assert t is not None and t.platform == "taobao"
        assert t.item_id == "679836775118"
        assert t.canonical_url == "https://item.taobao.com/item.htm?id=679836775118"

    def test_mobile_detail_form(self):
        t = parse_platform_url("https://main.m.taobao.com/detail/index.html?id=723456789012")
        assert t is not None and t.platform == "taobao"
        assert t.item_id == "723456789012"

    def test_taobao_without_id_is_none(self):
        assert parse_platform_url("https://item.taobao.com/item.htm?spm=x") is None

    def test_taobao_non_numeric_id_is_none(self):
        assert parse_platform_url("https://item.taobao.com/item.htm?id=abc") is None


# ── tmall ──


class TestParseTmall:
    def test_detail_tmall_id_query(self):
        raw = "https://detail.tmall.com/item.htm?id=654654136372&skuId=5123456789012"
        t = parse_platform_url(raw)
        assert t is not None and t.platform == "tmall"
        assert t.item_id == "654654136372"
        assert t.canonical_url == "https://detail.tmall.com/item.htm?id=654654136372"

    def test_tmall_subdomain(self):
        t = parse_platform_url("https://chaoshi.detail.tmall.com/item.htm?id=523456789012")
        assert t is not None and t.platform == "tmall"
        assert t.item_id == "523456789012"

    def test_tmall_without_id_is_none(self):
        assert parse_platform_url("https://detail.tmall.com/item.htm") is None


# ── pdd ──


class TestParsePdd:
    def test_yangkeduo_goods_html(self):
        raw = "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654"
        t = parse_platform_url(raw)
        assert t is not None and t.platform == "pdd"
        assert t.item_id == "734654654654"
        assert t.canonical_url == "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654"

    def test_goods1_goods2_forms(self):
        for form in ("goods1", "goods2"):
            t = parse_platform_url(
                f"https://mobile.yangkeduo.com/{form}.html?goods_id=6000000001")
            assert t is not None and t.platform == "pdd", form
            assert t.item_id == "6000000001", form

    def test_pinduoduo_domain(self):
        t = parse_platform_url("https://mobile.pinduoduo.com/goods.html?goods_id=6000000002")
        assert t is not None and t.platform == "pdd"
        assert t.item_id == "6000000002"

    def test_yangkeduo_without_goods_id_is_none(self):
        assert parse_platform_url("https://mobile.yangkeduo.com/goods.html?foo=1") is None


# ── 负例 / 白名单辅助 ──


class TestNegativesAndWhitelist:
    def test_empty_and_garbage(self):
        assert parse_platform_url("") is None
        assert parse_platform_url(None) is None
        assert parse_platform_url("   ") is None
        assert parse_platform_url("not-a-url") is None

    def test_non_source_urls(self):
        for url in (
            "https://www.ozon.ru/product/1234567890",
            "https://www.amazon.com/dp/B0CX1234",
            "https://www.jd.com/item/12345.html",
            "https://github.com/livekit/agents",
        ):
            assert parse_platform_url(url) is None, url

    def test_probe_whitelist_pdd_rejected_until_batch3(self):
        """pdd 解析可识别但抓取白名单批2 拒绝（批3 提供适配器）。"""
        t = parse_platform_url("https://mobile.yangkeduo.com/goods.html?goods_id=1")
        assert t is not None
        assert probe_platform_supported(t.platform) is False
        assert probe_platform_supported("1688") is True
        assert probe_platform_supported("taobao") is True
        assert probe_platform_supported("tmall") is True

    def test_target_is_immutable_dataclass(self):
        t = parse_platform_url("https://detail.1688.com/offer/123456.html")
        assert isinstance(t, PlatformTarget)
        try:
            t.item_id = "x"  # type: ignore[misc]
            raise AssertionError("PlatformTarget 应为 immutable")
        except AttributeError:
            pass


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
