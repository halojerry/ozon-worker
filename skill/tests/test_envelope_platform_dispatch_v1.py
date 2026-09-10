#!/usr/bin/env python3
"""跨平台货源 v1 批4 — 信封组装接线 TDD（全 mock，绝不启 Chrome）。

覆盖（PLAN §4 批4）:
- 分派矩阵：taobao/tmall/pdd URL → 各自适配器直调恰一次（不经
  enrich_product_with_cdp/probe_1688_page_safe 包装）；1688 URL → 适配器
  assert_not_called（原链 AK→CDP→validate→draft 逐字节回归）
- 信封 cid 键纪律（最高优先断言）：新平台信封**省略**所有 1688-cid 数字键
  （draft.source_category_id / source.category_id），保留中文类目词键
  （source_category_path / draft.source_category，适配器有则透传）；
  draft.ozon_category 缺省走 worker 文本+LLM 链；manual dc/tp 直传不受影响
- source.platform 填入（批1 契约 §1.1.2：worker 零强制消费）
- 变体折叠复用：平台 SKU 列表 → _collapse_variants_to_single（中位价）
- purchase_cost = 代表变体价 + freightCny（运费缺席=仅价；包邮 0=+0）
- 适配器人话错误原样透传（不被降级成「数据不完整」/「CDP failed after」）
- weight/dims 缺失 → 现有软兜底路径（50g / 2:1.5:1 估算）零重复造

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_envelope_platform_dispatch_v1.py -q
"""
from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scripts.cloud_probe as cloud_probe  # noqa: E402
from scripts.lib.pdd_client import PddFetchError  # noqa: E402
from scripts.lib.taobao_client import TaobaoFetchError  # noqa: E402

_TB = "https://item.taobao.com/item.htm?id=679836775118&spm=x"
_TB_CANONICAL = "https://item.taobao.com/item.htm?id=679836775118"
_TM = "https://detail.tmall.com/item.htm?id=654654136372"
_TM_CANONICAL = "https://detail.tmall.com/item.htm?id=654654136372"
_PDD = "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654"
_PDD_CANONICAL = "https://mobile.yangkeduo.com/goods.html?goods_id=734654654654"
_1688 = "https://detail.1688.com/offer/980815374096.html"


def _product_info(platform: str, **over):
    """适配器归一后的统一 ProductInfo 夹具（批2/批3 契约同形）。"""
    item_id = {"taobao": "679836775118", "tmall": "654654136372",
               "pdd": "734654654654"}[platform]
    canonical = {"taobao": _TB_CANONICAL, "tmall": _TM_CANONICAL,
                 "pdd": _PDD_CANONICAL}[platform]
    img = {
        "taobao": "https://img.alicdn.com/imgextra/i4/O1CN01main/!!6000000000000-0-tp.jpg",
        "tmall": "https://img.alicdn.com/imgextra/i1/O1CN01tmall/!!6100000000000-0-tp.jpg",
        "pdd": "https://img.pddpic.com/mms-material-msg/2023-01-01/test.jpeg",
    }[platform]
    base = {
        "platform": platform,
        "item_id": item_id,
        "canonical_url": canonical,
        "title": "创意简约陶瓷马克杯办公水杯定制",
        "price": "12.90",
        "price_ranges": [12.90, 25.00],
        "images": [img],
        "description": "",
        "attributes": [{"name": "材质", "value": "陶瓷"}],
        "option_groups": [
            {"name": "颜色", "values": [{"name": "白色"}, {"name": "黑色"}]},
        ],
        "sku_details": [
            {"sku_id": "s1", "name": "白色 300ml", "price": 12.90, "image": img},
            {"sku_id": "s2", "name": "黑色 300ml", "price": 15.80, "image": ""},
            {"sku_id": "s3", "name": "黑色 500ml", "price": 25.00, "image": ""},
        ],
        "shipping": {},
        "weight_grams": None,
        "seller": "某某家居专营店",
        "brand": "",
    }
    base.update(over)
    return base


def _patch_env(stack: ExitStack, adapter_mod: str, product: dict):
    """公共 mock 环境：鉴权/凭证/模板配置全 mock（零网络零 Chrome）。"""
    fetch = stack.enter_context(
        mock.patch(f"{adapter_mod}.fetch_product", return_value=dict(product)))
    stack.enter_context(
        mock.patch("scripts.lib.config_store._require_auth"))
    stack.enter_context(mock.patch.object(
        cloud_probe, "_get_ozon_credentials",
        return_value={"client_id": "cid-123", "api_key": "akey-456"}))
    stack.enter_context(mock.patch.object(cloud_probe, "_get_mxou_token",
                                          return_value="mxou-token"))
    stack.enter_context(mock.patch.object(cloud_probe, "_get_token",
                                          return_value="local-token"))
    stack.enter_context(
        mock.patch("scripts.lib.config_store.get_store_profile",
                   return_value={}))
    stack.enter_context(
        mock.patch("scripts.lib.config_store.get_template_profile",
                   return_value={}))
    return fetch


def _adapter_mod(platform: str) -> str:
    return ("scripts.lib.taobao_client" if platform in ("taobao", "tmall")
            else "scripts.lib.pdd_client")


# ── 分派矩阵 ──


class TestDispatchMatrix:
    @pytest.mark.parametrize("url,platform", [
        (_TB, "taobao"), (_TM, "tmall"), (_PDD, "pdd"),
    ])
    def test_platform_url_calls_its_adapter_once(self, url, platform):
        with ExitStack() as stack:
            fetch = _patch_env(stack, _adapter_mod(platform),
                               _product_info(platform))
            graph = cloud_probe.build_graph_envelope(
                item_id=_product_info(platform)["item_id"],
                detail_url=url, poll_category=True)
        assert fetch.call_count == 1
        # 直调纪律：适配器拿到平台 item 归一 target（首参 cdp_url，次参 URL）
        assert graph["envelope"]["source"]["platform"] == platform

    def test_taobao_dispatch_does_not_touch_pdd_adapter(self):
        """taobao 分派不碰 pdd 适配器（taobao/tmall 共用 taobao_client，
        其「恰一次」由分派矩阵 call_count==1 覆盖）。"""
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"), _product_info("taobao"))
            other = stack.enter_context(mock.patch(
                "scripts.lib.pdd_client.fetch_product"))
            cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        other.assert_not_called()

    def test_pdd_dispatch_does_not_touch_taobao_adapter(self):
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("pdd"), _product_info("pdd"))
            other = stack.enter_context(mock.patch(
                "scripts.lib.taobao_client.fetch_product"))
            cloud_probe.build_graph_envelope(
                item_id="734654654654", detail_url=_PDD, poll_category=True)
        other.assert_not_called()


class Test1688ByteCompat:
    def test_1688_url_never_touches_adapters(self):
        """1688 URL → 原链（AK API → CDP enrich），两适配器 assert_not_called。"""
        with ExitStack() as stack:
            tb = stack.enter_context(
                mock.patch("scripts.lib.taobao_client.fetch_product"))
            pdd = stack.enter_context(
                mock.patch("scripts.lib.pdd_client.fetch_product"))
            stack.enter_context(
                mock.patch("scripts.lib.config_store._require_auth"))
            api = stack.enter_context(mock.patch(
                "scripts.lib.ak_1688_client.get_product_details",
                return_value={"980815374096": {
                    "title": "1688 测试商品", "price": "10.00",
                    "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
                    "categories": [],
                }}))
            enrich = stack.enter_context(mock.patch(
                "scripts.lib.ak_1688_client.enrich_product_with_cdp",
                return_value={"data": {
                    "title": "1688 测试商品", "price": "10.00",
                    "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
                    "attributes": [], "option_groups": [], "sku_details": [],
                    "shipping": {"freightCny": 5.0}, "seller": "测试店铺",
                    "description": "",
                }, "source": "cdp"}))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_ozon_credentials",
                return_value={"client_id": "cid", "api_key": "akey"}))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_mxou_token", return_value="tok"))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_token", return_value="tok"))
            stack.enter_context(
                mock.patch("scripts.lib.config_store.get_store_profile",
                           return_value={}))
            stack.enter_context(
                mock.patch("scripts.lib.config_store.get_template_profile",
                           return_value={}))
            graph = cloud_probe.build_graph_envelope(
                item_id="980815374096", detail_url=_1688, poll_category=False)
        tb.assert_not_called()
        pdd.assert_not_called()
        api.assert_called_once()
        enrich.assert_called_once()
        draft = graph["envelope"]["draft"]
        assert draft["item_id"] == "980815374096"
        assert draft["purchase_url"] == _1688
        # 1688 信封仍带数字 cid 键（学习链数据源，逐字节旧行为）
        source = graph["envelope"]["source"]
        assert "category_id" in source
        assert draft["purchase_cost"] == 15.0  # 10.00 + freightCny 5.0

    def test_unknown_url_falls_back_to_1688_chain(self):
        """parse_platform_url 失败（非四平台）→ 回落旧链（历史误提取行为保留）。"""
        with ExitStack() as stack:
            tb = stack.enter_context(
                mock.patch("scripts.lib.taobao_client.fetch_product"))
            pdd = stack.enter_context(
                mock.patch("scripts.lib.pdd_client.fetch_product"))
            stack.enter_context(
                mock.patch("scripts.lib.config_store._require_auth"))
            stack.enter_context(mock.patch(
                "scripts.lib.ak_1688_client.get_product_details",
                return_value={}))
            enrich = stack.enter_context(mock.patch(
                "scripts.lib.ak_1688_client.enrich_product_with_cdp",
                return_value={"data": {
                    "title": "T", "price": "1",
                    "images": ["https://cbu01.alicdn.com/img/ibank/y.jpg"],
                }, "source": "cdp"}))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_ozon_credentials",
                return_value={"client_id": "cid", "api_key": "akey"}))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_mxou_token", return_value="t"))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_token", return_value="t"))
            stack.enter_context(
                mock.patch("scripts.lib.config_store.get_store_profile",
                           return_value={}))
            stack.enter_context(
                mock.patch("scripts.lib.config_store.get_template_profile",
                           return_value={}))
            graph = cloud_probe.build_graph_envelope(
                item_id="123456", detail_url="https://example.com/1.html",
                poll_category=False)
        tb.assert_not_called()
        pdd.assert_not_called()
        enrich.assert_called_once()
        assert graph["envelope"]["draft"]["purchase_url"] == \
            "https://example.com/1.html"


# ── 信封 cid 键纪律（最高优先断言）──


class TestCidKeyDiscipline:
    @pytest.mark.parametrize("url,platform", [
        (_TB, "taobao"), (_TM, "tmall"), (_PDD, "pdd"),
    ])
    def test_platform_envelope_omits_all_cid_keys(self, url, platform):
        """新平台信封绝无 1688-cid 数字键——L0 学习写侧自然跳过（零污染）。"""
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod(platform),
                       _product_info(platform))
            graph = cloud_probe.build_graph_envelope(
                item_id=_product_info(platform)["item_id"],
                detail_url=url, poll_category=True)
        draft = graph["envelope"]["draft"]
        source = graph["envelope"]["source"]
        assert "source_category_id" not in draft
        assert "source_category_id" not in source
        # belt-and-suspenders（fix round 1 Minor #4）：draft 侧数字 cid 键同样缺席
        assert "category_id" not in draft
        assert "category_id" not in source
        # ozon_category 缺省（无 manual/无本地猜测）→ worker 文本+LLM 链
        assert "ozon_category" not in draft
        # 适配器未产出类目词 → 键省略（不写空壳）
        assert "source_category_path" not in source
        assert "source_category" not in draft

    def test_platform_source_category_path_kept_when_supplied(self):
        """中文类目词（worker R4 重配可复用）适配器有则透传——绝不因 cid 纪律误删。"""
        product = _product_info(
            "taobao", source_category_path="家居 > 杯子 > 马克杯")
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"), product)
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        draft = graph["envelope"]["draft"]
        source = graph["envelope"]["source"]
        assert source["source_category_path"] == "家居 > 杯子 > 马克杯"
        assert draft["source_category"] == "家居 > 杯子 > 马克杯"
        # 中文词在，数字 cid 仍绝无
        assert "source_category_id" not in draft
        assert "category_id" not in source

    def test_source_platform_registered(self):
        """source.platform 填入（批1 契约：worker 零强制消费，缺失按域名推断）。"""
        for platform, url in (("taobao", _TB), ("tmall", _TM), ("pdd", _PDD)):
            with ExitStack() as stack:
                _patch_env(stack, _adapter_mod(platform),
                           _product_info(platform))
                graph = cloud_probe.build_graph_envelope(
                    item_id=_product_info(platform)["item_id"],
                    detail_url=url, poll_category=True)
            assert graph["envelope"]["source"]["platform"] == platform

    def test_manual_ozon_category_passthrough_on_platform(self):
        """--category-id/--type-id 人工类目直传（source=manual）平台不受影响——
        这是 Ozon 类目不是货源 cid，与 cid 纪律正交。"""
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"),
                       _product_info("taobao"))
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True,
                category_id="123456", type_id="789101")
        ozc = graph["envelope"]["draft"]["ozon_category"]
        assert ozc["description_category_id"] == "123456"
        assert ozc["type_id"] == "789101"
        assert ozc["source"] == "manual"


# ── draft 组装语义 ──


class TestDraftAssembly:
    @pytest.mark.parametrize("url,platform,canonical", [
        (_TB, "taobao", _TB_CANONICAL),
        (_TM, "tmall", _TM_CANONICAL),
        (_PDD, "pdd", _PDD_CANONICAL),
    ])
    def test_draft_identity_fields(self, url, platform, canonical):
        """item_id=平台 item_id、purchase_url=canonical_url（两处一致）。"""
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod(platform),
                       _product_info(platform))
            graph = cloud_probe.build_graph_envelope(
                item_id=_product_info(platform)["item_id"],
                detail_url=url, poll_category=True)
        draft = graph["envelope"]["draft"]
        assert draft["item_id"] == _product_info(platform)["item_id"]
        assert draft["purchase_url"] == canonical
        assert graph["envelope"]["source"]["purchase_url"] == canonical
        assert draft["currency"] == "CNY"
        assert draft["supplier"] == "某某家居专营店"
        assert draft["attributes"]["材质"] == "陶瓷"

    def test_purchase_cost_freight_absent_price_only(self):
        """运费未知（shipping 无 freightCny）→ purchase_cost=代表变体价。"""
        product = _product_info("taobao", shipping={})
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"), product)
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        # 中位价 15.80（[12.90,15.80,25.00] → 取中），无运费
        assert graph["envelope"]["draft"]["purchase_cost"] == pytest.approx(15.80)

    def test_purchase_cost_free_shipping_zero_freight(self):
        """包邮（freightCny=0）→ price + 0（真实 0 保留）。"""
        product = _product_info(
            "taobao", shipping={"freightCny": 0.0, "freeShipping": True})
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"), product)
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        assert graph["envelope"]["draft"]["purchase_cost"] == pytest.approx(15.80)

    def test_purchase_cost_with_freight(self):
        """purchase_cost = 代表变体价 + freightCny（8 元运费）。"""
        product = _product_info("taobao", shipping={"freightCny": 8.0})
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"), product)
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        assert graph["envelope"]["draft"]["purchase_cost"] == pytest.approx(23.80)

    def test_weight_missing_falls_back_to_existing_path(self):
        """weight_grams=None → 现有软兜底（50g + weight_estimated 标记），
        dims 全缺 → 2:1.5:1 估算 + dimensions_estimated（零重复造）。"""
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"),
                       _product_info("taobao"))
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        draft = graph["envelope"]["draft"]
        assert draft["weight"] == 50
        assert draft.get("weight_estimated") is True
        assert draft.get("dimensions_estimated") is True
        d = draft["dimensions"]
        # 2:1.5:1 比例（_validate_and_fix_product_data 现有估算，比例断言而非
        # 绝对值——密度/基准常量属 1688 共享兜底，由其自身测试锁定）
        assert d["height"] > 0
        assert round(d["width"] / d["height"], 1) == 1.5
        assert round(d["length"] / d["width"], 1) == 1.3

    def test_weight_from_adapter_preserved(self):
        """适配器产出的真实重量（属性表解析）原样透传，不兜底。"""
        product = _product_info("taobao", weight_grams=250)
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"), product)
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        draft = graph["envelope"]["draft"]
        assert draft["weight"] == 250
        assert "weight_estimated" not in draft

    def test_taobao_imgextra_main_image_survives_to_draft(self):
        """端到端：淘宝 imgextra 主图经平台感知滤网活到 draft.images
        （前置硬警报回归——不修则此处为空 → validate「产品图片为空」）。"""
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"),
                       _product_info("taobao"))
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        assert graph["envelope"]["draft"]["images"], "淘宝主图被 bad-token 误杀"


# ── 变体折叠复用 ──


class TestVariantCollapse:
    def test_multi_variant_median_selection(self):
        """3 个平台 SKU → 折叠 1 个代表变体（中位价 15.80）。"""
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"),
                       _product_info("taobao"))
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        draft = graph["envelope"]["draft"]
        assert draft["price"] == pytest.approx(15.80)
        assert "variants" not in draft  # 折叠后单 SKU 平铺（1688 同语义）

    def test_single_variant_platform(self):
        """单 SKU 商品 → 折叠直通（price+freight）。"""
        product = _product_info(
            "pdd",
            sku_details=[{"sku_id": "only", "name": "默认", "price": 9.90,
                          "image": ""}],
            price_ranges=[9.90, 9.90], price="9.90",
            shipping={"freightCny": 6.0})
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("pdd"), product)
            graph = cloud_probe.build_graph_envelope(
                item_id="734654654654", detail_url=_PDD, poll_category=True)
        draft = graph["envelope"]["draft"]
        assert draft["purchase_cost"] == pytest.approx(15.90)
        assert draft["sku_id"] == "734654654654_0"

    def test_quantity_variants_pick_single_unit(self):
        """数量变体（1只装/5只装）→ 复用 _collapse_variants_to_single 的
        「1只装中位」策略（平台 SKU 命名同构）。"""
        product = _product_info(
            "pdd",
            sku_details=[
                {"sku_id": "q1", "name": "1只装", "price": 5.00, "image": ""},
                {"sku_id": "q2", "name": "5只装", "price": 20.00, "image": ""},
                {"sku_id": "q3", "name": "10只装", "price": 36.00, "image": ""},
            ],
            price="5.00", price_ranges=[5.00, 36.00])
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("pdd"), product)
            graph = cloud_probe.build_graph_envelope(
                item_id="734654654654", detail_url=_PDD, poll_category=True)
        assert graph["envelope"]["draft"]["purchase_cost"] == pytest.approx(5.00)

    def test_no_sku_details_default_variant(self):
        """sku_details 空 → 兜底默认变体（1688 同语义），价取商品价。"""
        product = _product_info(
            "taobao", sku_details=[], price_ranges=[12.90, 12.90])
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"), product)
            graph = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        draft = graph["envelope"]["draft"]
        assert draft["sku_id"] == "679836775118"
        assert draft["purchase_cost"] == pytest.approx(12.90)


# ── 尾部形状对齐机制闸（fix round 1 Minor #1）──
# 双副本（平台路径 vs 1688 路径组装尾部）此前只有 docstring「对齐义务」——本闸
# 断言平台路径 draft/source/extensions 键集合 ⊆ 1688 路径（modulo 文档化差量），
# 未来任一侧漂移即红：
# - 平台独有（CONTRACT-v4 §1.1.2）：source.platform（1688 缺省按域名推断故不填）
# - 1688 独有：extensions.cdp_degraded（api_only 降级标记，平台链无 CDP 降级语义）
# - shipping/dimensions_estimated/weight_estimated/ozon_category/source_category
#   均为两侧同构条件键——夹具两侧等价触发（shipping 在场、weight 缺失、manual 直传、
#   类目词在场），锁条件模式而不只锁基形
_DOC_PLATFORM_ONLY_SOURCE_KEYS = {"platform"}
_DOC_1688_ONLY_EXTENSION_KEYS = {"cdp_degraded"}


class TestEnvelopeShapeSubsetOf1688:
    def _build_both(self):
        """等价触发两侧全部条件键：shipping 在场 + weight 缺失（est 标记）+
        manual 类目直传 + 中文类目词在场 + cid（1688 侧唯一）。"""
        categories = [{"name": "家居", "id": "61"}, {"name": "杯子", "id": "9901"}]
        platform_product = _product_info(
            "taobao",
            shipping={"freightCny": 8.0},
            source_category_path="家居 > 杯子 > 马克杯",
        )
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"), platform_product)
            p = cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True,
                category_id="123456", type_id="789101")
        data_1688 = {
            "title": "1688 测试马克杯", "price": "10.00",
            "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
            "attributes": [], "option_groups": [],
            "sku_details": [{"name": "默认", "price": 10.0, "image": ""}],
            "shipping": {"freightCny": 8.0}, "seller": "测试店铺",
            "description": "",
        }
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch("scripts.lib.config_store._require_auth"))
            stack.enter_context(mock.patch(
                "scripts.lib.ak_1688_client.get_product_details",
                return_value={"980815374096": {
                    "title": "1688 测试马克杯", "price": "10.00",
                    "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
                    "categories": categories,
                }}))
            stack.enter_context(mock.patch(
                "scripts.lib.ak_1688_client.enrich_product_with_cdp",
                return_value={"data": dict(data_1688), "source": "cdp"}))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_ozon_credentials",
                return_value={"client_id": "cid", "api_key": "akey"}))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_mxou_token", return_value="tok"))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_token", return_value="tok"))
            stack.enter_context(
                mock.patch("scripts.lib.config_store.get_store_profile",
                           return_value={}))
            stack.enter_context(
                mock.patch("scripts.lib.config_store.get_template_profile",
                           return_value={}))
            w = cloud_probe.build_graph_envelope(
                item_id="980815374096", detail_url=_1688, poll_category=False,
                category_id="123456", type_id="789101")
        return p["envelope"], w["envelope"]

    def test_draft_source_extensions_key_subset(self):
        p_env, w_env = self._build_both()
        p_draft, w_draft = p_env["draft"], w_env["draft"]
        p_source, w_source = p_env["source"], w_env["source"]
        p_ext, w_ext = p_env["extensions"], w_env["extensions"]

        drift_draft = set(p_draft) - set(w_draft)
        drift_source = set(p_source) - set(w_source) - _DOC_PLATFORM_ONLY_SOURCE_KEYS
        drift_ext = set(p_ext) - set(w_ext) - _DOC_1688_ONLY_EXTENSION_KEYS
        assert not drift_draft, f"平台 draft 出现 1688 没有的键（尾部形状漂移）: {drift_draft}"
        assert not drift_source, f"平台 source 漂移（超出文档化差量 {_DOC_PLATFORM_ONLY_SOURCE_KEYS}）: {drift_source}"
        assert not drift_ext, f"平台 extensions 漂移（超出文档化差量 {_DOC_1688_ONLY_EXTENSION_KEYS}）: {drift_ext}"

        # 条件键两侧确已触发（夹具自检——防止子集断言空转成基形比较）
        for key in ("shipping", "dimensions_estimated", "weight_estimated",
                    "ozon_category", "source_category", "sku_id", "price"):
            assert key in p_draft and key in w_draft, key
        assert "source_category_id" in w_draft  # 1688 独有 cid 键在场
        assert "source_category_id" not in p_draft  # 平台侧 cid 纪律仍成立
        assert "source_category_path" in p_source and "source_category_path" in w_source

    def test_platform_pricing_audit_line_present(self):
        """Minor #2：平台路径尾部与 1688 同构携带 pricing 审计行（观测面对齐）。"""
        with ExitStack() as stack:
            _patch_env(stack, _adapter_mod("taobao"),
                       _product_info("taobao"))
            audit_cls = stack.enter_context(mock.patch.object(
                cloud_probe, "AuditLogger"))
            cloud_probe.build_graph_envelope(
                item_id="679836775118", detail_url=_TB, poll_category=True)
        events = [call.args[1] for call in audit_cls.return_value.log.call_args_list]
        assert "integrity" in events
        assert "pricing" in events


# ── 适配器直调：人话错误透传 ──


class TestAdapterErrorPropagation:
    def test_taobao_error_propagates_verbatim(self):
        """适配器人话错误原样上抛——绝不降级「数据不完整」（批2 concern #2）。"""
        msg = ("淘宝详情接口返回失败（ret=FAIL_SYS_USER_VALIDATE）——"
               "失败出声，拒绝编造数据")
        with ExitStack() as stack:
            fetch = _patch_env(stack, _adapter_mod("taobao"),
                               _product_info("taobao"))
            fetch.side_effect = TaobaoFetchError(msg)
            with pytest.raises(TaobaoFetchError, match="FAIL_SYS_USER_VALIDATE"):
                cloud_probe.build_graph_envelope(
                    item_id="679836775118", detail_url=_TB, poll_category=True)

    def test_pdd_error_propagates_verbatim(self):
        msg = "拼多多未登录或会话过期（页内无 pdd_user_id cookie）——请在工具 Chrome 登录拼多多后重试"
        with ExitStack() as stack:
            fetch = _patch_env(stack, _adapter_mod("pdd"),
                               _product_info("pdd"))
            fetch.side_effect = PddFetchError(msg)
            with pytest.raises(PddFetchError, match="请在工具 Chrome 登录拼多多后重试"):
                cloud_probe.build_graph_envelope(
                    item_id="734654654654", detail_url=_PDD, poll_category=True)

    def test_with_retry_platform_single_attempt_no_wrap(self):
        """with_retry 对新平台单次尝试：适配器错误不上包「CDP failed after N
        retries」（重试语义=1688 限流冷却，对登录/风控错误无意义）。"""
        msg = "淘宝/天猫未登录或会话过期（TOKEN_EXPIRED）——请在工具 Chrome 登录后重试"
        with ExitStack() as stack:
            fetch = _patch_env(stack, _adapter_mod("taobao"),
                               _product_info("taobao"))
            fetch.side_effect = TaobaoFetchError(msg)
            with pytest.raises(TaobaoFetchError) as excinfo:
                cloud_probe.build_graph_envelope_with_retry(
                    item_id="679836775118", detail_url=_TB,
                    max_retries=3, retry_delay=0.0)
        assert fetch.call_count == 1
        assert "CDP failed after" not in str(excinfo.value)

    def test_with_retry_1688_retry_loop_untouched(self):
        """1688 RuntimeError 仍走原重试循环（max_retries 次数行为不变）。"""
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch("scripts.lib.config_store._require_auth"))
            stack.enter_context(mock.patch(
                "scripts.lib.ak_1688_client.get_product_details",
                return_value={}))
            enrich = stack.enter_context(mock.patch(
                "scripts.lib.ak_1688_client.enrich_product_with_cdp",
                side_effect=RuntimeError("1688 anti-bot")))
            stack.enter_context(mock.patch.object(
                cloud_probe, "_get_ozon_credentials",
                return_value={"client_id": "c", "api_key": "a"}))
            sleeps = stack.enter_context(
                mock.patch("scripts.cloud_probe.time.sleep"))
            with pytest.raises(RuntimeError, match="CDP failed after"):
                cloud_probe.build_graph_envelope_with_retry(
                    item_id="980815374096", detail_url=_1688,
                    max_retries=2, retry_delay=0.0)
        assert enrich.call_count == 2
        assert sleeps.call_count == 1  # 最后一次失败后不再 sleep
