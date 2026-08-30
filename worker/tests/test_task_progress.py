"""PRD M4: 任务进度事件服务测试(真实 PG)。"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)

from services import task_progress_service as tps  # noqa: E402


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")


@pytest.fixture()
def task_id():
    tid = str(uuid.uuid4())  # ozon_product_tasks.id 是 UUID
    yield tid
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM task_progress_events WHERE task_id=:t"), {"t": tid})


def test_emit_seq_monotonic_and_list(task_id):
    s1 = tps.emit(task_id, "pricing", "compute", "progress", "正在计算")
    s2 = tps.emit(task_id, "pricing", "compute", "progress", "完成", {"price": 100})
    s3 = tps.emit(task_id, "image_generation", "", "finished", "已生成")
    assert s1 == 1 and s2 == 2 and s3 == 3
    events = tps.list_events(task_id)
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[1]["detail"] == {"price": 100}
    after = tps.list_events(task_id, after_seq=2)
    assert [e["seq"] for e in after] == [3]


def test_is_terminal(task_id):
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (id, tenant_id, status, payload, priority, retry_count, max_retries, timeout_seconds) "
            "VALUES (:id, 'tenant-x', 'completed', '{}'::jsonb, 0, 0, 3, 1800)"
        ), {"id": task_id})
    assert tps.is_terminal(task_id) is True
    assert tps.is_terminal("nonexistent-task") is False
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM ozon_product_tasks WHERE id::text=:id"), {"id": task_id})
