"""v0.76 T6(api-H2): cancel_task 必须鉴权 + 租户校验（mock-only，无需 PG/GPU）。

修复前：POST /cancel_task/{task_id}（旧路径 + /api/v1 别名）完全无鉴权——
匿名持有 task uuid 即可跨租户取消任意 pending 任务（安全探针实证）。
本文件锁定（语义与 task_status v0.73 同源，复用 ``_task_status_guard``）：
- 无 Bearer → 401 "Token is required"。
- Bearer 有效 + 租户不一致 → 404 "task not found"（等价不存在，不泄漏存在性）。
- Bearer 有效 + 租户一致 → 200，取消业务逻辑不变（cancel_task 真被调用）。
- 任务不存在 → 404（先查归属后鉴权，与 task_status 同序）。
- 旧路径 /task_status 系外的 /cancel_task 老路径同款语义（v1 直通主函数）。

说明：conftest autouse 清空 SUPABASE_* env；鉴权两件套用 monkeypatch 替身
（``main._verify_analytics_token`` / ``services.tenant_service.resolve_tenant``
——guard 内延迟 import，patch 源模块即生效）。task_processor 用替身整只替换
（不带 with 的 TestClient 不触发 lifespan，模块级 task_processor 恒 None，
不能按属性 patch——同 test_task_status_redact_v076 惯例）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_cancel_auth_v076.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod  # noqa: E402
import services.tenant_service as tenant_service  # noqa: E402


class _FakeProcessor:
    """task_processor 替身：fetch_task_owner 返回预置 owner，cancel_task 记录调用。"""

    def __init__(self, owner):
        self._owner = owner
        self.cancel_calls: list[str] = []

    async def fetch_task_owner(self, task_id):
        return self._owner

    async def cancel_task(self, task_id):
        self.cancel_calls.append(task_id)
        return True


def _client_with(monkeypatch, owner):
    """公共夹具：整只替换 task_processor + 确保应急门不在 env 里被关。"""
    proc = _FakeProcessor(owner)
    monkeypatch.setattr(main_mod, "task_processor", proc)
    monkeypatch.delenv("TASK_STATUS_AUTH", raising=False)
    # 不带 with → 不触发 lifespan（不连 PG / 不启动 worker，同 test_task_status_redact_v076 惯例）
    return TestClient(main_mod.app, raise_server_exceptions=False), proc


# ── brief 四用例 ──────────────────────────────────────────────

def test_cancel_without_token_401(monkeypatch):
    """无 Bearer → 401 "Token is required"（修复前匿名 200）。"""
    client, proc = _client_with(monkeypatch, {"tenant_id": "9", "status": "pending"})
    r = client.post("/api/v1/cancel_task/tid-1")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"
    assert proc.cancel_calls == []  # 401 不得触达取消逻辑


def test_cancel_cross_tenant_404(monkeypatch):
    """Bearer 有效 + 租户不一致 → 404 "task not found"（不泄漏存在性）。"""
    client, proc = _client_with(monkeypatch, {"tenant_id": "victim-tenant", "status": "pending"})
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)
    monkeypatch.setattr(tenant_service, "resolve_tenant", lambda tok: "attacker-tenant")
    r = client.post("/api/v1/cancel_task/tid-1", headers={"Authorization": "Bearer sk-attacker"})
    assert r.status_code == 404
    assert r.json()["detail"] == "task not found"
    assert proc.cancel_calls == []  # 跨租户不得触达取消逻辑


def test_cancel_owner_ok(monkeypatch):
    """Bearer 有效 + 租户一致 → 200，取消逻辑原样执行。"""
    client, proc = _client_with(monkeypatch, {"tenant_id": "own-tenant", "status": "pending"})
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)
    monkeypatch.setattr(tenant_service, "resolve_tenant", lambda tok: "own-tenant")
    r = client.post("/api/v1/cancel_task/tid-1", headers={"Authorization": "Bearer sk-owner"})
    assert r.status_code == 200
    # v1 别名经 response_model=CancelTaskResponse 过滤 → {ok, task_id, message}
    assert r.json()["ok"] is True
    assert r.json()["task_id"] == "tid-1"
    assert proc.cancel_calls == ["tid-1"]


def test_cancel_missing_row_404(monkeypatch):
    """任务不存在（fetch_task_owner → None）→ 404。"""
    client, _proc = _client_with(monkeypatch, None)
    r = client.post("/api/v1/cancel_task/tid-404")
    assert r.status_code == 404
    assert r.json()["detail"] == "task not found"


# ── 旧路径（v1 直通主函数，但旧路径自身也要被锁住） ──────────────

def test_cancel_legacy_path_without_token_401(monkeypatch):
    """旧路径 /cancel_task/{id}（无 /api/v1 前缀）同样 401——修复前匿名 200 的主路径。"""
    client, _proc = _client_with(monkeypatch, {"tenant_id": "9", "status": "pending"})
    r = client.post("/cancel_task/tid-1")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
