"""错误报告端点测试（真实 PG，v0.69）：创建/enrichment/列表/详情/租户隔离。

⚠️ 本文件不做 ozon_product_tasks 全表清理（其它测试/实机可能在跑任务）——
只插带固定 uuid 的种子任务行并在 teardown 删除自己的行。
"""
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
os.environ["SKIP_STORE_SYNC"] = "1"

import main as main_mod  # noqa: E402

TENANT = main_mod._key_user_id("tokE2EReport")
OTHER = main_mod._key_user_id("tokE2EReportOther")
TASK_ID = str(uuid.uuid4())
HDR = {"Authorization": "Bearer tokE2EReport"}
HDR_OTHER = {"Authorization": "Bearer tokE2EReportOther"}


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    # error_reports 表可能尚未建（create_all 在应用启动时执行）——TestClient 启动即建
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM error_reports WHERE tenant_id IN (:t, :o)"),
                     {"t": TENANT, "o": OTHER})
        conn.execute(text("DELETE FROM ozon_product_tasks WHERE id=CAST(:i AS uuid)"),
                     {"i": TASK_ID})
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (id, tenant_id, status, priority, payload, timeout_seconds) "
            "VALUES (CAST(:i AS uuid), :t, 'completed', 5, '{}', 1800)"), {"i": TASK_ID, "t": TENANT})
        # 给该任务一行 result.product_id（enrichment 断言用）
        conn.execute(text("UPDATE ozon_product_tasks SET result=:r WHERE id=CAST(:i AS uuid)"),
                     {"r": '{"product_id": "123456789"}', "i": TASK_ID})
    with TestClient(main_mod.app) as c:
        yield c
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM error_reports WHERE tenant_id IN (:t, :o)"),
                     {"t": TENANT, "o": OTHER})
        conn.execute(text("DELETE FROM ozon_product_tasks WHERE id=CAST(:i AS uuid)"),
                     {"i": TASK_ID})


def _body():
    return {
        "title": "E2E 测试：泡脚包三连失败",
        "severity": "high",
        "category": "upload_failed",
        "description": "期望创建卡片，实际三种失败形态",
        "reproduction": {
            "steps": ["提交任务", "17s completed 但无商品"],
            "command": "cli.py graph --url https://detail.1688.com/offer/1.html",
            "expect": "卡片创建", "actual": "假成功",
        },
        "evidence": {"task_ids": [TASK_ID], "item_id": "623236101606",
                     "error_codes": ["VALUE_MAX_LIMIT"]},
    }


def test_create_enriches_task_snapshot(client):
    resp = client.post("/api/v1/error_reports", json=_body(), headers=HDR)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok" and data["report_id"] and data["tasks_attached"] == 1
    detail = client.get(f"/api/v1/error_reports?report_id={data['report_id']}", headers=HDR).json()
    tasks = (detail.get("auto_context") or {}).get("tasks") or []
    assert tasks and tasks[0]["task_id"] == TASK_ID and tasks[0]["product_id"] == "123456789"
    assert detail["severity"] == "high" and detail["status"] == "new"


def test_create_requires_title(client):
    resp = client.post("/api/v1/error_reports", json={"description": "no title"}, headers=HDR)
    assert resp.status_code == 422


def test_requires_bearer(client):
    resp = client.post("/api/v1/error_reports", json=_body())
    assert resp.status_code == 401


def test_list_and_tenant_isolation(client):
    lst = client.get("/api/v1/error_reports", headers=HDR).json()
    assert lst["total"] >= 1 and any("泡脚包" in r["title"] for r in lst["reports"])
    other = client.get("/api/v1/error_reports", headers=HDR_OTHER).json()
    assert other["total"] == 0 and other["reports"] == []


def test_detail_cross_tenant_404(client):
    lst = client.get("/api/v1/error_reports", headers=HDR).json()
    rid = lst["reports"][0]["report_id"]
    resp = client.get(f"/api/v1/error_reports?report_id={rid}", headers=HDR_OTHER)
    assert resp.status_code == 404


def test_flat_evidence_fields_accepted(client):
    """扁平兼容：task_ids 直接放顶层 → worker 归入 evidence 并 enrich。"""
    resp = client.post("/api/v1/error_reports",
                       json={"title": "扁平字段", "task_ids": [TASK_ID]}, headers=HDR)
    assert resp.status_code == 200 and resp.json()["tasks_attached"] == 1
