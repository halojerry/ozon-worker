#!/usr/bin/env python3
"""F-E01 category_mapping cid 规范化归并原子化单测（P1 波，2026-09-09 审计）。

审计发现（findings.md，中）：归并旁路在 Python 内存 `_canon.success_count += 1`
后 commit（local_db_manager add_category_mapping cid 规范化分支）——check-then-act
非原子，并发两个同 (cid,dc,tp) approved 任务都读到同一快照 → 两次计一次
（学习累计丢失，L0 转权威被稀释）；`source_category_leaf` 后写赢可倒刷措辞。

修复契约（本文件锁定）：
  归并改为单条原子 UPDATE 表达式（success_count = coalesce(success_count,0)+1
  在 SQL 侧自增），并发 N 次归并 → 计数精确 +N。

运行（需本地 PG 5433，不可达自动 skip）：
    cd worker && PGDATABASE_URL=... PYTHONPATH=src ../skill/.venv314/bin/python \
        -m pytest tests/test_category_mapping_merge_atomic.py -q
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)
_DC = 987654  # 测试标记 dc（隔离真实数据）


def _pg_available() -> bool:
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="本地 PG 不可达")


@pytest.fixture()
def engine():
    eng = create_engine(DB_URL)
    yield eng
    with eng.connect() as conn:
        conn.execute(text(
            "DELETE FROM category_mapping WHERE description_category_id = :dc"),
            {"dc": _DC})
        conn.commit()


def _mk_canon(eng, leaf: str, success_count: int = 5) -> None:
    with eng.connect() as conn:
        conn.execute(text("""
            INSERT INTO category_mapping
                (source_category_leaf, source_category_id, description_category_id,
                 type_id, success_count, fail_count, confidence, source, is_active)
            VALUES (:leaf, 9001, :dc, 2, :sc, 0, 0.7, 'learned', TRUE)
        """), {"leaf": leaf, "dc": _DC, "sc": success_count})
        conn.commit()


def _svc():
    from utils.local_db_manager import LocalDBManager
    return LocalDBManager()


def test_merge_bumps_count_and_refreshes_leaf(engine):
    _mk_canon(engine, "旧措辞", success_count=5)
    _svc().add_category_mapping(
        source_category_leaf="新措辞", description_category_id=_DC, type_id=2,
        confidence=0.6, source="llm", source_category_id=9001)
    with engine.connect() as conn:
        n, sc, leaf = conn.execute(text(
            "SELECT COUNT(*), MAX(success_count), MAX(source_category_leaf) "
            "FROM category_mapping WHERE source_category_id=9001 "
            "AND description_category_id=:dc AND type_id=2"), {"dc": _DC}).fetchone()
    assert n == 1, "同 cid 不同措辞不得裂行"
    assert sc == 6
    assert leaf == "新措辞"


def test_merge_race_counts_every_call(engine):
    """核心回归：并发两路归并 → success_count 精确 +2（旧 ORM 读改写会丢一次）。"""
    _mk_canon(engine, "旧措辞", success_count=5)
    barrier = threading.Barrier(2)

    def _do(leaf: str):
        barrier.wait(timeout=5)
        _svc().add_category_mapping(
            source_category_leaf=leaf, description_category_id=_DC, type_id=2,
            confidence=0.6, source="llm", source_category_id=9001)

    t1 = threading.Thread(target=_do, args=("并发措辞A",))
    t2 = threading.Thread(target=_do, args=("并发措辞B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    with engine.connect() as conn:
        sc = conn.execute(text(
            "SELECT MAX(success_count) FROM category_mapping "
            "WHERE source_category_id=9001 AND description_category_id=:dc"),
            {"dc": _DC}).scalar()
    assert sc == 7, f"并发两次归并应精确 +2，got success_count={sc}"
