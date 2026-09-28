#!/usr/bin/env python3
"""v0.83 gate 批①：预估 extensions 带店铺 ozon_client_id（预估↔卡价物流同源）。

背景：skill 提交前预估走 worker ``POST /api/v1/estimate``（envelope 形态），此前不带
任何凭证线索 → worker 恒走默认 RETS 物流（钥匙盒例 ¥7.52），而 pricing_node 用店铺
真实 3PL（¥6.76）→ 预估↔卡价 +5.9%（验收线 ±3%）。修复：``_store_pricing_extensions``
把店铺 ``client_id`` 作为 ``extensions.ozon_client_id`` 带上，worker 按 (tenant, client)
反查凭证解密探测真实 3PL。

本文件锁定 skill→worker 契约（纯 mock；worker 侧解析见 tests/test_estimate_batch_parity_v083）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_estimate_ext_client_id_v083.py -q
"""
from __future__ import annotations

import io
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402

_EST_PATCH = "scripts.lib.estimate_client.estimate_envelope"

_DRAFT = {
    "item_id": "123", "title": "x",
    "purchase_cost": 10.0, "weight": 1000,
    "dimensions": {"length": 100, "width": 100, "height": 50},
}


def _est_resp(price=28.12, logistics=6.76, profit=13.12, rate=0.467, currency="CNY"):
    return {
        "price": price,
        "logistics_cost_cny": logistics,
        "profit_cny": profit,
        "profit_rate": rate,
        "currency": currency,
        "logistics_source": "store",
    }


def test_store_pricing_extensions_carries_client_id():
    """店铺 client_id → extensions.ozon_client_id（定价配置键照旧透传）。"""
    with mock.patch("scripts.lib.config_store.get_store_profile", return_value={"margin_rate": 1.5}), \
            mock.patch("scripts.lib.config_store.get_store",
                       return_value={"client_id": "4718259", "api_key": "k"}):
        ext = cli._store_pricing_extensions("主店铺")
    assert ext["ozon_client_id"] == "4718259"
    assert ext["margin_rate"] == 1.5


def test_store_pricing_extensions_no_store_omits_client_id():
    """未配置店铺/无 client_id → 省略键（worker 走 default_rets，行为不变）。"""
    with mock.patch("scripts.lib.config_store.get_store_profile", return_value={}), \
            mock.patch("scripts.lib.config_store.get_store", return_value=None):
        ext = cli._store_pricing_extensions("")
    assert "ozon_client_id" not in ext


def test_estimate_and_print_envelope_carries_ozon_client_id():
    """_estimate_and_print 的 envelope.extensions 必须带 ozon_client_id（gate 根因修复点）。"""
    captured: dict = {}

    def _fake(env, *a, **k):
        captured["env"] = env
        return _est_resp()

    with mock.patch(_EST_PATCH, side_effect=_fake), \
            mock.patch("scripts.lib.config_store.get_store_profile", return_value={}), \
            mock.patch("scripts.lib.config_store.get_store",
                       return_value={"client_id": "4718259"}), \
            mock.patch("sys.stdout", io.StringIO()):
        est = cli._estimate_and_print(dict(_DRAFT), store="主店铺")

    assert est is not None and est["estimate_source"] == "worker"
    assert captured["env"]["extensions"]["ozon_client_id"] == "4718259"
    assert est["estimated_logistics_cny"] == 6.76
