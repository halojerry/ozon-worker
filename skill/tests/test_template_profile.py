#!/usr/bin/env python3
"""D11: skill 读 worker listing_templates 默认配置（get_template_profile + 三段降级注入）。

背景：worker /api/v1/templates（GET，Bearer 鉴权）返回 [{is_default, config,
store_overrides}]，config 白名单 7 字段（margin_rate/commission_rate/fx_buffer/
offer_id_prefix/follow_type/stock/warehouse_id）。skill 从此获得「默认上架配置」——
webui 配默认模板后，graph 不传 margin_rate 也生效（多店铺开箱即用）。

cloud_probe 注入段三段降级（R5 已定稿）：显式 extensions > worker 默认模板 >
本地 stores.json。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_template_profile.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402
from scripts.lib import config_store  # noqa: E402

CONFIG_KEYS = ("margin_rate", "commission_rate", "fx_buffer",
               "offer_id_prefix", "follow_type", "stock", "warehouse_id")

ITEM_ID = "980815374096"
DETAIL_URL = f"https://detail.1688.com/offer/{ITEM_ID}.html"
ALICDN_IMG = "https://cbu01.alicdn.com/img/ibank/2024/O/123/456.jpg"


def _tpl(**overrides) -> dict:
    """worker /templates 单条记录（ListingTemplateOut 结构）。"""
    t = {
        "id": "tpl-1",
        "tenant_id": "tenant-1",
        "name": "默认模板",
        "is_default": True,
        "config": {
            "margin_rate": 0.3,
            "commission_rate": 0.15,
            "fx_buffer": 0.08,
            "offer_id_prefix": "MX",
            "follow_type": "hand",
            "stock": 500,
            "warehouse_id": "wh-1",
        },
        "store_overrides": {},
    }
    t.update(overrides)
    return t


def _clear_template_cache():
    config_store._TEMPLATE_CACHE.clear()


# ── get_template_profile ────────────────────────────────────────────────

def test_get_template_profile_returns_7_fields():
    """命中 is_default 模板 → 返回白名单 7 字段。"""
    _clear_template_cache()
    with mock.patch("requests.get") as m_get:
        m_get.return_value.status_code = 200
        m_get.return_value.json.return_value = [_tpl()]
        profile = config_store.get_template_profile("sk-token-a")
    assert profile is not None
    for k in CONFIG_KEYS:
        assert k in profile, f"get_template_profile 应返回 {k}, got {profile}"
    assert profile["margin_rate"] == 0.3
    assert profile["warehouse_id"] == "wh-1"


def test_get_template_profile_api_failure_returns_none():
    """API 异常/非 200 → None（调用方降级本地）。"""
    _clear_template_cache()
    with mock.patch("requests.get", side_effect=RuntimeError("network down")):
        assert config_store.get_template_profile("sk-token-b") is None
    _clear_template_cache()
    with mock.patch("requests.get") as m_get:
        m_get.return_value.status_code = 500
        assert config_store.get_template_profile("sk-token-b") is None


def test_get_template_profile_no_default_returns_none():
    """列表无 is_default 模板 → None。"""
    _clear_template_cache()
    with mock.patch("requests.get") as m_get:
        m_get.return_value.status_code = 200
        m_get.return_value.json.return_value = [_tpl(is_default=False, id="tpl-x")]
        assert config_store.get_template_profile("sk-token-c") is None


def test_get_template_profile_store_override_applied():
    """credential_id 命中 store_overrides → 覆盖顶层 config 同 key。"""
    _clear_template_cache()
    t = _tpl(store_overrides={"4718259": {"margin_rate": 0.5, "stock": 99}})
    with mock.patch("requests.get") as m_get:
        m_get.return_value.status_code = 200
        m_get.return_value.json.return_value = [t]
        profile = config_store.get_template_profile(
            "sk-token-d", credential_id="4718259")
    assert profile["margin_rate"] == 0.5, f"store_overrides 应覆盖 margin_rate, got {profile}"
    assert profile["stock"] == 99
    # 未覆盖的 key 保留顶层 config
    assert profile["commission_rate"] == 0.15


def test_get_template_profile_explicit_id_wins():
    """template_id 显式 → 用指定模板而非默认。"""
    _clear_template_cache()
    t1 = _tpl()
    t2 = _tpl(id="tpl-2", is_default=False, name="指定",
              config={"margin_rate": 0.4})
    with mock.patch("requests.get") as m_get:
        m_get.return_value.status_code = 200
        m_get.return_value.json.return_value = [t1, t2]
        profile = config_store.get_template_profile("sk-token-e", template_id="tpl-2")
    assert profile["margin_rate"] == 0.4, f"显式 template_id 应优先, got {profile}"


# ── cloud_probe 注入段三段降级 ──────────────────────────────────────────

def _api_enriched() -> dict:
    """enrich_product_with_cdp 的 api_only 返回（跳过 Ozon 反查段，测试快速）。"""
    data = {
        "title": "宠物自动饮水器 2L",
        "price": "5.50",
        "brand": "",
        "seller": "",
        "images": [ALICDN_IMG],
        "weight_grams": None,
        "packaging_rows": [],
        "shipping": {},
        "description": "",
        "sku_details": [],
        "attributes": [],
        "option_groups": [],
        "category_id": "",
    }
    return {"ok": False, "degraded": True, "degraded_reason": "mock",
            "user_action": None, "data": data, "source": "api_only"}


def _build(template_profile=None, store_profile=None, template_id=""):
    """调用 build_graph_envelope（mock 全部外部依赖 + 模板/店铺 profile）。"""
    with mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    return_value={ITEM_ID: {}}), \
         mock.patch("scripts.lib.ak_1688_client.enrich_product_with_cdp",
                    return_value=_api_enriched()), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "4718259", "api_key": "key"}), \
         mock.patch.object(cloud_probe, "_get_mxou_token", return_value="sk-test"), \
         mock.patch("scripts.lib.config_store.get_store_profile",
                    return_value=store_profile or {}), \
         mock.patch("scripts.lib.config_store.get_template_profile",
                    return_value=template_profile):
        return cloud_probe.build_graph_envelope(
            item_id=ITEM_ID,
            detail_url=DETAIL_URL,
            poll_category=False,
            template_id=template_id,
        )


def test_merge_explicit_ext_wins():
    """_merge_config_tiers：显式 extensions 值恒优先（不被模板/本地覆盖）。"""
    ext = {"margin_rate": 0.4}
    cloud_probe._merge_config_tiers(
        ext, template_profile={"margin_rate": 0.3}, store_profile={"margin_rate": 0.2})
    assert ext["margin_rate"] == 0.4


def test_merge_template_wins_over_local():
    """_merge_config_tiers：worker 模板覆盖本地 stores.json。"""
    ext = {}
    cloud_probe._merge_config_tiers(
        ext, template_profile={"margin_rate": 0.3, "stock": 500},
        store_profile={"margin_rate": 0.2})
    assert ext["margin_rate"] == 0.3
    assert ext["stock"] == 500


def test_merge_local_fallback():
    """_merge_config_tiers：模板无值 → 本地 stores.json 兜底。"""
    ext = {}
    cloud_probe._merge_config_tiers(
        ext, template_profile=None, store_profile={"margin_rate": 0.2, "commission_rate": 0.1})
    assert ext["margin_rate"] == 0.2
    assert ext["commission_rate"] == 0.1


def test_merge_zero_pricing_not_injected():
    """_merge_config_tiers：本地 margin=0 不注入（worker 默认兜底，旧行为保留）。"""
    ext = {}
    cloud_probe._merge_config_tiers(
        ext, template_profile={"margin_rate": 0.3}, store_profile={"margin_rate": 0})
    assert ext["margin_rate"] == 0.3


def test_build_template_overrides_store():
    """cloud_probe 注入段：模板 margin 优先于本地 stores.json。"""
    graph = _build(template_profile={"margin_rate": 0.3, "warehouse_id": "wh-1"},
                   store_profile={"margin_rate": 0.2})
    ext = graph["envelope"]["extensions"]
    assert ext["margin_rate"] == 0.3
    assert ext["warehouse_id"] == "wh-1"


def test_build_template_none_falls_back_to_local():
    """模板拿不到（None）→ 本地 stores.json 兜底。"""
    graph = _build(template_profile=None, store_profile={"margin_rate": 0.2})
    assert graph["envelope"]["extensions"]["margin_rate"] == 0.2


if __name__ == "__main__":
    import traceback

    failed = 0
    total = 0
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
