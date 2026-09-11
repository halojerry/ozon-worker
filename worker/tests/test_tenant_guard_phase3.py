"""v0.75 C3: 租户 guard 收尾（BL-17 Phase 2/3 余量）——审计表租户列 + 防复发 CI 闸。

三组断言（对应 plan Task C3 Step 1 的三组失败测试）：
1. 租户面读端点白名单：import main.app 遍历路由（含 FastAPI ≥0.140 的
   _IncludedRouter 嵌套展平），对已收敛集合（error_reports / forensics /
   drafts / credentials / orders / stores / categories(search|attributes) /
   discovery/runs / task_status）断言租户 guard 标记不回退——标记 = 签名含
   tenant 注入（Depends(get_tenant) 形态，error_reports_routes 先例）或
   handler 源引用 Request 版 helper / _authenticate / _task_status_guard
   （main.py 内联端点 Phase 2 形态，见 api/deps_tenant.py 接线状态注释）。
   categories/attributes 两端点必须在列。
2. 审计表裸查询闸：worker/src 全部 .py 中 FROM/JOIN category_match_log /
   attr_match_log 的语句，上下文窗口必须带 tenant_id 条件或 join
   ozon_product_tasks（回填迁移/脚本/测试目录豁免）。
3. 审计写点 SQL 含 tenant_id 列（源码断言 + 捕获式 mock 真写入带租户 /
   空租户落 NULL）+ Input model 声明锁（langgraph 纪律：prepare 链
   PrepareOzonUploadInput.user_id 必须声明，否则静默 None）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_tenant_guard_phase3.py -q
"""
from __future__ import annotations

import inspect
import re
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_WORKER_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _WORKER_ROOT / "src"

# 租户面读端点路径白名单（v0.73~v0.75 已收敛面；前缀按段匹配，防 /drafts-x 误配）
_TENANT_READ_PATH_RE = re.compile(
    r"^/(api/v1)?("
    r"error_reports"
    r"|forensics/task/[^/]+"
    r"|drafts(/.*)?"
    r"|credentials(/[^\s/]*)*"
    r"|orders(/[^\s/]*)*"
    r"|stores/[^/]+(/[^\s/]*)*"
    r"|discovery/runs"
    r"|categories/(search|attributes)"
    r"|task_status/[^/]+"
    r")$"
)

# handler 认定为「租户 guard 已接线」的标记：Depends 注入形态（签名 tenant 参数）
# 或 Request 版 helper / 内联鉴权调用（main.py 内联端点与 routes 文件两种 Phase 2 形态）
_TENANT_SOURCE_MARKERS = (
    "resolve_tenant_from_request",
    "verify_bearer_from_request",
    "resolve_tenant(",
    "_authenticate_token(",
    "_authenticate(request",
    "_task_status_guard",
    "get_tenant",
)


def _iter_api_routes(routes):
    """展平 app.routes——FastAPI 0.141 include_router 产物为 _IncludedRouter
    （嵌套在 .original_router.routes），Mount/Router 递归，APIRoute 直出。"""
    for r in routes:
        orig = getattr(r, "original_router", None)
        if orig is not None:
            yield from _iter_api_routes(orig.routes)
        elif hasattr(r, "endpoint"):
            yield r
        elif hasattr(r, "routes"):
            yield from _iter_api_routes(r.routes)


def _handler_tenant_guarded(route) -> bool:
    ep = route.endpoint
    try:
        if any(str(p).startswith("tenant") for p in inspect.signature(ep).parameters):
            return True
        src = inspect.getsource(ep)
    except (TypeError, ValueError, OSError):
        return False
    return any(m in src for m in _TENANT_SOURCE_MARKERS)


@pytest.fixture(scope="module")
def flat_routes():
    import os
    os.environ.setdefault("GRSAI_API_KEY", "test-key")
    os.environ.setdefault("LOG_FORMAT", "text")
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    import main
    return list(_iter_api_routes(main.app.routes))


# ============================================================
# 1. 租户面读端点白名单（不回退闸）
# ============================================================

def test_tenant_read_endpoints_keep_guard(flat_routes):
    """已收敛租户面 GET 读端点必须全部带 guard 标记——新增裸读端点/回退即红。"""
    unguarded = []
    checked = 0
    for r in flat_routes:
        methods = getattr(r, "methods", None) or set()
        if "GET" not in methods:
            continue
        path = getattr(r, "path", "")
        if not _TENANT_READ_PATH_RE.match(path):
            continue
        checked += 1
        if not _handler_tenant_guarded(r):
            unguarded.append(f"GET {path}")
    assert checked >= 20, f"白名单匹配数异常（路由结构变了？）: {checked}"
    assert not unguarded, f"租户面读端点丢 guard: {unguarded}"


def test_categories_search_and_attributes_present_and_guarded(flat_routes):
    """categories/search + categories/attributes 四条路由（legacy+api/v1）必须在列且有 guard。"""
    want = {"/api/v1/categories/search", "/categories/search",
            "/api/v1/categories/attributes", "/categories/attributes"}
    seen = {}
    for r in flat_routes:
        p = getattr(r, "path", "")
        if p in want and "GET" in (getattr(r, "methods", None) or set()):
            seen[p] = _handler_tenant_guarded(r)
    assert set(seen) == want, f"categories 路由缺失: {want - set(seen)}"
    bad = [p for p, ok in seen.items() if not ok]
    assert not bad, f"categories 端点丢 guard: {bad}"


def test_error_reports_get_and_post_guarded(flat_routes):
    """error_reports 双方法（B4 试点 Depends(get_tenant) 形态）不回退。"""
    found = {}
    for r in flat_routes:
        p = getattr(r, "path", "")
        if p.endswith("/error_reports"):
            methods = getattr(r, "methods", None) or set()
            for m in ("GET", "POST"):
                if m in methods:
                    found[m] = _handler_tenant_guarded(r)
    assert found.get("GET") is True and found.get("POST") is True, found


# ============================================================
# 2. 审计表裸查询闸（FROM/JOIN 必须带 tenant 条件或任务表 join）
# ============================================================

_AUDIT_TABLE_RE = re.compile(
    r"(FROM|JOIN)\s+(category_match_log|attr_match_log)\b", re.IGNORECASE)

# 白名单豁免（文件级）：forensics_service 的两处 task_id 等值查询不做 tenant_id
# 下推——同函数、同连接块内**先**以 `ozon_product_tasks WHERE id::text=:i AND
# tenant_id=:t` 完成任务行租户校验（不属本租户 → None → 端点 404），审计行按
# task_id（=任务 uuid）取出即已租户安全；不做下推还避免迁移回填前的 NULL 历史
# 行被误过滤（v0.75 C3 行为不变约束，test_forensics_endpoint 锁定）。
_AUDIT_SCOPE_EXEMPT_FILES = {"src/services/forensics_service.py"}


def _audit_query_violations():
    violations = []
    for py in sorted(_SRC_DIR.rglob("*.py")):
        if str(py.relative_to(_WORKER_ROOT)) in _AUDIT_SCOPE_EXEMPT_FILES:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for m in _AUDIT_TABLE_RE.finditer(text):
            window = text[max(0, m.start() - 300): m.end() + 300]
            if "tenant_id" in window or "ozon_product_tasks" in window:
                continue
            violations.append(f"{py.relative_to(_WORKER_ROOT)}:{text[:m.start()].count(chr(10)) + 1}")
    return violations


def test_audit_table_queries_carry_tenant_scope():
    """src 下审计表 SELECT/JOIN 必须 tenant 过滤（forensics_service 先例）或任务表 join。"""
    assert not _audit_query_violations(), \
        f"审计表裸查询缺租户条件（新增读路径必须带 tenant_id 或 join 任务表）: {_audit_query_violations()}"


# ============================================================
# 3. 审计写点：SQL 含 tenant_id + 真写入带租户 + Input 声明锁
# ============================================================

def test_audit_writer_sql_contains_tenant_column():
    """两处 INSERT 源码必须含 tenant_id 列（写点遗漏 = 新行永 NULL）。"""
    writer = (_SRC_DIR / "utils" / "attr_match_log.py").read_text(encoding="utf-8")
    assert "INSERT INTO attr_match_log" in writer
    assert "candidates_json, tenant_id)" in writer
    assemble = (_SRC_DIR / "graphs" / "nodes" / "assemble_ozon_product_node.py").read_text(encoding="utf-8")
    assert "INSERT INTO category_match_log" in assemble
    assert "candidates_json, tenant_id)" in assemble


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def execute(self, sql, params=None):
        self._sink.append((str(sql), list(params or [])))

    def close(self):
        pass


class _FakePgConn:
    last = None

    def __init__(self):
        self.sink = []
        self.cursor_obj = _FakeCursor(self.sink)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def close(self):
        pass


def test_log_attr_match_writes_tenant_and_null_for_empty():
    """attr_match_log 写点：tenant_id 值透传；空 → None（老任务兼容，不猜不派生）。"""
    from utils.attr_match_log import log_attr_match
    for tenant, expect in (("28", "28"), ("", None)):
        conn = _FakePgConn()
        with mock.patch("psycopg2.connect", return_value=conn), \
                mock.patch("storage.database.db.get_db_url", return_value="postgresql://fake"):
            log_attr_match(
                task_id="task-1", attr_id=100, attr_name="颜色", source_value="红",
                status="matched", match_layer="synonym", candidates=[], tenant_id=tenant,
            )
        assert conn.sink, "必须执行 INSERT"
        sql, params = conn.sink[0]
        assert "INSERT INTO attr_match_log" in sql and "tenant_id" in sql
        assert params[-1] == expect, (tenant, params[-1])


def test_assemble_match_log_writes_tenant_from_state_user_id():
    """category_match_log 写点：租户值源 state.user_id（assemble 吃 GlobalState）。"""
    from graphs.nodes.assemble_ozon_product_node import _log_match_attempt
    state = types.SimpleNamespace(user_id=" 42 ", draft={"purchase_url": "https://x"}, task_id="t-2")
    conn = _FakePgConn()
    with mock.patch("psycopg2.connect", return_value=conn), \
            mock.patch("storage.database.db.get_db_url", return_value="postgresql://fake"):
        _log_match_attempt(state, "标题", "类目", "关键词  关键词2",
                           {"description_category_id": 1, "type_id": 2},
                           "l1", 0.9, [], config={"configurable": {"thread_id": "t-2"}})
    assert conn.sink, "必须执行 INSERT"
    sql, params = conn.sink[0]
    assert "INSERT INTO category_match_log" in sql and "tenant_id" in sql
    assert params[-1] == "42", "tenant 必须取 state.user_id（strip 后）"


def test_prepare_input_declares_user_id_for_langgraph():
    """langgraph 纪律锁：PrepareOzonUploadInput 必须声明 user_id——
    不声明则 channel 过滤后 getattr 恒空，attr_match_log 租户静默 None。"""
    from graphs.state import GlobalState, PrepareOzonUploadInput
    assert "user_id" in PrepareOzonUploadInput.model_fields
    assert "user_id" in GlobalState.model_fields
