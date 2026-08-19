"""learning_record 佣金回填单测 — 任务 1.4（TDD）。

覆盖：approved 成功路径 prices 回填（parse → 选段 → upsert）/
product_id 缺失跳过（无 API 调用、无 upsert）/ API 异常非致命（不传播）。
mock ozon_post 与 upsert_category_commission，不连真实 PG / Ozon API。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.learning_record_node import _backfill_category_commission


def _state(**kw):
    return SimpleNamespace(**kw)


def test_backfill_parses_and_upserts(monkeypatch):
    captured = {}

    def fake_ozon_post(client_id, api_key, endpoint, body, timeout=60, language="ZH_HANS"):
        captured["endpoint"] = endpoint
        captured["body"] = body
        assert client_id == "4718259"
        assert api_key == "sk-api-key"
        return {"items": [{"commissions": {"sales_percent_rfbs": 15.0}}]}

    def fake_upsert(description_category_id, source="what_to_sell", session=None, **segments):
        captured["dc"] = description_category_id
        captured["source"] = source
        captured["segments"] = segments

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)
    monkeypatch.setattr("utils.commission_resolver.upsert_category_commission", fake_upsert)

    _backfill_category_commission(_state(
        product_id="123456",
        description_category_id="17028929",
        ozon_client_id="4718259",
        ozon_api_key="sk-api-key",
        pricing_info={"currency_code": "RUB", "price": 3000},
    ))

    assert captured["endpoint"] == "/v5/product/info/prices"
    assert captured["body"] == {"filter": {"product_id": ["123456"]}, "limit": 1}
    assert captured["dc"] == 17028929
    assert captured["source"] == "prices_api"
    # 3000 RUB → leq_5000 段；15% → 百分比 15.0
    assert captured["segments"] == {"fbs_leq_5000": 15.0}


def test_backfill_skips_missing_guard_fields(monkeypatch):
    called = {"api": 0, "upsert": 0}

    def fake_ozon_post(*a, **kw):
        called["api"] += 1
        return {}

    def fake_upsert(*a, **kw):
        called["upsert"] += 1

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)
    monkeypatch.setattr("utils.commission_resolver.upsert_category_commission", fake_upsert)

    # 无 product_id
    _backfill_category_commission(_state(
        product_id=None,
        description_category_id="17028929",
        ozon_client_id="4718259",
        ozon_api_key="sk-api-key",
    ))
    # 无 description_category_id
    _backfill_category_commission(_state(
        product_id="123456",
        description_category_id=None,
        ozon_client_id="4718259",
        ozon_api_key="sk-api-key",
    ))
    # 无凭证（state 无 ozon_client_id/ozon_api_key → 降级跳过）
    _backfill_category_commission(_state(
        product_id="123456",
        description_category_id="17028929",
    ))
    assert called == {"api": 0, "upsert": 0}


def test_backfill_non_fatal_on_api_error(monkeypatch):
    def fake_ozon_post(*a, **kw):
        raise RuntimeError("api down")

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)

    # 不应抛异常（学习路径不被回填阻断）
    _backfill_category_commission(_state(
        product_id="123456",
        description_category_id="17028929",
        ozon_client_id="4718259",
        ozon_api_key="sk-api-key",
    ))


def test_backfill_uses_neutral_band_when_price_unknown(monkeypatch):
    captured = {}

    def fake_ozon_post(*a, **kw):
        return {"items": [{"commissions": {"sales_percent_rfbs": 15.0}}]}

    def fake_upsert(description_category_id, source="what_to_sell", session=None, **segments):
        captured.update(dc=description_category_id, source=source, segments=segments)

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)
    monkeypatch.setattr("utils.commission_resolver.upsert_category_commission", fake_upsert)

    # CNY 店铺（非 RUB 售价，不可直接套 RUB 分段）→ 中性段 fbs_leq_5000
    _backfill_category_commission(_state(
        product_id="123456",
        description_category_id="17028929",
        ozon_client_id="4718259",
        ozon_api_key="sk-api-key",
        pricing_info={"currency_code": "CNY", "price": 100},
    ))
    assert captured["source"] == "prices_api"
    assert captured["segments"] == {"fbs_leq_5000": 15.0}
