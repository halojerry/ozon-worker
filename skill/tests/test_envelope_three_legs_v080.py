#!/usr/bin/env python3
"""arch-findings #5: 信封三腿统一——extensions 配置注入唯一入口回归（纯 mock）。

原裂口：graph/跨平台走 _merge_config_tiers（含模板 9048 前缀/三档键），follow 腿
手写 7 数值键循环（只读 stores.json，模板层三档键缺位），discover 降级腿手写
3 键循环（同缺模板层）——三条腿注入口径互斥漂移。

统一后：
- follow/discover 降级腿同走 _merge_config_tiers 三段降级
  （显式 > worker 模板 get_template_profile > stores.json；数值键非零纪律同源）；
- 两腿排除 offer_id_prefix/traffic_keywords（9048 前缀/SEO 流量词只属 graph 主链，
  跟卖 offer upsert/并卡语义不带前缀；builder 模板层若已注入，follow 腿剥离）；
- follow_type 由调用方先 setdefault（hand/discover），不被模板覆盖。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_envelope_three_legs_v080.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


# ── ① _merge_config_tiers 排除参数 ─────────────────────────────────────────

def test_merge_exclude_keys_skips_injection():
    """exclude_keys 命中的键即使模板/store 提供也不注入。"""
    ext: dict = {}
    cloud_probe._merge_config_tiers(
        ext,
        template_profile={"offer_id_prefix": "MX", "traffic_keywords": ["корм"],
                          "margin_rate": 0.3},
        store_profile={},
        exclude_keys=("offer_id_prefix", "traffic_keywords"))
    assert "offer_id_prefix" not in ext
    assert "traffic_keywords" not in ext
    assert ext["margin_rate"] == 0.3, "排除只命中声明键，其余照常注入"


def test_merge_without_exclude_keeps_full_behavior():
    """不传 exclude_keys → 行为与旧版逐字一致（graph 主链零变化）。"""
    ext: dict = {}
    cloud_probe._merge_config_tiers(
        ext,
        template_profile={"offer_id_prefix": "MX",
                          "traffic_keywords": ["корм", "автопоилка"]},
        store_profile={})
    assert ext["offer_id_prefix"] == "MX"
    assert ext["traffic_keywords"] == ["корм", "автопоилка"]


def test_merge_exclude_does_not_strip_explicit_ext_values():
    """exclude 只封「注入」，不清洗调用方显式放进的值（显式恒优先语义不变）。"""
    ext = {"margin_rate": 0.5}
    cloud_probe._merge_config_tiers(
        ext, template_profile={"margin_rate": 0.3}, store_profile={},
        exclude_keys=("margin_rate",))
    assert ext["margin_rate"] == 0.5


# ── ② follow 腿 ────────────────────────────────────────────────────────────

_URL = "https://www.ozon.ru/product/avtopoilka-4767514314/"
_CDP_DATA = {"success": True, "images": ["http://img/ozon/1.jpg"], "title": "Автопоилка"}
_CDP_RESULTS = [{
    "id": "980815374096", "title": "宠物饮水器", "price": "5.5",
    "image": "http://img/1688/1.jpg", "badge": "符合 3/3 个条件",
}]
_BEST = {"id": "980815374096", "badge_score": 3, "title": "宠物饮水器"}


def _run_follow_capture(builder_envelope: dict, *, store_profile: dict,
                        template_profile: dict | None) -> dict:
    """跑 follow_sell_cloud（全 mock），捕获 submit_envelope 收到的信封。"""
    captured: dict = {}

    def _submit(env):
        captured["envelope"] = env
        return {"ok": True, "task_id": "T-F"}

    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set"), \
         mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
         mock.patch.object(cloud_probe, "_cached_ozon_scrape", return_value=_CDP_DATA), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"), \
         mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics",
                    return_value={}), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp",
                    return_value=_CDP_RESULTS), \
         mock.patch("scripts.lib.ozon_discovery._pick_best_match", return_value=_BEST), \
         mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=builder_envelope), \
         mock.patch.object(cloud_probe, "submit_envelope", side_effect=_submit), \
         mock.patch("scripts.lib.config_store.get_store_profile",
                    return_value=store_profile), \
         mock.patch("scripts.lib.config_store.get_template_profile",
                    return_value=template_profile):
        cloud_probe.follow_sell_cloud(_URL, auto_submit=True, store_id="s1")
    return captured


def test_follow_injects_template_three_tier_keys():
    """follow 腿接入模板层：模板三档键（margin_floor 等）进入信封 extensions。"""
    builder = {"token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
               "envelope": {"draft": {"item_id": "980815374096"},
                            "extensions": {}}}
    tpl = {"margin_floor": 0.6, "margin_anchor": 2.0,
           "variable_cost_rate": 0.155, "offer_id_prefix": "MX"}
    cap = _run_follow_capture(builder, store_profile={}, template_profile=tpl)
    ext = cap["envelope"]["envelope"]["extensions"]
    assert ext["margin_floor"] == 0.6
    assert ext["margin_anchor"] == 2.0
    assert ext["variable_cost_rate"] == 0.155


def test_follow_excludes_prefix_and_traffic_even_from_builder():
    """builder 模板层带来的 offer_id_prefix/traffic_keywords 在 follow 腿剥离。"""
    builder = {"token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
               "envelope": {"draft": {"item_id": "980815374096"},
                            "extensions": {"offer_id_prefix": "MX",
                                           "traffic_keywords": ["корм"]}}}
    cap = _run_follow_capture(builder, store_profile={}, template_profile=None)
    ext = cap["envelope"]["envelope"]["extensions"]
    assert "offer_id_prefix" not in ext, "9048 前缀不得进 follow 信封（并卡语义）"
    assert "traffic_keywords" not in ext
    assert ext.get("follow_sell") is True
    assert ext.get("follow_type") == "hand"


def test_follow_type_not_overridden_by_template():
    """模板下发 follow_type=api 也不得翻掉 follow 腿 setdefault 的 hand。"""
    builder = {"token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
               "envelope": {"draft": {"item_id": "980815374096"},
                            "extensions": {}}}
    tpl = {"follow_type": "api", "margin_rate": 0.3}
    cap = _run_follow_capture(builder, store_profile={}, template_profile=tpl)
    ext = cap["envelope"]["envelope"]["extensions"]
    assert ext["follow_type"] == "hand", "follow_type 是管线标记，非用户配置"
    assert ext["margin_rate"] == 0.3


def test_follow_store_tier_still_works_without_template():
    """模板不可用 → stores.json 层照常注入（三段降级第二兜底，旧语义保持）。"""
    builder = {"token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
               "envelope": {"draft": {"item_id": "980815374096"},
                            "extensions": {}}}
    cap = _run_follow_capture(
        builder,
        store_profile={"margin_rate": 0.28, "commission_rate": 0.12,
                       "margin_floor": 0.55},
        template_profile=None)
    ext = cap["envelope"]["envelope"]["extensions"]
    assert ext["margin_rate"] == 0.28
    assert ext["commission_rate"] == 0.12
    assert ext["margin_floor"] == 0.55


# ── ③ discover 降级腿 ──────────────────────────────────────────────────────

def _mk_candidate(**overrides) -> ProductCandidate:
    base = dict(
        ozon_product_id="4767514314",
        ozon_title="Автопоилка для кошек 2л",
        ozon_price=1290.0,
        match_1688_url="https://detail.1688.com/offer/980815374096.html",
        competing_sellers=4,
    )
    base.update(overrides)
    c = ProductCandidate(
        ozon_product_id=base["ozon_product_id"],
        ozon_title=base["ozon_title"],
        ozon_price=base["ozon_price"],
    )
    c.match_1688_url = base["match_1688_url"]
    c.competing_sellers = base["competing_sellers"]
    c.weight_g = 0
    c.dimensions_mm = {}
    c.ozon_category = {}
    return c


def _build_fallback(cand: ProductCandidate, store_profile: dict,
                    template_profile: dict | None) -> dict | None:
    """走 build_envelope_from_discovery 的降级组装段（retry 失败 → 本地兜底）。"""
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=None), \
         mock.patch("scripts.lib.config_store.get_mxou_token",
                    return_value="sk-test"), \
         mock.patch("scripts.lib.config_store.get_store_profile",
                    return_value=store_profile), \
         mock.patch("scripts.lib.config_store.get_template_profile",
                    return_value=template_profile):
        return cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})


def test_fallback_template_tier_wins_over_store():
    """降级腿三段降级：模板键优先于 stores.json 同键。"""
    result = _build_fallback(
        _mk_candidate(),
        store_profile={"margin_rate": 0.28, "commission_rate": 0.12},
        template_profile={"margin_rate": 0.35, "margin_anchor": 2.0,
                          "offer_id_prefix": "MX"})
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert ext["margin_rate"] == 0.35, "模板层优先于 stores.json"
    assert ext["commission_rate"] == 0.12, "模板未配置的键回落 stores.json"
    assert ext["margin_anchor"] == 2.0
    assert "offer_id_prefix" not in ext, "9048 前缀对降级腿保持排除"


def test_fallback_follow_type_discover_not_overridden():
    """降级腿 follow_type=setdefault discover，模板 follow_type 不覆盖。"""
    result = _build_fallback(
        _mk_candidate(), store_profile={},
        template_profile={"follow_type": "hand"})
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert ext["follow_sell"] is True
    assert ext["follow_type"] == "discover"


def test_fallback_zero_store_values_still_not_injected():
    """数值键非零纪律同源：store 显式 0 值不注入（v0.65 语义保持）。"""
    result = _build_fallback(
        _mk_candidate(), store_profile={"margin_rate": 0, "fx_buffer": 0},
        template_profile=None)
    assert result is not None
    ext = result["envelope"]["extensions"]
    assert "margin_rate" not in ext
    assert "fx_buffer" not in ext


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
    sys.exit(1 if failed else 0)
