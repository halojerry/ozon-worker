"""登录余额卡真实化测试:GET /api/v1/mxou/balance(鉴权 + MXOU 查询降级)。"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)
os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"
os.environ["SKIP_STORE_SYNC"] = "1"

import main as main_mod  # noqa: E402

TENANT = main_mod._key_user_id("tokBal")


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM ozon_product_tasks WHERE status IN ('pending','running') "
            "AND created_at < NOW() - INTERVAL '30 seconds'"
        ))
    with TestClient(main_mod.app) as c:
        yield c


def test_balance_ok(client):
    with patch("utils.mxou_api.get_mxou_balance", return_value=1234.5):
        resp = client.get("/api/v1/mxou/balance", headers={"Authorization": "Bearer tokBal"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance"] == 1234.5
    assert data["currency"] == "CNY"
    assert data["source"] == "mxou"


def test_balance_unavailable_fail_open(client):
    with patch("utils.mxou_api.get_mxou_balance", return_value=None):
        resp = client.get("/api/v1/mxou/balance", headers={"Authorization": "Bearer tokBal"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance"] is None
    assert data["source"] == "unavailable"


def test_balance_requires_auth(client):
    resp = client.get("/api/v1/mxou/balance")
    assert resp.status_code == 401
