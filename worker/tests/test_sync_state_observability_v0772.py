# -*- coding: utf-8 -*-
"""v0.77.2 — credential_sync_state.orders_error / products_error 死列复活（纯 mock，无 PG）。

生产实证（2026-09-18 ozon_ro 只读分析）：
- store_sync_jobs 近 7 天 failed 1427 条，唯一错误 `filter.to must be after the filter.since`（S1）。
- 但 credential_sync_state 全库 13 行 orders_error/products_error **恒空** —— 错误只在
  jobs.error 留痕，state 面零异常，运维无从发现「订单一直失败、商品一直成功」的店。

根因（job 化执行链，store_sync_scheduler._run_job → store_sync_service.sync_store）：
  sync_store 依次**无条件**调用 `_sync_orders` → `_sync_products`（orders/products 无域节流）。
  1. `_sync_orders` 拉取失败（S1 400）→ except 分支 `_set_orders_error_no_watermark` **确实写了**
     orders_error="拉取失败: …"。→ 说明不是「没被调用」也不是「被 try 吞」。
  2. 紧接着 `_sync_products` 成功 → `_set_sync_error(kind="products", "")` 的 ON CONFLICT
     UPDATE 里带了 `orders_error = ''`（other_err 清空）→ **把上一步刚写的订单错误原地抹掉**。
  3. 且 `_sync_products` 失败时从不写 products_error（只在成功分支调 `_set_sync_error(…, "")`）
     → products_error 连「写过再被抹」都没有，从来就是死列。

契约（本文件锁定）：
- 兄弟域的成功写**绝不**清空另一域的错误列（反 clobber）；
- `_sync_orders` 失败 → orders_error 非空；成功 → 清空（_complete_orders_window 既有行为）；
- `_sync_products` 失败 → products_error 非空；成功 → 清空；
- `mark_sync_failure(error=…)`（job 层兜底）只填充**空白**列，不覆盖域级精确错误；
- 全部非致命（不得抛出）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest \
      tests/test_sync_state_observability_v0772.py -q
"""
import datetime
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import services.store_sync_service as sss  # noqa: E402
from services import store_sync_jobs as jobs  # noqa: E402


# ──────────────────────────────────────────────────────────────
# 纯 mock 引擎：捕获 execute() 的 SQL 文本与参数（不起 PG）
# ──────────────────────────────────────────────────────────────
class _FakeResult:
    rowcount = 1

    def fetchone(self):
        return None

    def scalar(self):
        return None

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self, log):
        self._log = log

    def execute(self, stmt, params=None):
        self._log.append((str(stmt), dict(params or {})))
        return _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, log):
        self._log = log

    def begin(self):
        return _FakeConn(self._log)

    def connect(self):
        return _FakeConn(self._log)


def _conflict_clause(sql: str) -> str:
    """取 ON CONFLICT ... DO UPDATE SET 之后的片段（判定兄弟列是否被误清）。"""
    marker = "DO UPDATE SET"
    return sql.split(marker, 1)[1] if marker in sql else ""


def _state_row(orders_last_synced_at=None, incomplete=False,
               window_since=None, window_to=None):
    return SimpleNamespace(
        orders_sync_incomplete=incomplete,
        orders_last_synced_at=orders_last_synced_at,
        orders_window_since=window_since,
        orders_window_to=window_to,
        orders_sync_cursor="",
        products_last_synced_at=None,
        orders_error="",
        products_error="",
    )


# ──────────────────────────────────────────────────────────────
# ① 反 clobber：products 成功写不得清空 orders_error
# ──────────────────────────────────────────────────────────────
def test_products_success_write_does_not_clear_orders_error():
    """_set_sync_error(kind='products', '') 的 ON CONFLICT 不得触碰 orders_error。

    死列根因回归锁：任何「兄弟域成功 → 抹掉对方错误」的写法都是生产 13 行恒空的元凶。
    """
    log: list = []
    with patch.object(sss, "get_engine", return_value=_FakeEngine(log)):
        sss._set_orders_error_no_watermark("t1", "c1", "拉取失败: filter.to must be after")
        sss._set_sync_error("t1", "c1", "products", "")
    assert len(log) == 2
    orders_sql = log[0][0]
    products_conflict = _conflict_clause(log[1][0])
    # orders 错误写：只动 orders_error
    assert "orders_error" in _conflict_clause(orders_sql)
    # products 成功写：必须只动 products 列，绝不能出现 orders_error
    assert "orders_error" not in products_conflict, (
        "products 成功写清空了 orders_error —— 死列根因回归"
    )


def test_orders_success_write_does_not_clear_products_error():
    """对称：orders 完成写不得清空 products_error（防反向 clobber）。"""
    log: list = []
    with patch.object(sss, "get_engine", return_value=_FakeEngine(log)):
        sss._set_sync_error("t1", "c1", "products", "拉取失败: 商品")
        sss._complete_orders_window("t1", "c1",
                                    datetime.datetime.now(datetime.timezone.utc))
    products_conflict = _conflict_clause(log[0][0])
    orders_conflict = _conflict_clause(log[1][0])
    assert "products_error" in products_conflict
    assert "orders_error" in orders_conflict          # 成功 → 清自己的
    assert "products_error" not in orders_conflict, (
        "orders 完成写清空了 products_error —— 反向 clobber"
    )


# ──────────────────────────────────────────────────────────────
# ② 订单：失败写 orders_error，成功清空
# ──────────────────────────────────────────────────────────────
S1_ERR = "OzonValidationError: filter.to must be after the filter.since"


def test_orders_failure_records_orders_error():
    def _boom(*a, **k):
        raise RuntimeError(S1_ERR)

    with patch.object(sss, "_sync_state_row",
                      return_value=_state_row(orders_last_synced_at=None)), \
         patch("utils.ozon_client.ozon_post", side_effect=_boom), \
         patch.object(sss, "_set_orders_error_no_watermark") as err_mock:
        out = sss._sync_orders("t1", "c1", "cid", "key")
    assert out["error"], "失败必须返回 error"
    err_mock.assert_called_once()
    _t, _c, written = err_mock.call_args[0]
    assert written, "orders_error 必须写入非空文本（否则 state 面零留痕）"
    assert "filter.to" in written


def test_orders_success_clears_orders_error():
    log: list = []
    with patch.object(sss, "_sync_state_row",
                      return_value=_state_row(orders_last_synced_at=None)), \
         patch("utils.ozon_client.ozon_post",
               side_effect=lambda *a, **k: {"postings": []}), \
         patch.object(sss, "get_engine", return_value=_FakeEngine(log)):
        out = sss._sync_orders("t1", "c1", "cid", "key")
    assert out["error"] == ""
    complete_conflict = _conflict_clause(log[0][0])
    assert "orders_error = ''" in complete_conflict, "成功后必须清空 orders_error"


# ──────────────────────────────────────────────────────────────
# ③ 商品：失败写 products_error，成功清空
# ──────────────────────────────────────────────────────────────
def test_products_failure_records_products_error():
    def _boom(*a, **k):
        raise RuntimeError("商品拉取 500")

    with patch("utils.ozon_client.ozon_post", side_effect=_boom), \
         patch.object(sss, "_archive_missing"), \
         patch.object(sss, "_set_products_error_no_watermark") as err_mock, \
         patch.object(sss, "_set_sync_error") as ok_mock:
        out = sss._sync_products("t1", "c1", "cid", "key")
    assert out["error"], "商品失败必须返回 error"
    err_mock.assert_called_once()
    _t, _c, written = err_mock.call_args[0]
    assert "商品拉取 500" in written, "products_error 必须写入非空文本"
    ok_mock.assert_not_called()  # 失败分支不得走「成功清空」写


def test_products_success_clears_products_error():
    with patch("utils.ozon_client.ozon_post",
               side_effect=lambda *a, **k: {"result": {"total": 0, "items": []}}), \
         patch.object(sss, "_archive_missing"), \
         patch.object(sss, "_set_products_error_no_watermark") as err_mock, \
         patch.object(sss, "_set_sync_error") as ok_mock:
        out = sss._sync_products("t1", "c1", "cid", "key")
    assert out["error"] == ""
    err_mock.assert_not_called()
    ok_mock.assert_called_once_with("t1", "c1", "products", "")


# ──────────────────────────────────────────────────────────────
# ④ job 层兜底：mark_sync_failure(error=…) 填空白列、不覆盖精确错误、非致命
# ──────────────────────────────────────────────────────────────
def test_mark_sync_failure_fills_blank_error_columns_only():
    log: list = []
    with patch.object(jobs, "get_engine", return_value=_FakeEngine(log)):
        jobs.mark_sync_failure("t1", "c1", error="同步任务异常: 凭证不可用")
    assert len(log) == 1
    sql, params = log[0]
    assert params.get("e") == "同步任务异常: 凭证不可用"
    update = _conflict_clause(sql)
    # 空白守卫：只填空白列，已有精确域级错误不被 job 层泛化文本覆盖
    assert "orders_error" in update and "products_error" in update
    assert "NULLIF" in update, "job 层兜底必须只填空白列（NULLIF 守卫）"


def test_mark_sync_failure_without_error_is_non_destructive():
    log: list = []
    with patch.object(jobs, "get_engine", return_value=_FakeEngine(log)):
        jobs.mark_sync_failure("t1", "c1")  # 既有调用姿势（无 error）
    sql, params = log[0]
    assert params.get("e") == ""
    assert "consecutive_failures" in _conflict_clause(sql)


def test_mark_success_clears_both_error_columns():
    log: list = []
    with patch.object(jobs, "get_engine", return_value=_FakeEngine(log)):
        jobs.mark_sync_success("t1", "c1", 42)
    update = _conflict_clause(log[0][0])
    assert "orders_error = ''" in update and "products_error = ''" in update, (
        "job 成功后 state 面必须清空两列错误"
    )
