"""v0.73: GET /task_status 补 Bearer 鉴权 + 租户校验（mock-only，无需 PG/GPU）。

端点此前完全无鉴权——任何拿到 task uuid 的人可读全量任务数据（tenant_id/
采购链接/定价成本）。本文件锁定 _task_status_guard 语义：
- env TASK_STATUS_AUTH=0 → 应急开关放行（默认开）。
- 无 Bearer → 401 "Token is required"。
- Bearer 有效 + 租户一致 → 200。
- Bearer 有效 + 租户不一致 → 404 "task not found"（等价不存在，不泄漏存在性）。
- row 无 tenant_id（老数据）→ 宽容读 200。
- /api/v1 别名同款语义。

说明：_verify_analytics_token 在无 Supabase 配置时本地放行（conftest autouse
已清空 SUPABASE_* env）；resolve_tenant 通过 monkeypatch
services.tenant_service.get_supabase 提供假 tokens 表。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_task_status_auth_v073.py -q
"""
import asyncio
import sys
from pathlib import Path

import pytest
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod  # noqa: E402
import services.tenant_service as tenant_service  # noqa: E402

TASK_TENANT = "28"


# ── 替身 ──────────────────────────────────────────────────────

class _FakeProcessor:
    """task_processor 替身：get_task_status 返回预置 row。"""

    def __init__(self, row):
        self._row = row

    async def get_task_status(self, task_id):
        return self._row


class _FakeTokenQuery:
    """supabase .table('tokens').select('user_id').eq().is_().limit().execute() 链替身。"""

    def __init__(self, user_id):
        self._user_id = user_id

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("Result", (), {"data": [{"user_id": self._user_id}]})()


class _FakeSupabase:
    def __init__(self, user_id):
        self._user_id = user_id

    def table(self, _name):
        return _FakeTokenQuery(self._user_id)


def _row(tenant_id=TASK_TENANT, status="running"):
    row = {
        "id": "t1",
        "status": status,
        "priority": 0,
        "payload": {"token": "sk-x"},
        "result": None,
        "error_message": None,
        "retry_count": 0,
        "max_retries": 3,
        "created_at": None,
        "updated_at": None,
        "started_at": None,
        "completed_at": None,
        "timeout_seconds": 1800,
        "progress": None,
    }
    if tenant_id is not None:
        row["tenant_id"] = tenant_id
    return row


def _req(auth: str = "") -> Request:
    headers = []
    if auth:
        headers.append((b"authorization", auth.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/task_status/t1",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


def _call_status(monkeypatch, row, auth="", env_auth=None, handler=None):
    """直调 task_status 处理器的公共 helper（env_auth=None → 删除应急开关 env）。"""
    monkeypatch.setattr(main_mod, "task_processor", _FakeProcessor(row))
    if env_auth is None:
        monkeypatch.delenv("TASK_STATUS_AUTH", raising=False)
    else:
        monkeypatch.setenv("TASK_STATUS_AUTH", env_auth)
    tenant_service.clear_cache()
    fn = handler or main_mod.http_task_status
    return asyncio.run(fn("t1", _req(auth)))


# ── 旧路径路由 ────────────────────────────────────────────────

def test_kill_switch_env_zero_allows_anonymous(monkeypatch):
    """TASK_STATUS_AUTH=0 应急开关：无 token 也 200（锁开关语义）。"""
    resp = _call_status(monkeypatch, _row(), auth="", env_auth="0")
    assert resp["id"] == "t1"
    assert resp["status"] == "running"


def test_missing_bearer_401(monkeypatch):
    with pytest.raises(main_mod.HTTPException) as ei:
        _call_status(monkeypatch, _row(), auth="")
    assert ei.value.status_code == 401
    assert ei.value.detail == "Token is required"


def test_valid_bearer_same_tenant_200(monkeypatch):
    monkeypatch.setattr(tenant_service, "get_supabase", lambda: _FakeSupabase(TASK_TENANT))
    resp = _call_status(monkeypatch, _row(), auth="Bearer sk-tok-a")
    assert resp["id"] == "t1"


def test_valid_bearer_cross_tenant_404(monkeypatch):
    """租户不一致 → 404 "task not found"（等价不存在，不泄漏存在性）。"""
    monkeypatch.setattr(tenant_service, "get_supabase", lambda: _FakeSupabase("99"))
    with pytest.raises(main_mod.HTTPException) as ei:
        _call_status(monkeypatch, _row(), auth="Bearer sk-tok-b")
    assert ei.value.status_code == 404
    assert ei.value.detail == "task not found"


def test_row_without_tenant_id_lenient_read(monkeypatch):
    """老数据 row 无 tenant_id 键 → 跳过比对放行（宽容读）。"""
    resp = _call_status(monkeypatch, _row(tenant_id=None), auth="Bearer sk-tok-c")
    assert resp["id"] == "t1"


# ── /api/v1 别名（复用 helper） ───────────────────────────────

def test_v1_alias_missing_bearer_401(monkeypatch):
    with pytest.raises(main_mod.HTTPException) as ei:
        _call_status(monkeypatch, _row(), auth="", handler=main_mod.v1_task_status)
    assert ei.value.status_code == 401
    assert ei.value.detail == "Token is required"


def test_v1_alias_cross_tenant_404(monkeypatch):
    monkeypatch.setattr(tenant_service, "get_supabase", lambda: _FakeSupabase("99"))
    with pytest.raises(main_mod.HTTPException) as ei:
        _call_status(monkeypatch, _row(), auth="Bearer sk-tok-d", handler=main_mod.v1_task_status)
    assert ei.value.status_code == 404
    assert ei.value.detail == "task not found"


def test_v1_alias_same_tenant_200(monkeypatch):
    monkeypatch.setattr(tenant_service, "get_supabase", lambda: _FakeSupabase(TASK_TENANT))
    resp = _call_status(monkeypatch, _row(), auth="Bearer sk-tok-e", handler=main_mod.v1_task_status)
    assert resp["id"] == "t1"


# ── 终态 progress 语义不回归 ──────────────────────────────────

def test_completed_progress_untouched_by_guard(monkeypatch):
    """鉴权放行后 v0.19 终态 progress 归位逻辑不受影响。"""
    resp = _call_status(
        monkeypatch, _row(status="completed"), auth="Bearer sk-tok-f",
        env_auth="0",
    )
    assert resp["progress"]["stage"] == "completed"
    assert resp["progress"]["percent"] == 100


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
