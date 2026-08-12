"""Phase 5: 属性匹配审计日志单测（test_attr_match_log.py）。

锁定：
- log_attr_match 非致命（DB 不可用/异常 → warning 不 raise）
- task_id 空 → 跳过
- compute_attempted_fill_rate 复用 Phase 0 compute_gap（系统生成不计分母）
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

from utils.attr_match_log import compute_attempted_fill_rate, log_attr_match  # noqa: E402


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


# ── compute_attempted_fill_rate ──

def test_compute_fill_rate_ignores_system_generated():
    """系统生成属性不计入分母（海关/标记码/品牌/原产国）。"""
    schema = [
        {"id": 22604, "name": "HS编码"},          # 海关
        {"id": 23536, "name": "标记码"},          # 系统生成
        {"id": 85, "name": "品牌"},               # 强制默认
        {"id": 4389, "name": "原产国"},           # 强制默认
        {"id": 10096, "name": "颜色", "dictionary_id": 1494},  # 应填，已填
        {"id": 8962, "name": "件数"},             # 应填，未填
    ]
    r = compute_attempted_fill_rate(schema, [10096])
    assert r["should_fill"] == 2
    assert r["filled"] == 1
    assert r["attempted_fill_rate"] == 0.5


def test_compute_fill_rate_zero_denominator():
    """全系统生成 → 分母 0 → rate 0（不除零）。"""
    schema = [{"id": 22604, "name": "HS编码"}, {"id": 23536, "name": "标记码"}]
    r = compute_attempted_fill_rate(schema, [])
    assert r["should_fill"] == 0
    assert r["attempted_fill_rate"] == 0.0


# ── 表模型结构（对照 category_match_log 先例）──

def test_attr_match_log_model_exists():
    from storage.database.shared.model import AttrMatchLog
    assert AttrMatchLog.__tablename__ == "attr_match_log"
    cols = {c.name for c in AttrMatchLog.__table__.columns}
    assert {"task_id", "attr_id", "attr_name", "source_value",
            "status", "match_layer", "dictionary_value_id", "should_fill",
            "candidates_json", "created_at"} <= cols
