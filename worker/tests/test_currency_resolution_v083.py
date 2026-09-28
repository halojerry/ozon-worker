# -*- coding: utf-8 -*-
"""v0.83 gate B2: pricing_node 币种解析信任序（信封 → 本地凭证 → Ozon API → 显式失败）。

背景（gate v083 #4 实锤）：graph 信封不带 currency_code、Ozon /v1/seller/info 查询
超时 → 此前**静默回落默认 RUB** → CNY 合约店发 RUB 价 → Ozon 拒
currency_differs_from_contract。本用例锁：

1. envelope ``extensions.currency_code`` 在场 → 用它（source=envelope）；
2. 否则本地凭证行 currency（纯本地读）→ 用它（source=credential）；
3. 否则 Ozon API / auth_node 透传 → 用它（source=ozon_api）；
4. 全败 → 显式 failed ``CURRENCY_UNRESOLVED``（**绝不静默 RUB**）。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_currency_resolution_v083.py -q
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes import pricing_node as pn
from utils import currency_resolver


class _DummyRuntime:
    class _DummyContext:
        pass

    context = _DummyContext()


def _make_state(draft=None, extensions=None, currency="", user_id="tenant-1"):
    return SimpleNamespace(
        draft=draft
        or {
            "cost_cny": 5.5,
            "purchase_cost": 5.5,
            "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        },
        extensions=extensions if extensions is not None else {},
        supabase_url="http://supabase.local",
        supabase_key="k",
        currency_code=currency,
        ozon_client_id="1",
        ozon_api_key="k",
        description_category_id="17028830",
        task_id="t",
        tenant_id=user_id,
        user_id=user_id,
        token="sk-test",
        envelope={},
        variants=[],
    )


def _patch_common(monkeypatch, credential="", ozon_raises=True):
    """mock 物流/佣金/汇率/凭证/ozon_post（纯内存，无 PG/HTTP）。"""
    from utils import logistics_quote

    monkeypatch.setattr(
        logistics_quote, "query_logistics_cost",
        lambda *a, **k: (10.0, "mock_channel", {}),
    )
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config",
        lambda *a, **k: ("RETS", "Standard"),
    )
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None)
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: 12.0)

    cred_calls: list = []

    def _fake_cred(tenant_id, client_id, session=None):
        cred_calls.append((tenant_id, client_id))
        return credential

    monkeypatch.setattr(currency_resolver, "resolve_credential_currency", _fake_cred)

    def _fake_ozon(*a, **k):
        if ozon_raises:
            raise TimeoutError("SSL: UNEXPECTED_EOF_WHILE_READING")
        return {"company": {"currency": "RUB"}}

    monkeypatch.setattr(pn, "ozon_post", _fake_ozon)
    return cred_calls


def test_envelope_currency_wins(monkeypatch):
    """① 信封 extensions.currency_code 在场 → 用它（source=envelope），不查凭证/Ozon。"""
    cred_calls = _patch_common(monkeypatch, credential="")
    state = _make_state(extensions={"currency_code": "CNY"}, currency="")
    out = pn.pricing_node(state, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert out.pricing_info["currency_unit"] == "CNY"
    assert out.pricing_info["currency_source"] == "envelope"
    assert cred_calls == [], "信封命中不应再查本地凭证"


def test_credential_currency_used_when_ozon_timeout(monkeypatch):
    """② 无信封 + 本地凭证 currency=CNY + Ozon 超时 → CNY（source=credential）。"""
    cred_calls = _patch_common(monkeypatch, credential="CNY")
    state = _make_state(currency="")
    out = pn.pricing_node(state, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert out.pricing_info["currency_unit"] == "CNY"
    assert out.pricing_info["currency_source"] == "credential"
    assert cred_calls == [("tenant-1", "1")], "应按 ozon_client_id 本地读凭证币种"


def test_ozon_api_result_used_last(monkeypatch):
    """③ 无信封/凭证 + Ozon API 查询成功 → 用它（source=ozon_api）。"""
    _patch_common(monkeypatch, credential="", ozon_raises=False)
    state = _make_state(currency="")
    out = pn.pricing_node(state, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert out.pricing_info["currency_unit"] == "RUB"
    assert out.pricing_info["currency_source"] == "ozon_api"


def test_auth_node_currency_short_circuits_ozon_query(monkeypatch):
    """auth_node 已透传 state.currency_code（Ozon 查询成功）→ 直接用它（source=ozon_api），不再查 Ozon。"""
    calls = {"ozon": 0}

    def _fail_ozon(*a, **k):
        calls["ozon"] += 1
        raise AssertionError("不应重复查 Ozon")

    _patch_common(monkeypatch, credential="")
    monkeypatch.setattr(pn, "ozon_post", _fail_ozon)
    state = _make_state(currency="CNY")
    out = pn.pricing_node(state, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert out.pricing_info["currency_unit"] == "CNY"
    assert out.pricing_info["currency_source"] == "ozon_api"
    assert calls["ozon"] == 0


def test_all_sources_fail_returns_currency_unresolved(monkeypatch):
    """④ 信封/凭证/Ozon 全败 → 显式 CURRENCY_UNRESOLVED failed，绝不静默 RUB。"""
    _patch_common(monkeypatch, credential="", ozon_raises=True)
    state = _make_state(currency="")
    out = pn.pricing_node(state, None, _DummyRuntime())
    assert out.error_code == "CURRENCY_UNRESOLVED"
    assert out.failed_stage == "pricing"
    assert out.price == ""
    assert out.pricing_info.get("currency_source") == "unresolved"
    assert "币种无法确定" in out.error_message


def test_no_tenant_skips_credential_lookup(monkeypatch):
    """tenant 为空（mock/老调用形态）→ 跳过本地凭证读，行为不回归（不误查 PG）。"""
    cred_calls = _patch_common(monkeypatch, credential="CNY")
    state = _make_state(currency="RUB", user_id="")
    out = pn.pricing_node(state, None, _DummyRuntime())
    assert cred_calls == [], "无 tenant 不应查凭证"
    assert out.pricing_info["currency_source"] == "ozon_api"
    assert out.pricing_info["currency_unit"] == "RUB"


def test_normalize_currency_whitelist():
    """币种白名单归一：strip/upper/未知值丢弃。"""
    assert currency_resolver.normalize_currency(" cny ") == "CNY"
    assert currency_resolver.normalize_currency("rub") == "RUB"
    assert currency_resolver.normalize_currency("BTC") == ""
    assert currency_resolver.normalize_currency(None) == ""
    assert currency_resolver.normalize_currency(123) == ""


def test_resolve_credential_currency_local_read(monkeypatch):
    """本地凭证读：命中返回归一币种；无行/异常返回 ""；tenant 空不查。"""
    class _Row:
        def __init__(self, v):
            self._v = v

        def __getitem__(self, i):
            return self._v

    class _Session:
        def __init__(self, row):
            self._row = row
            self.closed = False

        def execute(self, *a, **k):
            return self

        def fetchone(self):
            return self._row

        def close(self):
            self.closed = True

    s = _Session(_Row("cny"))
    assert currency_resolver.resolve_credential_currency("t", "c", session=s) == "CNY"
    assert currency_resolver.resolve_credential_currency("t", "c", session=_Session(None)) == ""
    assert currency_resolver.resolve_credential_currency("", "c", session=s) == ""

    class _Boom(_Session):
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert currency_resolver.resolve_credential_currency("t", "c", session=_Boom(None)) == ""
