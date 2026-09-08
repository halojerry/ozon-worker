"""v0.70 取证只读通道 — GET /api/v1/forensics/task/{id} 端点测试（真实 PG）。

四路聚合断言：task / listing_result / category_match_log / attr_match_log；
租户隔离（跨租户 404）；不存在 404；无 Bearer 401。
⚠️ 不做 ozon_product_tasks 全表清理——只插固定 uuid 种子行，teardown 删自己行。
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
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)
os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"
os.environ["SKIP_STORE_SYNC"] = "1"

import main as main_mod  # noqa: E402

TENANT = main_mod._key_user_id("tokForensic")
OTHER = main_mod._key_user_id("tokForensicOther")
TASK_ID = str(uuid.uuid4())
HDR = {"Authorization": "Bearer tokForensic"}
HDR_OTHER = {"Authorization": "Bearer tokForensicOther"}
URL = f"/api/v1/forensics/task/{TASK_ID}"


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM ozon_product_tasks WHERE id=CAST(:i AS uuid)"), {"i": TASK_ID})
        conn.execute(text("DELETE FROM listing_result_log WHERE task_db_id=:i"), {"i": TASK_ID})
        conn.execute(text("DELETE FROM category_match_log WHERE task_id=:i"), {"i": TASK_ID})
        conn.execute(text("DELETE FROM attr_match_log WHERE task_id=:i"), {"i": TASK_ID})
        # 任务行（本租户）
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (id, tenant_id, status, priority, payload, timeout_seconds)"
            " VALUES (CAST(:i AS uuid), :t, 'completed', 5, :p, 1800)"),
            {"i": TASK_ID, "t": TENANT, "p": '{"envelope": {"draft": {"title": "取证测试标题"}}}'})
        conn.execute(text(
            "UPDATE ozon_product_tasks SET result=:r WHERE id=CAST(:i AS uuid)"),
            {"r": '{"product_id": "987654321"}', "i": TASK_ID})
        # 留存行
        conn.execute(text(
            "INSERT INTO listing_result_log (task_db_id, tenant_id, source_item_id,"
            " source_title_cn, ozon_product_id, final_status, match_layer, match_confidence, weight_g)"
            " VALUES (:i, :t, '623236101606', '取证测试标题', '987654321', 'approved', 'Skill', 0.95, 950)"),
            {"i": TASK_ID, "t": TENANT})
        # 类目审计行
        conn.execute(text(
            "INSERT INTO category_match_log (task_id, source_title, matched_description_category_id,"
            " matched_type_id, matched_path_zh, match_layer, confidence)"
            " VALUES (:i, '取证测试标题', 98765432, 12345678, 'Дом и сад > Хранение', 'Skill', 1.0)"),
            {"i": TASK_ID})
        # 属性审计行
        conn.execute(text(
            "INSERT INTO attr_match_log (task_id, attr_id, attr_name, source_value, status,"
            " match_layer, confidence, dictionary_value_id, should_fill)"
            " VALUES (:i, 8229, '类型', '收纳盒', 'matched', 'dict_exact', 1.0, 9710, false)"),
            {"i": TASK_ID})
    with TestClient(main_mod.app) as c:
        yield c
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM ozon_product_tasks WHERE id=CAST(:i AS uuid)"), {"i": TASK_ID})
        conn.execute(text("DELETE FROM listing_result_log WHERE task_db_id=:i"), {"i": TASK_ID})
        conn.execute(text("DELETE FROM category_match_log WHERE task_id=:i"), {"i": TASK_ID})
        conn.execute(text("DELETE FROM attr_match_log WHERE task_id=:i"), {"i": TASK_ID})


def test_requires_bearer(client):
    assert client.get(URL).status_code == 401


def test_unknown_task_404(client):
    r = client.get("/api/v1/forensics/task/00000000-0000-0000-0000-000000000000",
                   headers=HDR)
    assert r.status_code == 404


def test_cross_tenant_404(client):
    r = client.get(URL, headers=HDR_OTHER)
    assert r.status_code == 404, "跨租户取证必须 404（等价不存在）"


def test_full_aggregate(client):
    r = client.get(URL, headers=HDR)
    assert r.status_code == 200
    body = r.json()
    # 任务快照
    assert body["task"]["task_id"] == TASK_ID
    assert body["task"]["status"] == "completed"
    assert body["task"]["product_id"] == "987654321"
    assert body["task"]["title"] == "取证测试标题"
    # 留存表
    lr = body["listing_result"]
    assert lr is not None
    assert lr["source_item_id"] == "623236101606"
    assert lr["final_status"] == "approved"
    assert lr["match_layer"] == "Skill"
    assert lr["weight_g"] == 950
    # 类目审计
    assert len(body["category_match_log"]) == 1
    m = body["category_match_log"][0]
    assert m["match_layer"] == "Skill"
    assert m["matched_dc"] == 98765432
    assert m["upload_success"] is None  # 未回填字段原样 None
    # 属性审计
    assert len(body["attr_match_log"]) == 1
    a = body["attr_match_log"][0]
    assert a["attr_id"] == 8229
    assert a["dictionary_value_id"] == 9710
    assert a["should_fill"] is False
