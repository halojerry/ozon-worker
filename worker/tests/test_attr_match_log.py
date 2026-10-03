"""Phase 5: 属性匹配审计日志单测（test_attr_match_log.py）。

锁定：
- log_attr_match 非致命（DB 不可用/异常 → warning 不 raise）
- task_id 空 → 跳过
- 表模型 AttrMatchLog 结构（category_match_log 先例对照）
无需 PG（mock psycopg2）。
"""
import os
import sys
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.attr_match_log import log_attr_match  # noqa: E402


def test_log_empty_task_id_skips():
    """task_id 空 → 跳过（不调 DB）。"""
    with mock.patch("psycopg2.connect", side_effect=AssertionError("不应调 DB")):
        log_attr_match("", attr_id=10096, attr_name="颜色", source_value="白色",
                       status="matched", match_layer="exact")


def test_log_db_error_non_fatal():
    """DB 异常 → warning 不 raise（非致命纪律）。"""
    with mock.patch("psycopg2.connect", side_effect=Exception("conn refused")):
        # 不 raise 即通过
        log_attr_match("task1", attr_id=10096, attr_name="颜色", source_value="白色",
                       status="matched", match_layer="exact")


def test_log_success_write():
    """正常写入路径：connect → insert → commit → close。"""
    fake_conn = mock.MagicMock()
    fake_cur = fake_conn.cursor.return_value
    with mock.patch("psycopg2.connect", return_value=fake_conn), \
         mock.patch("storage.database.db.get_db_url", return_value="postgresql://x"):
        log_attr_match("task1", attr_id=10096, attr_name="颜色", source_value="白色",
                       status="matched", match_layer="exact",
                       dictionary_value_id=61571, candidates=[{"id": 61571, "value": "Белый"}])
    fake_conn.commit.assert_called_once()
    fake_conn.close.assert_called_once()
    assert fake_cur.execute.call_count == 1


def test_log_candidates_truncated():
    """candidates_json 截断 15 条。"""
    fake_conn = mock.MagicMock()
    with mock.patch("psycopg2.connect", return_value=fake_conn), \
         mock.patch("storage.database.db.get_db_url", return_value="postgresql://x"):
        log_attr_match("task1", attr_id=1, attr_name="x", source_value="v",
                       status="matched", match_layer="x",
                       candidates=[{"id": i, "value": "v"} for i in range(30)])
    sql, params = fake_conn.cursor.return_value.execute.call_args[0]
    import json
    cands = json.loads(params[11])
    assert len(cands) == 15  # 截断


# ── 表模型结构（对照 category_match_log 先例）──

def test_attr_match_log_model_exists():
    from storage.database.shared.model import AttrMatchLog
    assert AttrMatchLog.__tablename__ == "attr_match_log"
    cols = {c.name for c in AttrMatchLog.__table__.columns}
    assert {"task_id", "attr_id", "attr_name", "source_value",
            "status", "match_layer", "dictionary_value_id", "should_fill",
            "candidates_json", "created_at"} <= cols
