"""P2-6: fx_rate 三级解析 —— CLI 显式 > 店铺 stores.json fx_rate > settings.json fx_rate > DEFAULT_FX_RATE 0.075。

- get_store_profile whitelist 含 fx_rate（配置后返回）
- cmd_discover 缺省 --fx-rate（None）→ 店铺 fx_rate 优先
- 店铺无 fx_rate → settings.json fx_rate
- 店铺/settings 都无 → DEFAULT_FX_RATE（0.075）
- --fx-rate 显式 → 覆盖店铺/settings
"""
from __future__ import annotations

import io
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import DEFAULT_FX_RATE, ProductCandidate


def _mk(pid="p1"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title="Автопоилка для кошек",
                         ozon_price=1500.0)
    c.status = "ok"
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def _discover_args(**overrides):
    import argparse
    defaults = dict(
        url="", keyword="поилка", max_products=50, min_margin=15.0, fx_rate=None,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        rules="", export="", output="", auto_submit=False,
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=False, blue_ocean_source="", blue_ocean_csv="",
        china=False, local=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _settings_side_effect(**values):
    """get_setting mock：只对指定 key 返回值，其余回退 default（如 visual_review）。"""
    def _get(key, default=None):
        return values.get(key, default)
    return _get


def _run_discover(args, candidates, store_profile, settings_values):
    """跑完整 cmd_discover（采集/匹配 mock），返回 (rc, match_selected_mock)。"""
    from scripts import cli
    c = candidates
    with mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                    return_value=candidates), \
         mock.patch("scripts.cli._interactive_select", return_value=candidates), \
         mock.patch("scripts.lib.ozon_discovery.match_selected") as ms, \
         mock.patch("scripts.lib.config_store.get_store_profile",
                    return_value=store_profile), \
         mock.patch("scripts.lib.config_store.get_setting",
                    side_effect=_settings_side_effect(**settings_values)), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = cli.cmd_discover(args)
    return rc, ms


# ── config_store.get_store_profile whitelist ───────────────────────────────

def test_get_store_profile_includes_fx_rate():
    """get_store_profile: fx_rate 在 whitelist 内——店铺配置后返回，凭证不泄露。"""
    from scripts.lib import config_store
    with mock.patch("scripts.lib.config_store.get_store",
                    return_value={"client_id": "111", "api_key": "k",
                                  "currency": "CNY", "fx_rate": 0.10}):
        profile = config_store.get_store_profile("shop")
    assert profile.get("fx_rate") == 0.10, \
        f"get_store_profile 应返回 fx_rate, got {profile}"
    assert "client_id" not in profile and "api_key" not in profile, \
        "whitelist 不应泄露凭证字段"


# ── cmd_discover fx_rate 三级解析 ──────────────────────────────────────────

def test_cmd_discover_store_fx_rate_wins_over_settings():
    """缺省 --fx-rate：店铺 fx_rate=0.10 → match_selected 收到 0.10（优先于 settings 0.09）。"""
    c = _mk()
    args = _discover_args(store="shop")
    rc, ms = _run_discover(args, [c], {"fx_rate": 0.10}, {"fx_rate": 0.09})
    assert rc == 0
    assert ms.call_args.kwargs["fx_rate"] == 0.10, \
        f"店铺 fx_rate 应优先, got {ms.call_args.kwargs.get('fx_rate')}"


def test_cmd_discover_settings_fx_rate_fallback():
    """店铺无 fx_rate → settings.json fx_rate=0.09 → match_selected 收到 0.09。"""
    c = _mk()
    args = _discover_args()
    rc, ms = _run_discover(args, [c], {}, {"fx_rate": 0.09})
    assert rc == 0
    assert ms.call_args.kwargs["fx_rate"] == 0.09, \
        f"settings fx_rate 应兜底, got {ms.call_args.kwargs.get('fx_rate')}"


def test_cmd_discover_default_fx_rate_fallback():
    """店铺/settings 都无 fx_rate → DEFAULT_FX_RATE（0.075）。"""
    c = _mk()
    args = _discover_args()
    rc, ms = _run_discover(args, [c], {}, {})
    assert rc == 0
    assert ms.call_args.kwargs["fx_rate"] == DEFAULT_FX_RATE, \
        f"应回退 DEFAULT_FX_RATE={DEFAULT_FX_RATE}, got {ms.call_args.kwargs.get('fx_rate')}"


def test_cmd_discover_explicit_fx_rate_wins():
    """--fx-rate 显式 0.06 → 覆盖店铺 0.10 / settings 0.09。"""
    c = _mk()
    args = _discover_args(fx_rate=0.06, store="shop")
    rc, ms = _run_discover(args, [c], {"fx_rate": 0.10}, {"fx_rate": 0.09})
    assert rc == 0
    assert ms.call_args.kwargs["fx_rate"] == 0.06, \
        f"显式 --fx-rate 应优先, got {ms.call_args.kwargs.get('fx_rate')}"


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
