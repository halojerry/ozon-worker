# worker/tests/test_metrics_retention_v0772.py
"""v0.77.2 运维修复（任务二）：store_metrics_history 保留策略（纯 mock，无 PG 依赖）。

背景（生产实数据）：store_metrics_history 每次同步 append 一条快照，是唯一高速
增长表（生产 15,557 行 / ~650 行/店/天；327MB 库中它 12MB 且增速最快），无保留策略。

策略：删除 snapshot_at < NOW() - INTERVAL 'N days' 的行（默认 N=90，
env STORE_METRICS_RETENTION_DAYS 覆盖）；每轮清理循环执行、幂等；失败仅
warning——savepoint 隔离，不污染同轮其它清理写入、不中止主循环。

运行：PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_metrics_retention_v0772.py -q
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main  # noqa: E402


def _conn(rowcount: int = 0) -> mock.MagicMock:
    """假 conn：execute 返回给定 rowcount；begin_nested 上下文不吞异常。"""
    conn = mock.MagicMock()
    conn.execute.return_value = mock.MagicMock(rowcount=rowcount)
    conn.begin_nested.return_value.__exit__.return_value = False
    return conn


def _last_sql_params(conn):
    c = conn.execute.call_args_list[-1]
    return str(c.args[0]), dict(c.args[1] or {})


# ============================================================
# _sweep_store_metrics_history：SQL 形状 + 天数口径
# ============================================================

def test_sweep_default_90_days(monkeypatch):
    """默认保留 90 天：DELETE store_metrics_history，绑定参数 days=90，返回行数。"""
    monkeypatch.delenv("STORE_METRICS_RETENTION_DAYS", raising=False)
    conn = _conn(rowcount=123)
    n = main._sweep_store_metrics_history(conn)
    assert n == 123
    sql, params = _last_sql_params(conn)
    assert "DELETE FROM store_metrics_history" in sql
    assert "snapshot_at" in sql and "now()" in sql.lower()
    assert "make_interval" in sql, "天数走绑定参数（make_interval），不拼字面量"
    assert params == {"days": 90}


def test_sweep_env_override(monkeypatch):
    """env STORE_METRICS_RETENTION_DAYS=30 → 清理 30 天口径生效。"""
    monkeypatch.setenv("STORE_METRICS_RETENTION_DAYS", "30")
    conn = _conn(rowcount=5)
    main._sweep_store_metrics_history(conn)
    _, params = _last_sql_params(conn)
    assert params == {"days": 30}


def test_sweep_invalid_env_falls_back_90(monkeypatch):
    """非法 env（非整数）→ 回落 90，绝不因配置手滑删太多或崩。"""
    monkeypatch.setenv("STORE_METRICS_RETENTION_DAYS", "not-a-number")
    conn = _conn()
    main._sweep_store_metrics_history(conn)
    _, params = _last_sql_params(conn)
    assert params == {"days": 90}


def test_sweep_explicit_days_arg_wins(monkeypatch):
    monkeypatch.setenv("STORE_METRICS_RETENTION_DAYS", "30")
    conn = _conn()
    main._sweep_store_metrics_history(conn, retention_days=180)
    _, params = _last_sql_params(conn)
    assert params == {"days": 180}


# ============================================================
# _maybe_sweep_store_metrics：每轮清理 + 吞错（不影响主循环）
# ============================================================

def test_maybe_sweep_executes_and_returns_count(monkeypatch):
    monkeypatch.delenv("STORE_METRICS_RETENTION_DAYS", raising=False)
    conn = _conn(rowcount=7)
    assert main._maybe_sweep_store_metrics(conn) == 7
    conn.execute.assert_called_once()


def test_maybe_sweep_failure_swallowed(monkeypatch):
    """清理失败仅 warning，返回 0 不抛——主循环不受影响。"""
    conn = mock.MagicMock()
    conn.execute.side_effect = RuntimeError("db down")
    conn.begin_nested.return_value.__exit__.return_value = False
    assert main._maybe_sweep_store_metrics(conn) == 0  # 不抛即过


def test_maybe_sweep_uses_savepoint_isolation(monkeypatch):
    """sweep 走 SAVEPOINT 隔离——失败只回滚本语句，不污染同轮其它清理写入。"""
    conn = _conn()
    main._maybe_sweep_store_metrics(conn)
    conn.begin_nested.assert_called_once()
