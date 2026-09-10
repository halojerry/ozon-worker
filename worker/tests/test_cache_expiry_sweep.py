# worker/tests/test_cache_expiry_sweep.py
"""B2a-5/6: _periodic_task_cleanup 的过期缓存清扫 + fx 每日刷新钩子（纯 mock，无 PG 依赖）。

- 缓存清扫：dictionary_value_cache / attribute_cache 两表按 expires_at（int 秒）
  ctid 批删（LIMIT 5000/批、单表 cap 20 批），24h 节流，失败非致命。
- fx 钩子：lazy import utils.fx_rate_service.refresh_cny_rub_if_due（模块由并行
  开发提供，测试以 sys.modules 注入 stub 解耦），异常吞掉不影响清理主循环。

运行：PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_cache_expiry_sweep.py -q
"""
import sys
import time
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main  # noqa: E402


def _conn_with_rowcounts(rowcounts: list) -> mock.MagicMock:
    """假 conn：execute 按序返回递减 rowcount 序列。"""
    conn = mock.MagicMock()
    conn.execute.side_effect = [mock.MagicMock(rowcount=n) for n in rowcounts]
    return conn


def _captured_sql(conn) -> list:
    return [str(c.args[0]) for c in conn.execute.call_args_list]


# ============================================================
# 缓存清扫（B2a-5）
# ============================================================

def test_sweep_covers_two_tables_with_limit_5000(monkeypatch):
    """SQL 覆盖两表、批删 LIMIT 5000、绑定参数纯 int 秒。"""
    monkeypatch.setattr(main, "_LAST_CACHE_SWEEP", 0.0)
    conn = _conn_with_rowcounts([5000, 3000, 5000, 2000])
    main._maybe_sweep_caches(conn)
    stmts = _captured_sql(conn)
    assert any("dictionary_value_cache" in s for s in stmts)
    assert any("attribute_cache" in s for s in stmts)
    assert not any("category_cache" in s for s in stmts)  # 树快照另有治理，不扫
    assert all("LIMIT 5000" in s for s in stmts)
    for c in conn.execute.call_args_list:
        (params,) = c.args[1:]
        assert set(params) == {"now"}
        assert isinstance(params["now"], int)
    # 成功后推进节流
    assert main._LAST_CACHE_SWEEP > 0.0


def test_sweep_iteration_bounded_at_20_batches_per_table(monkeypatch):
    """行数恒满批（rowcount==5000）→ 单表最多 20 批，两表合计 40 次封顶。"""
    monkeypatch.setattr(main, "_LAST_CACHE_SWEEP", 0.0)
    conn = _conn_with_rowcounts([5000] * 100)
    main._maybe_sweep_caches(conn)
    assert conn.execute.call_count == 40  # 2 表 × cap 20 批


def test_sweep_throttled_within_window(monkeypatch):
    """节流生效：成功后 24h 内再进循环不触发 execute。"""
    monkeypatch.setattr(main, "_LAST_CACHE_SWEEP", 0.0)
    conn = _conn_with_rowcounts([1, 1])
    main._maybe_sweep_caches(conn)
    assert main._LAST_CACHE_SWEEP > 0.0
    idle = mock.MagicMock()
    main._maybe_sweep_caches(idle)
    idle.execute.assert_not_called()


def test_sweep_failure_swallowed_and_not_throttled(monkeypatch):
    """异常非致命吞掉；失败不推进节流（下轮清理循环重试）。"""
    monkeypatch.setattr(main, "_LAST_CACHE_SWEEP", 0.0)
    conn = mock.MagicMock()
    conn.execute.side_effect = RuntimeError("db down")
    main._maybe_sweep_caches(conn)  # 不抛
    assert main._LAST_CACHE_SWEEP == 0.0


# ============================================================
# fx 每日刷新钩子（B2a-6）
# ============================================================

def _fx_stub(fn) -> types.ModuleType:
    mod = types.ModuleType("utils.fx_rate_service")
    mod.refresh_cny_rub_if_due = fn
    return mod


def test_fx_refresh_called_and_throttled(monkeypatch):
    monkeypatch.setattr(main, "_LAST_FX_REFRESH", 0.0)
    fn = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "utils.fx_rate_service", _fx_stub(fn))
    main._maybe_refresh_fx()
    assert fn.call_count == 1
    assert main._LAST_FX_REFRESH > 0.0
    main._maybe_refresh_fx()  # 节流：24h 内不再调
    assert fn.call_count == 1


def test_fx_refresh_failure_swallowed(monkeypatch):
    """刷新异常吞掉（非致命）；失败同样推进节流——防每分钟 warning 刷屏。"""
    monkeypatch.setattr(main, "_LAST_FX_REFRESH", 0.0)

    def _boom():
        raise RuntimeError("fx down")

    monkeypatch.setitem(sys.modules, "utils.fx_rate_service", _fx_stub(_boom))
    main._maybe_refresh_fx()  # 不抛
    assert main._LAST_FX_REFRESH > 0.0


def test_fx_module_missing_swallowed(monkeypatch):
    """utils.fx_rate_service 尚未合入（并行开发）→ ImportError 同样吞掉。"""
    monkeypatch.setattr(main, "_LAST_FX_REFRESH", 0.0)
    monkeypatch.setitem(sys.modules, "utils.fx_rate_service", None)
    main._maybe_refresh_fx()  # 不抛
