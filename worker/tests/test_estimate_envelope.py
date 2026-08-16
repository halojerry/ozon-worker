"""P2a: 独立定价器端点测试（POST /api/v1/estimate，无 draft_id 直传 envelope）。

验收门：与 /{draft_id}/estimate 同公式（estimate_from_envelope），
前端/skill 不写公式铁律不变；422 缺 draft；鉴权 401。
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from routes.estimate_routes import router_estimate
from fastapi import FastAPI

app = FastAPI()
app.include_router(router_estimate)


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    """mock _authenticate_token → 固定 tenant；rate_limiter 放行。"""
    import main as main_mod

    def _auth(token: str) -> str:
        return "tenant-A"

    monkeypatch.setattr(main_mod, "_authenticate_token", _auth)
    monkeypatch.setattr(main_mod, "rate_limiter", main_mod.RateLimiter(max_per_minute=1000))


def _env(cost=12.5, margin=None, commission=None):
    env = {
        "draft": {
            "purchase_cost": cost,
            "weight": 350,
            "dimensions": {"length": 100, "width": 80, "height": 60},
        },
        "extensions": {},
    }
    body = {"token": "sk-test", "envelope": env}
    if margin is not None:
        body["margin_rate"] = margin
    if commission is not None:
        body["commission_rate"] = commission
    return body


def test_estimate_envelope_success():
    resp = TestClient(app).post("/api/v1/estimate", json=_env())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["price"] > 0
    assert data["profit_cny"] >= 0
    assert data["currency"] in ("CNY", "RUB")


def test_estimate_margin_override():
    client = TestClient(app)
    low = client.post("/api/v1/estimate", json=_env(margin=0.1)).json()
    high = client.post("/api/v1/estimate", json=_env(margin=0.4)).json()
    assert high["price"] > low["price"]


def test_estimate_missing_draft_422():
    resp = TestClient(app).post("/api/v1/estimate", json={"token": "sk-test", "envelope": {"extensions": {}}})
    assert resp.status_code == 422
    assert "envelope.draft" in resp.text


def test_estimate_malformed_body_422():
    resp = TestClient(app).post("/api/v1/estimate", json={"token": "sk-test"})
    assert resp.status_code == 422


def test_estimate_no_token_401():
    import main as main_mod
    with patch.object(main_mod, "_authenticate_token", side_effect=__import__("fastapi").HTTPException(401, "Token is required")):
        resp = TestClient(app).post("/api/v1/estimate", json={"envelope": {"draft": {"purchase_cost": 10}}})
    assert resp.status_code == 401
