# -*- coding: utf-8 -*-
"""v0.65: promo_price → min_price 上送测试（TDD）。

链路：
  pricing_node 三档默认激活 → promo_price 产出 → ozon_status_node 在 /v1/product/import/info
  确认新建商品 imported（真实 product_id/offer_id 到手）后，经 ozon_upload_node 的
  try_set_min_price_floor 接线 → ozon_client.update_min_price_floor → POST /v1/product/import/prices。

契约（Ozon /v1/product/import/prices）：
  - body {"prices":[{offer_id, product_id, price, old_price, min_price}]}；
  - min_price 受 `min_auto_price_too_small` 约束不得低于售价 50%（防御抬升）；
  - `price_less_than_min_auto_price` = min_price > 售价会拒（上限收在售价内）；
  - `old_price_less_than_price` = 划线价须 ≥ 售价；
  - `NOT_FOUND_ERROR` = 商品不存在（故须在 import 成功、真实 product_id 到手后才调）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_min_price_floor.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from graphs.nodes import ozon_upload_node as oun
from utils import ozon_client as oc_mod


# ── update_min_price_floor 纯函数 ──
def _call_floor(monkeypatch, *args, **kwargs):
    """mock ozon_post 捕获请求后调 update_min_price_floor，返回 (response, captured)。"""
    captured = {}

    def _fake_ozon_post(client_id, api_key, endpoint, body, **kw):
        captured["client_id"] = client_id
        captured["api_key"] = api_key
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"result": [{"product_id": body["prices"][0]["product_id"], "updated": True}]}

    monkeypatch.setattr(oc_mod, "ozon_post", _fake_ozon_post)
    resp = oc_mod.update_min_price_floor(*args, **kwargs)
    return resp, captured


def test_update_min_price_floor_body(monkeypatch):
    """断言调端点 /v1/product/import/prices，body.prices[0] 含 offer_id/product_id/price/old_price/min_price。"""
    resp, cap = _call_floor(
        monkeypatch,
        client_id="100",
        api_key="key",
        offer_id="sku_123",
        product_id=1386,
        price="1448",
        old_price="1800",
        min_price="800",
    )
    assert cap["client_id"] == "100"
    assert cap["api_key"] == "key"
    assert cap["endpoint"] == "/v1/product/import/prices", f"端点应=import/prices，实际 {cap['endpoint']}"
    p0 = cap["body"]["prices"][0]
    assert p0["offer_id"] == "sku_123"
    assert p0["product_id"] == 1386
    assert p0["price"] == "1448"
    assert p0["old_price"] == "1800"
    assert p0["min_price"] == "800"


def test_min_price_floor_not_below_half_price(monkeypatch):
    """min_price 传太小（< price×50%）→ 抬到 ≥ceil(price×0.5)。"""
    _, cap = _call_floor(
        monkeypatch,
        client_id="100", api_key="key",
        offer_id="o", product_id=1,
        price="1000", old_price="1200", min_price="100",
    )
    p0 = cap["body"]["prices"][0]
    assert int(p0["min_price"]) >= 500, f"min_price 应≥500，实际 {p0['min_price']}"
    assert p0["min_price"] == "500", f"应抬到 ceil(1000×0.5)=500，实际 {p0['min_price']}"


def test_min_price_floor_capped_at_price(monkeypatch):
    """极端 margin 下 min_price > price → 收在 price（防 price_less_than_min_auto_price 拒）。"""
    _, cap = _call_floor(
        monkeypatch,
        client_id="100", api_key="key",
        offer_id="o", product_id=1,
        price="1000", old_price="1000", min_price="1200",
    )
    p0 = cap["body"]["prices"][0]
    assert p0["min_price"] == "1000", f"min_price 不应超过售价，实际 {p0['min_price']}"


def test_old_price_defaulted_to_price(monkeypatch):
    """old_price 缺省 → 用 price（Ozon 要求划线价 ≥ 售价）。"""
    _, cap = _call_floor(
        monkeypatch,
        client_id="100", api_key="key",
        offer_id="o", product_id=1,
        price="1000", old_price=None, min_price=None,
    )
    p0 = cap["body"]["prices"][0]
    assert p0["old_price"] == "1000"
    assert p0["min_price"] == "500"  # 无 min_price → 至少售价 50%


# ── ozon_upload_node.try_set_min_price_floor 接线（吞异常 + 守卫） ──
def test_caller_survives_api_error(monkeypatch):
    """ozon_post 抛异常 → try_set_min_price_floor 不崩、返回 False。"""
    def _boom(*a, **k):
        raise RuntimeError("mock ozon_post 500")

    monkeypatch.setattr(oc_mod, "update_min_price_floor", _boom)
    # try_set_min_price_floor 内 `from utils.ozon_client import update_min_price_floor` 会拿到 patched
    ret = oun.try_set_min_price_floor(
        ozon_client_id="100", ozon_api_key="key",
        offer_id="sku_1", product_id="1386",
        price=1448, old_price=1800, promo_price=800,
    )
    assert ret is False, "API 异常应返回 False 而非抛错"


def test_caller_passes_through_on_success(monkeypatch):
    """成功路径：update_min_price_floor 收到正确参数并返回 True。"""
    captured = {}

    def _fake(client_id, api_key, offer_id, product_id, price, old_price, min_price, **kw):
        captured.update(client_id=client_id, api_key=api_key, offer_id=offer_id,
                        product_id=product_id, price=price, old_price=old_price,
                        min_price=min_price)
        return {"ok": True}

    monkeypatch.setattr(oc_mod, "update_min_price_floor", _fake)
    ret = oun.try_set_min_price_floor(
        ozon_client_id="100", ozon_api_key="key",
        offer_id="sku_1", product_id="1386",
        price=1448, old_price=1800, promo_price=800,
    )
    assert ret is True, "成功应返回 True"
    assert captured["offer_id"] == "sku_1"
    assert captured["product_id"] == 1386
    assert captured["min_price"] == 800


def test_caller_skips_when_no_promo(monkeypatch):
    """无 promo_price（非三档）→ 跳过不调 API，返回 False。"""
    called = []

    def _fake(*a, **k):
        called.append(a)
        return {"ok": True}

    monkeypatch.setattr(oc_mod, "update_min_price_floor", _fake)
    ret = oun.try_set_min_price_floor(
        ozon_client_id="100", ozon_api_key="key",
        offer_id="sku_1", product_id="1386",
        price=1448, old_price=1800, promo_price=None,
    )
    assert ret is False
    assert called == [], "无 promo_price 时不得调 API"


def test_caller_skips_without_real_product_id(monkeypatch):
    """缺真实 product_id（非数字/空）→ 跳过不调 API（import/prices 会 NOT_FOUND）。"""
    called = []

    def _fake(*a, **k):
        called.append(a)
        return {"ok": True}

    monkeypatch.setattr(oc_mod, "update_min_price_floor", _fake)
    ret = oun.try_set_min_price_floor(
        ozon_client_id="100", ozon_api_key="key",
        offer_id="sku_1", product_id="abc_task_id_172549793",  # 非真实数字 product_id
        price=1448, old_price=1800, promo_price=800,
    )
    assert ret is False
    assert called == [], "缺真实 product_id 时不得调 API（NOT_FOUND_ERROR）"
