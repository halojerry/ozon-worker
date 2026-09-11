#!/usr/bin/env python3
"""跨平台货源 v1 批2 — cli/batch/platform 路由接线测试（全 mock，绝不启 Chrome）。

覆盖:
- batch_test.parse_urls_file：taobao/tmall/pdd 识别入列 + 1688 旧口径逐字节
  回归（含 m 站 offerId）+ 未知平台忽略 + 跨平台去重
- --type-filter choices / GRAPH_URL_TYPES 常量口径
- batch 分派：taobao/tmall/pdd 走 graph 直传链（process_1688_url），不落
  process_ozon_url 跟卖链
- cli cmd_graph：taobao URL → parse_platform_url 提取 item_id 后进信封组装
- browser_probe 硬门：taobao/tmall/pdd 放行 → 各自适配器分派（pdd 为批3）；
  未知域名拒绝；1688 不进适配器（原链回归）

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_platform_routing_v1.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import batch_test  # noqa: E402
from scripts.capabilities.browser_probe import service as _svc  # noqa: E402
from scripts.capabilities.browser_probe.service import (  # noqa: E402
    ConfigError,
    ValidationError,
    probe_1688_page,
    probe_1688_page_safe,
)

_TB = "https://item.taobao.com/item.htm?id=679836775118&spm=x"
_TM = "https://detail.tmall.com/item.htm?id=654654136372"
_PDD = "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654"


def _write_urls(lines: list[str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="urls_", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ── parse_urls_file：新平台识别 + 1688 旧口径回归 ──


class TestParseUrlsFilePlatforms:
    def test_taobao_tmall_pdd_types(self):
        path = _write_urls([_TB, _TM, _PDD])
        try:
            items = batch_test.parse_urls_file(path)
        finally:
            os.unlink(path)
        by_type = {i["type"]: i for i in items}
        assert set(by_type) == {"taobao", "tmall", "pdd"}
        assert by_type["taobao"]["id"] == "679836775118"
        assert by_type["taobao"]["url"] == _TB
        assert by_type["tmall"]["id"] == "654654136372"
        assert by_type["pdd"]["id"] == "734654654654"

    def test_1688_legacy_forms_unchanged(self):
        """1688 旧口径（offer/ + m 站 offerId=）逐字节回归。"""
        path = _write_urls([
            "https://detail.1688.com/offer/980815374096.html",
            "https://detail.m.1688.com/page/index.html?offerId=1234567890",
        ])
        try:
            items = batch_test.parse_urls_file(path)
        finally:
            os.unlink(path)
        assert [(i["type"], i["id"]) for i in items] == [
            ("1688", "980815374096"), ("1688", "1234567890")]

    def test_unknown_platform_ignored_and_dedup(self):
        path = _write_urls([
            _TB,
            "https://item.taobao.com/item.htm?id=679836775118&spm=other",
            "https://www.amazon.com/dp/B0CX1234",
            "https://www.ozon.ru/product/123456789",
            "not a url",
        ])
        try:
            items = batch_test.parse_urls_file(path)
        finally:
            os.unlink(path)
        taobao_rows = [i for i in items if i["type"] == "taobao"]
        assert len(taobao_rows) == 1, "同 item_id 跨 URL 形态应去重"
        assert all(i["type"] != "amazon" for i in items)
        assert any(i["type"] == "ozon" for i in items)


class TestTypeFilterChoices:
    def test_choices_include_new_platforms(self):
        assert set(batch_test.TYPE_FILTER_CHOICES) == {
            "1688", "ozon", "taobao", "tmall", "pdd", "all"}

    def test_argparse_accepts_new_platforms(self):
        """--type-filter 行为验证：真 parse_args（build_arg_parser 直调，
        对齐 cli.build_arg_parser 先例）——非源码 grep。"""
        parser = batch_test.build_arg_parser()
        for choice in ("taobao", "tmall", "pdd"):
            args = parser.parse_args(
                ["--urls-file", "x.txt", "--type-filter", choice])
            assert args.type_filter == choice, choice

    def test_argparse_rejects_unknown_type(self):
        """未知类型被 argparse choices 真拒（SystemExit=2），不是静默放行。"""
        parser = batch_test.build_arg_parser()
        with pytest.raises(SystemExit) as ei:
            parser.parse_args(
                ["--urls-file", "x.txt", "--type-filter", "amazon"])
        assert ei.value.code == 2

    def test_graph_url_types_semantics(self):
        assert batch_test.GRAPH_URL_TYPES == ("1688", "taobao", "tmall", "pdd")


# ── batch 分派：新平台走 graph 直传链（真实 main() 路径）──


class TestBatchDispatch:
    """批2 fix round 1: 不再复刻分派循环——直接跑生产 batch_test.main()，
    让 ``if url_type in GRAPH_URL_TYPES:`` 这一行被非 1688 URL 真实执行并锁定
    （分派行回归成 == "1688" 时本组测试必红：淘宝/天猫/拼多多会落到 ozon mock）。"""

    def _run_main_routing(self, urls_lines: list[str]):
        """跑真实 main()，mock 两条链入口，返回 (graph_calls, ozon_calls)。"""
        out_dir = Path(tempfile.mkdtemp(prefix="batch_routing_"))
        urls_file = out_dir / "urls.txt"
        urls_file.write_text("\n".join(urls_lines) + "\n", encoding="utf-8")
        graph_calls: list[tuple[str, str]] = []
        ozon_calls: list[str] = []

        def fake_process_1688(url, offer_id, client_id, api_key, worker_url,
                              dry_run, store_id="", source_type="1688"):
            graph_calls.append((source_type, offer_id))
            return {"type": source_type, "offer_id": offer_id, "success": True}

        def fake_process_ozon(url, product_id, **_kw):
            ozon_calls.append(product_id)
            return {"type": "ozon", "product_id": product_id, "success": True}

        with mock.patch.object(batch_test, "OUTPUT_DIR", out_dir), \
                mock.patch.object(sys, "argv", [
                    "batch_test.py", "--urls-file", str(urls_file), "--dry-run"]), \
                mock.patch.object(batch_test.time, "sleep"), \
                mock.patch("requests.get", side_effect=ConnectionError("no cdp")), \
                mock.patch("scripts.lib.config_store.check_config",
                           return_value={"missing": [],
                                         "cdp": {"browser_available": True}}), \
                mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                           return_value=(True, "ok")), \
                mock.patch.object(batch_test, "process_1688_url",
                                  side_effect=fake_process_1688), \
                mock.patch.object(batch_test, "process_ozon_url",
                                  side_effect=fake_process_ozon):
            rc = batch_test.main()
        return rc, graph_calls, ozon_calls

    def test_taobao_routes_to_graph_chain_not_ozon(self):
        """taobao/tmall/pdd → 生产分派行走 graph 直传链，ozon URL → 跟卖链。"""
        rc, graph_calls, ozon_calls = self._run_main_routing([
            "https://item.taobao.com/item.htm?id=679836775118",
            "https://detail.tmall.com/item.htm?id=654654136372",
            "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654",
            "https://www.ozon.ru/product/123456789",
        ])
        assert rc == 0
        assert sorted(graph_calls) == sorted([
            ("taobao", "679836775118"),
            ("tmall", "654654136372"),
            ("pdd", "734654654654"),
        ]), f"三个新平台都必须经生产分派行进 graph 链，实际 {graph_calls}"
        assert ozon_calls == ["123456789"], ozon_calls

    def test_process_1688_url_source_type_labeling(self):
        """source_type 只影响结果行 type 标注（默认 1688 逐字节兼容）。"""
        with mock.patch("scripts.cloud_probe.build_graph_envelope_with_retry",
                        side_effect=RuntimeError("boom")):
            r = batch_test.process_1688_url(
                url=_TB, offer_id="679836775118", client_id="", api_key="",
                worker_url="", dry_run=True, source_type="taobao")
        assert r["type"] == "taobao"
        r2 = batch_test.process_1688_url(
            url="https://detail.1688.com/offer/1.html", offer_id="1",
            client_id="", api_key="", worker_url="", dry_run=True)
        assert r2["type"] == "1688"


# ── cli cmd_graph：taobao URL → 平台 item_id 提取 ──


class TestCmdGraphRouting:
    def _args(self, *extra):
        from scripts import cli
        return cli.build_arg_parser().parse_args(
            ["graph", "--url", _TB, "--no-submit", *extra])

    def test_taobao_url_extracts_item_id_into_envelope_builder(self):
        from scripts import cli
        captured: dict[str, str] = {}

        def _fake_graph(**kw):
            captured.update({k: str(v) for k, v in kw.items()
                             if k in ("item_id", "detail_url")})
            return {"token": "t", "ozon_client_id": "c", "ozon_api_key": "k",
                    "envelope": {"draft": {"item_id": kw["item_id"], "title": "x",
                                           "purchase_cost": 1, "weight": 100,
                                           "dimensions": {"length": 0, "width": 0,
                                                          "height": 0},
                                           "images": ["https://img.alicdn.com/a.jpg"],
                                           "attributes": {"颜色": "红"}},
                                 "source": {}, "extensions": {}}}

        payloads: list[dict] = []
        with mock.patch("scripts.lib.config_store.preflight_check",
                        return_value=[]), \
                mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                           return_value=(True, "ok")), \
                mock.patch("scripts.cloud_probe.build_graph_envelope_with_retry",
                           _fake_graph), \
                mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                           return_value=None), \
                mock.patch.object(cli, "_out",
                                  lambda payload: payloads.append(payload)):
            rc = cli.cmd_graph(self._args())
        assert rc == 0
        assert captured.get("item_id") == "679836775118", captured
        assert captured.get("detail_url") == _TB

    def test_1688_url_regression_same_extraction(self):
        """1688 URL 走同一入口：item_id/canonical 口径回归。"""
        from scripts import cli
        captured: dict[str, str] = {}

        def _fake_graph(**kw):
            captured.update({k: str(v) for k, v in kw.items()
                             if k in ("item_id", "detail_url")})
            return {"envelope": {"draft": {}}}

        payloads: list[dict] = []
        with mock.patch("scripts.lib.config_store.preflight_check",
                        return_value=[]), \
                mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                           return_value=(True, "ok")), \
                mock.patch("scripts.cloud_probe.build_graph_envelope_with_retry",
                           _fake_graph), \
                mock.patch.object(cli, "_out",
                                  lambda payload: payloads.append(payload)):
            args = cli.build_arg_parser().parse_args(
                ["graph", "--url", "https://detail.1688.com/offer/980815374096.html",
                 "--no-submit"])
            rc = cli.cmd_graph(args)
        assert rc == 0
        assert captured.get("item_id") == "980815374096"
        assert captured.get("detail_url") == \
            "https://detail.1688.com/offer/980815374096.html"


# ── browser_probe 硬门：平台白名单分派 ──


class _CdpFake:
    cdp_url = "http://127.0.0.1:9222"

    def find_tab(self, pattern):
        raise ConfigError("SENTINEL_1688_FLOW")


class TestProbeHardGate:
    def test_taobao_dispatches_to_adapter(self):
        product = {
            "platform": "taobao", "item_id": "679836775118",
            "canonical_url": "https://item.taobao.com/item.htm?id=679836775118",
            "title": "马克杯", "price": "12.90", "price_ranges": [12.9, 12.9],
            "images": ["https://img.alicdn.com/imgextra/main.jpg"],
            "description": "", "attributes": [{"name": "材质", "value": "陶瓷"}],
            "option_groups": [], "sku_details": [], "shipping": {"freightCny": 0.0},
            "weight_grams": None, "seller": "店", "brand": "",
        }
        with mock.patch("scripts.lib.taobao_client.fetch_product",
                        return_value=product) as fp:
            result = probe_1688_page(_TB, cdp=_CdpFake())
        fp.assert_called_once()
        assert result["ready"] is True
        assert result["probe"]["site"] == "taobao"
        assert result["probe"]["title"] == "马克杯"
        assert result["source_product"]["item_id"] == "679836775118"

    def test_tmall_dispatches_to_adapter(self):
        with mock.patch("scripts.lib.taobao_client.fetch_product",
                        return_value={"platform": "tmall", "title": "耳机",
                                      "price": "199.00", "images": [],
                                      "attributes": [], "option_groups": [],
                                      "sku_details": [], "shipping": {}}) as fp:
            result = probe_1688_page(_TM, cdp=_CdpFake())
        fp.assert_called_once()
        assert result["probe"]["site"] == "tmall"

    def test_pdd_dispatches_to_adapter(self):
        """批3: pdd URL 进 pdd_client 适配器（不再拒绝）。"""
        product = {
            "platform": "pdd", "item_id": "734654654654",
            "canonical_url": "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654",
            "title": "陶瓷马克杯", "price": "12.90", "price_ranges": [12.9, 15.8],
            "images": ["https://img.pddpic.com/main.jpeg"],
            "description": "",
            "attributes": [{"name": "材质", "value": "陶瓷"}],
            "option_groups": [], "sku_details": [], "shipping": {"freightCny": 0.0},
            "weight_grams": None, "seller": "某某百货", "brand": "某杂货",
        }
        with mock.patch("scripts.lib.pdd_client.fetch_product",
                        return_value=product) as fp:
            result = probe_1688_page(_PDD, cdp=_CdpFake())
        fp.assert_called_once()
        assert result["ready"] is True
        assert result["probe"]["site"] == "pdd"
        assert result["probe"]["title"] == "陶瓷马克杯"
        assert result["source_product"]["item_id"] == "734654654654"

    def test_pdd_rejection_error_message_updated(self):
        """未知域名仍拒绝，且不再出现「拼多多待后续版本/批3」旧口径。"""
        with mock.patch("scripts.lib.pdd_client.fetch_product") as fp:
            with pytest.raises(ValidationError) as ei:
                probe_1688_page("https://www.amazon.com/dp/B0CX", cdp=_CdpFake())
        fp.assert_not_called()
        msg = str(ei.value)
        assert "后续版本" not in msg and "批3" not in msg and "暂不支持拼多多" not in msg

    def test_safe_wrapper_shapes_pdd_data(self):
        """probe_1688_page_safe 零改动消费 pdd 分派结果（批3 扩展）。"""
        product = {
            "platform": "pdd", "title": "陶瓷马克杯", "price": "12.90",
            "images": ["https://img.pddpic.com/main.jpeg"],
            "attributes": [{"name": "材质", "value": "陶瓷"}],
            "option_groups": [], "sku_details": [],
            "shipping": {"freightCny": 0.0}, "seller": "某某百货", "brand": "",
            "weight_grams": None,
        }
        with mock.patch("scripts.lib.pdd_client.fetch_product",
                        return_value=product):
            result = probe_1688_page_safe(_PDD, cdp=_CdpFake())
        assert result["ok"] is True
        assert result["error"] is None
        assert result["data"]["title"] == "陶瓷马克杯"
        assert result["data"]["attributes"] == [{"name": "材质", "value": "陶瓷"}]
        assert result["data"]["shipping"] == {"freightCny": 0.0}

    def test_unknown_domain_rejected(self):
        for url in ("https://www.amazon.com/dp/B0CX", "https://www.jd.com/1.html"):
            with pytest.raises(ValidationError):
                probe_1688_page(url, cdp=_CdpFake())

    def test_1688_keeps_original_flow(self):
        """1688 URL 不得进适配器（原 EXTRACT_1688_JS 链回归——外部连接下进入
        1688 专属 find_tab(offer_id) 匹配才被哨兵拦下）。"""
        with mock.patch("scripts.lib.taobao_client.fetch_product") as fp, \
                mock.patch("scripts.lib.pdd_client.fetch_product") as fp_pdd, \
                mock.patch("scripts.lib.cache.cache_get", return_value=None), \
                mock.patch("scripts.lib.cache.cache_set"), \
                mock.patch.object(_svc, "_find_cached_probe", return_value=None), \
                mock.patch("time.sleep"):
            with pytest.raises(ConfigError) as ei:
                probe_1688_page(
                    "https://detail.1688.com/offer/980815374096.html",
                    cdp=_CdpFake())
        fp.assert_not_called()
        fp_pdd.assert_not_called()
        assert "SENTINEL_1688_FLOW" in str(ei.value), \
            "应穿过硬门进入 1688 原链（在 1688 专属 find_tab 才被哨兵拦下）"

    def test_safe_wrapper_shapes_taobao_data(self):
        """probe_1688_page_safe 零改动消费 taobao 分派结果。"""
        product = {
            "platform": "taobao", "title": "马克杯", "price": "12.90",
            "images": ["https://img.alicdn.com/imgextra/main.jpg"],
            "attributes": [{"name": "材质", "value": "陶瓷"}],
            "option_groups": [], "sku_details": [],
            "shipping": {"freightCny": 0.0}, "seller": "店", "brand": "",
            "weight_grams": None,
        }
        with mock.patch("scripts.lib.taobao_client.fetch_product",
                        return_value=product):
            result = probe_1688_page_safe(_TB, cdp=_CdpFake())
        assert result["ok"] is True
        assert result["error"] is None
        assert result["data"]["title"] == "马克杯"
        assert result["data"]["attributes"] == [{"name": "材质", "value": "陶瓷"}]
        assert result["data"]["shipping"] == {"freightCny": 0.0}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
