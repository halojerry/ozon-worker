#!/usr/bin/env python3
"""P2-8: 多店铺支持验证与加固（TDD RED→GREEN）。

- get_store 多店铺解析：每个 store_id 精确命中；空 → 默认指针；指针空 → 第一个
- "default" 同名店铺歧义：指针字段优先（声明默认）+ 进程内告警一次
- store_id 全链路透传（read-only 验证，不修改 cli.py/cloud_probe）：
  cmd_discover → build_envelope_from_discovery(store_id) + 信封 extensions.store_id
  cmd_follow → follow_sell_cloud(store_id) + follow 缓存 key = product_id:store_id
- batch_test._resolve_credentials 按 store_id 解析不同凭证；显式凭证优先

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_multi_store.py -q
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_multi_store.py -v
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

def _stores_file(default: str, stores: dict) -> dict:
    return {"default": default, "stores": stores}


S1 = {"client_id": "111", "api_key": "aaa"}
S2 = {"client_id": "222", "api_key": "bbb"}
DEFAULT_NAMED = {"client_id": "999", "api_key": "zzz"}


# ── get_store 多店铺解析 ────────────────────────────────────────────────────

def test_get_store_resolves_each_store_by_id():
    """2 店铺 + 明确默认指针 → 各 store_id 精确命中；空 → 默认指针；未知 → None。"""
    from scripts.lib import config_store
    data = _stores_file("shop2", {"shop1": S1, "shop2": S2})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data):
        assert config_store.get_store("shop1") == S1, "shop1 应精确命中"
        assert config_store.get_store("shop2") == S2, "shop2 应精确命中"
        assert config_store.get_store("") == S2, "空 store_id → 默认指针 shop2"
        assert config_store.get_store("nope") is None, "未知店铺 → None"


def test_get_store_no_default_falls_back_first_store():
    """指针空 → 回退第一个店铺（不崩）。"""
    from scripts.lib import config_store
    data = _stores_file("", {"shop1": S1, "shop2": S2})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data):
        assert config_store.get_store("") == S1, "指针空 → 第一个店铺"


# ── "default" 同名店铺歧义（P2-8 核心）──────────────────────────────────────

def test_default_named_store_pointer_wins_and_warns():
    """店铺名为 "default" + 指针指向 shop2 → get_store("") 返回 shop2（指针优先），告警。"""
    from scripts.lib import config_store
    config_store._STORE_DEFAULT_AMBIGUITY_WARNED = False
    data = _stores_file("shop2", {"default": DEFAULT_NAMED, "shop2": S2})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data), \
         mock.patch.object(config_store.logger, "warning") as m_warn:
        got = config_store.get_store("")
    assert got == S2, f"指针声明优先，应返回 shop2 而非 default 同名店铺, got {got}"
    assert m_warn.called, "存在 default 同名店铺时应发出歧义告警"


def test_default_named_store_pointer_is_default():
    """指针值恰为 "default" → 解析到名为 "default" 的店铺（指针优先），告警。"""
    from scripts.lib import config_store
    config_store._STORE_DEFAULT_AMBIGUITY_WARNED = False
    data = _stores_file("default", {"default": DEFAULT_NAMED, "shop2": S2})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data), \
         mock.patch.object(config_store.logger, "warning") as m_warn:
        got = config_store.get_store("")
    assert got == DEFAULT_NAMED, f"指针 = default → 该同名店铺, got {got}"
    assert m_warn.called, "存在 default 同名店铺时应发出歧义告警"


def test_default_named_store_warns_once_per_process():
    """歧义告警进程内只发一次（同命令多次 get_store 不刷屏）。"""
    from scripts.lib import config_store
    config_store._STORE_DEFAULT_AMBIGUITY_WARNED = False
    data = _stores_file("shop2", {"default": DEFAULT_NAMED, "shop2": S2})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data), \
         mock.patch.object(config_store.logger, "warning") as m_warn:
        config_store.get_store("")
        config_store.get_store("")
        config_store.get_store("")
    assert m_warn.call_count == 1, f"应只告警一次, got {m_warn.call_count}"


def test_explicit_store_id_no_default_ambiguity_warning():
    """显式 store_id → 不触发默认歧义告警（即使配置含 default 同名店铺）。"""
    from scripts.lib import config_store
    config_store._STORE_DEFAULT_AMBIGUITY_WARNED = False
    data = _stores_file("shop2", {"default": DEFAULT_NAMED, "shop2": S2})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data), \
         mock.patch.object(config_store.logger, "warning") as m_warn:
        assert config_store.get_store("shop2") == S2
    assert not m_warn.called, "显式 store_id 不应告警"


def test_no_default_pointer_multi_store_warns():
    """指针空 + 多店铺 → 回退第一个并告警（提示显式声明默认）。"""
    from scripts.lib import config_store
    config_store._STORE_DEFAULT_AMBIGUITY_WARNED = False
    data = _stores_file("", {"shop1": S1, "shop2": S2})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data), \
         mock.patch.object(config_store.logger, "warning") as m_warn:
        assert config_store.get_store("") == S1
    assert m_warn.called, "多店铺无默认指针时应告警"


def test_no_default_pointer_single_store_no_warn():
    """指针空 + 单店铺 → 回退无歧义，不告警（避免噪音）。"""
    from scripts.lib import config_store
    config_store._STORE_DEFAULT_AMBIGUITY_WARNED = False
    data = _stores_file("", {"shop1": S1})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data), \
         mock.patch.object(config_store.logger, "warning") as m_warn:
        assert config_store.get_store("") == S1
    assert not m_warn.called, "单店铺回退无歧义，不应告警"


# ── store_id 全链路透传（read-only 验证，不修改 cli.py/cloud_probe）────────

def _profitable(pid="p1", title="Автопоилка для кошек",
                url="https://detail.1688.com/offer/1001.html"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=title, ozon_price=1500.0)
    c.status = "profitable"
    c.match_1688_url = url
    c.has_analytics = False
    c.competing_sellers = 7
    c.rating = 4.5
    return c


def _discover_args(**overrides):
    defaults = dict(
        url="", keyword="поилка", max_products=50, min_margin=15.0, fx_rate=0.075,
        store="", no_analytics=False, min_price=0, max_price=0, brand_filter="nobrand",
        rules="monthly_sales>=1", export="", output="", auto_submit=True,
        fission=False, max_depth=2, allow_depth_3=False, max_total_products=300,
        time_budget=600.0, max_sellers_per_product=20, max_products_per_seller=15,
        non_interactive=False, blue_ocean_source="", blue_ocean_csv="",
        china=False, local=False, review=False, notify=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_discover_threads_store_id_to_build_envelope():
    """cmd_discover store="shop2" → build_envelope_from_discovery 收 store_id="shop2" + extensions.store_id 注入。"""
    from scripts import cli
    c1 = _profitable("p1", "Товар один")
    args = _discover_args(store="shop2")
    built: list[dict] = []
    submitted: list[dict] = []

    def _build(c, store_config, store_id=""):
        built.append({"c": c, "store_id": store_id})
        # 模拟真实 build_envelope_from_discovery：把 store_id 注入 envelope extensions
        return {"draft": {"item_id": c.ozon_product_id},
                "extensions": {"store_id": store_id}}

    def _submit(envelope):
        submitted.append(envelope)
        return {"ok": True, "task_id": "T-1"}

    patches = [
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(True, "ok")),
        mock.patch("scripts.lib.ozon_discovery.collect_and_analyze",
                   return_value=[c1]),
        mock.patch("scripts.lib.ozon_discovery.apply_selection_rules",
                   return_value=[c1]),
        mock.patch("scripts.lib.ozon_discovery.match_selected"),
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value=""),
        mock.patch("scripts.lib.config_store.get_store_profile", return_value={}),
        mock.patch("scripts.lib.config_store.get_store", return_value={}),
        mock.patch("scripts.lib.config_store.get_setting", return_value=False),
        mock.patch("builtins.input", return_value="y"),
        mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                   side_effect=_build),
        mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = cli.cmd_discover(args)
    assert rc == 0
    assert built, "应调用 build_envelope_from_discovery"
    assert all(b["store_id"] == "shop2" for b in built), \
        f"build_envelope_from_discovery 应收到 store_id='shop2', got {built}"
    assert submitted and submitted[0]["extensions"].get("store_id") == "shop2", \
        f"信封 extensions.store_id 应注入 shop2, got {submitted[0].get('extensions') if submitted else None}"


def test_cmd_follow_threads_store_id():
    """cmd_follow store="shop2" → follow_sell_cloud 收到 store_id="shop2"。"""
    from scripts import cli
    args = argparse.Namespace(ozon_url="https://www.ozon.ru/product/x-1/",
                              auto_submit=False, store="shop2",
                              review=False, notify=False)
    with mock.patch("scripts.lib.config_store.preflight_check", return_value=[]), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/p"), \
         mock.patch("scripts.cloud_probe.follow_sell_cloud",
                    return_value={"success": True}) as m_fsc, \
         mock.patch("sys.stdout", new_callable=io.StringIO):
        rc = cli.cmd_follow(args)
    assert rc == 0
    assert m_fsc.call_args.kwargs.get("store_id") == "shop2", \
        f"follow_sell_cloud 应收到 store_id='shop2', got {m_fsc.call_args}"


def test_follow_cache_key_store_scoped():
    """follow 缓存 key = product_id:store_id —— 不同店铺不串缓存。"""
    from scripts import cloud_probe as cp

    url = "https://www.ozon.ru/product/avtopoilka-4767514314/"
    cached = {
        "success": True, "product_id": "4767514314", "slug": "avtopoilka",
        "images": ["http://img/ozon/1.jpg"], "title": "Автопоилка",
        "1688_matches": [{"id": "980815374096", "badge_score": 3}],
        "envelope": {"token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
                     "envelope": {"draft": {"item_id": "980815374096"},
                                  "extensions": {}}},
    }
    with mock.patch("scripts.lib.cache.cache_get", return_value=cached) as m_get, \
         mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch.object(cp, "_get_ozon_credentials",
                           return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
         mock.patch.object(cp, "submit_envelope") as m_submit:
        r = cp.follow_sell_cloud(url, auto_submit=False, store_id="shop2")
    assert r is cached
    m_get.assert_called_once_with("follow", "4767514314:shop2"), \
        f"follow 缓存 key 应含 store_id, got {m_get.call_args}"
    m_submit.assert_not_called()


# ── batch_test._resolve_credentials ─────────────────────────────────────────

def test_batch_resolve_credentials_per_store():
    """batch_test._resolve_credentials 按 store_id 解析不同 client_id/api_key。"""
    from scripts import batch_test
    data = _stores_file("shop2", {"shop1": S1, "shop2": S2})
    with mock.patch("scripts.lib.config_store._load_stores_file", return_value=data):
        cid1, key1 = batch_test._resolve_credentials("", "", "shop1")
        cid2, key2 = batch_test._resolve_credentials("", "", "shop2")
        cid_def, key_def = batch_test._resolve_credentials("", "", "")
    assert (cid1, key1) == (S1["client_id"], S1["api_key"]), \
        f"shop1 凭证, got {(cid1, key1)}"
    assert (cid2, key2) == (S2["client_id"], S2["api_key"]), \
        f"shop2 凭证, got {(cid2, key2)}"
    assert (cid_def, key_def) == (S2["client_id"], S2["api_key"]), \
        f"空 store_id → 默认 shop2, got {(cid_def, key_def)}"


def test_batch_resolve_explicit_credentials_win():
    """显式 --client-id/--api-key 优先于 stores.json（strip 后返回）。"""
    from scripts import batch_test
    cid, key = batch_test._resolve_credentials("  555  ", " kk ", "shop1")
    assert (cid, key) == ("555", "kk"), "显式凭证应 strip 后优先"


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
