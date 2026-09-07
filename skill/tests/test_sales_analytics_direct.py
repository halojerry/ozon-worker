#!/usr/bin/env python3
"""fetch_sales_analytics_direct 静默直调回归（漏斗 v2 收尾 · follow Step 2.5）。

背景：follow 竞品运营指标此前只走 fetch_sales_analytics（CDP 导航 seller 页）。
端点与已直调化的畅销榜是同一个（/api/site/seller-analytics/what_to_sell/data/v3），
补直调变体：cookie 直调优先 → 失败降级 CDP；缓存与 CDP 变体共用 key。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_sales_analytics_direct.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402

_FAKE_ITEM = {
    "result": {"items": [{
        "id": 1, "sku": 12345, "weight": 350,
        "length": 100, "width": 80, "height": 60,
    }]},
}


def _isolate_cache(monkeypatch):
    store: dict = {}
    monkeypatch.setattr("scripts.lib.cache.cache_get",
                        lambda ns, key: store.get(f"{ns}|{key}"))
    monkeypatch.setattr("scripts.lib.cache.cache_set",
                        lambda ns, key, data, ttl=86400: store.update({f"{ns}|{key}": data}))
    return store


def test_direct_success_parses_and_caches(monkeypatch):
    store = _isolate_cache(monkeypatch)
    posts: list[tuple[str, dict]] = []

    def _fake_post(path, body, cookies, timeout=20):
        posts.append((path, body))
        return dict(_FAKE_ITEM), True

    monkeypatch.setattr(osa, "_seller_direct_post", _fake_post)
    out = osa.fetch_sales_analytics_direct({"sc_company_id": "42"}, ["12345"])
    assert "12345" in out and out["12345"], "应有解析出的指标"
    assert len(posts) == 1
    path, body = posts[0]
    assert path == "/api/site/seller-analytics/what_to_sell/data/v3"
    assert body["filter"]["sku"] == "12345"
    assert body["filter"]["period"] == "monthly"
    assert any(k.startswith("seller_analytics|") for k in store), "成功结果应写缓存"


def test_direct_cache_hit_skips_request(monkeypatch):
    store = _isolate_cache(monkeypatch)
    monkeypatch.setattr(osa, "_seller_direct_post",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("缓存命中不应请求")))
    store["seller_analytics|12345|zh-Hans"] = {"12345": {"weight_g": 350}}
    out = osa.fetch_sales_analytics_direct({"sc_company_id": "42"}, ["12345"])
    assert out == {"12345": {"weight_g": 350}}


def test_direct_failure_returns_empty_no_cache(monkeypatch):
    store = _isolate_cache(monkeypatch)
    monkeypatch.setattr(osa, "_seller_direct_post", lambda *a, **k: ({}, False))
    out = osa.fetch_sales_analytics_direct({}, ["12345"])
    assert out == {}
    assert not store, "失败不缓存（下次可重试）"


def test_direct_missing_company_id_no_request(monkeypatch):
    _isolate_cache(monkeypatch)
    # _seller_direct_post 自身守卫 sc_company_id：缺失即短路 ({}, False)
    data, ok = osa._seller_direct_post("/x", {}, {})
    assert ok is False and data == {}


def test_follow_step25_wires_direct_before_cdp():
    """cloud_probe Step 2.5 真实接线：cookie 直调在 CDP 兜底之前（源码次序绊线）。"""
    import inspect
    from scripts import cloud_probe
    src = inspect.getsource(cloud_probe)
    i_direct = src.find("_metrics_map = fetch_sales_analytics_direct(")
    i_cdp = src.find("_metrics_map = fetch_sales_analytics(shared_cdp")
    assert i_direct > 0, "Step 2.5 应接线 fetch_sales_analytics_direct"
    assert 0 < i_direct < i_cdp, "直调必须先于 CDP 兜底"
    assert "_fetch_seller_session_cookies" in src, "直调前应取会话 cookie"
