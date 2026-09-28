"""v0.83 批①：POST /api/v1/estimate/batch 端点契约（鉴权/限流/限额/422 形态）。

鉴权沿用 main._require_bearer（唯一入口）+ 独立限流桶 estimate_batch:{token}。
本文件只测端点「外壳」契约（真算价路径见 test_estimate_batch_parity_v083）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from routes.estimate_routes import router_estimate

app = FastAPI()
app.include_router(router_estimate)


class _AllowLimiter:
    def check(self, *_a, **_k):
        return True, 999


class _DenyLimiter:
    def check(self, *_a, **_k):
        return False, 0


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    """桩：鉴权放行 + 限流放行 + 租户固定 + 物流/佣金 mock（避免 PG/Ozon）。"""
    import main as main_mod
    from services import estimate_service

    monkeypatch.setattr(main_mod, "_require_bearer", lambda request: "tenant-A")
    monkeypatch.setattr(main_mod, "rate_limiter", _AllowLimiter())
    monkeypatch.setattr(
        "services.tenant_service.resolve_tenant", lambda token: "tenant-A", raising=False
    )
    monkeypatch.setattr(
        estimate_service, "query_logistics_cost",
        lambda *a, **k: (10.0, "mock_channel", {}),
    )
    monkeypatch.setattr(estimate_service, "get_category_commission", lambda *a, **k: None)
    yield


def _item(**over):
    base = {
        "purchase_cost": 20.0,
        "weight_g": 227,
        "dims_mm": {"length": 120, "width": 80, "height": 60},
        "currency_code": "CNY",
    }
    base.update(over)
    return base


def test_batch_success_shape():
    resp = TestClient(app).post("/api/v1/estimate/batch", json={"items": [_item()]})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data.keys()) == {"items", "failed"}
    assert data["failed"] == []
    row = data["items"][0]
    assert row["index"] == 0 and row["ok"] is True
    assert row["estimate_source"] == "worker"
    assert row["logistics_source"] == "default_rets"
    for k in ("price", "old_price", "profit_cny", "profit_rate", "commission_rate", "commission_source"):
        assert k in row, f"缺字段 {k}"
    assert isinstance(row["marks"], dict)


def test_batch_over_limit_422():
    items = [_item() for _ in range(51)]
    resp = TestClient(app).post("/api/v1/estimate/batch", json={"items": items})
    assert resp.status_code == 422
    assert "50" in resp.text


def test_batch_empty_items_422():
    resp = TestClient(app).post("/api/v1/estimate/batch", json={"items": []})
    assert resp.status_code == 422


def test_batch_variants_rejected_422():
    """schema extra=forbid：收到 variants 键 → 422（不支持多 SKU）。"""
    resp = TestClient(app).post(
        "/api/v1/estimate/batch", json={"items": [_item(variants=[{"price": 9}])]}
    )
    assert resp.status_code == 422


def test_batch_malformed_json_422():
    resp = TestClient(app).post(
        "/api/v1/estimate/batch",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_batch_no_bearer_401(monkeypatch):
    import main as main_mod

    def _need(request):
        raise HTTPException(status_code=401, detail="Token is required")

    monkeypatch.setattr(main_mod, "_require_bearer", _need)
    resp = TestClient(app).post("/api/v1/estimate/batch", json={"items": [_item()]})
    assert resp.status_code == 401


def test_batch_rate_limited_429(monkeypatch):
    import main as main_mod

    monkeypatch.setattr(main_mod, "rate_limiter", _DenyLimiter())
    resp = TestClient(app).post("/api/v1/estimate/batch", json={"items": [_item()]})
    assert resp.status_code == 429
