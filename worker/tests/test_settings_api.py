"""用户设置端点测试(真实 PG):GET 默认值 / PUT 合并 / 校验。"""
import os
import sys
import uuid
from pathlib import Path

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
os.environ["SKIP_STORE_SYNC"] = "1"  # 本文件不测同步,避免调度器捡到存量店阻塞 shutdown

import main as main_mod  # noqa: E402

TENANT = main_mod._key_user_id("tokSet")


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM user_settings WHERE tenant_id=:t"), {"t": TENANT})
        # 清掉历史残留 pending/running 任务(防 task_processor 启动即执行 → shutdown 排空 5min)
        conn.execute(text(
            "DELETE FROM ozon_product_tasks WHERE status IN ('pending','running') "
            "AND created_at < NOW() - INTERVAL '30 seconds'"
        ))
    with TestClient(main_mod.app) as c:
        yield c
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM user_settings WHERE tenant_id=:t"), {"t": TENANT})


def test_get_defaults(client):
    resp = client.get("/api/v1/settings", headers={"Authorization": "Bearer tokSet"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["fx_buffer_percent"] == 3.5
    assert data["low_stock_threshold"] == 10
    assert data["auto_review_enabled"] is True
    assert data["daily_report_enabled"] is False


def test_put_partial_merge(client):
    resp = client.put("/api/v1/settings", json={
        "token": "tokSet",
        "fx_buffer_percent": 5.0,
        "low_stock_threshold": 20,
        "order_status_notify": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["fx_buffer_percent"] == 5.0
    assert data["low_stock_threshold"] == 20
    assert data["order_status_notify"] is False
    assert data["auto_review_score"] == 85  # 未传保持默认


def test_put_unknown_key_rejected(client):
    resp = client.put("/api/v1/settings", json={
        "token": "tokSet",
        "hacked_key": 1,
    })
    assert resp.status_code == 400
    assert "未知设置项" in resp.json()["detail"]


def test_put_out_of_range_rejected(client):
    resp = client.put("/api/v1/settings", json={
        "token": "tokSet",
        "auto_review_score": 10,
    })
    assert resp.status_code == 400
