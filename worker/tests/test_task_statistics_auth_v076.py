"""v0.76 T7(api-H3): task_statistics 补鉴权 + 非 admin 不可查他人租户（mock-only）。

修复前：GET /task_statistics（旧路径 + /api/v1 别名）完全无鉴权——匿名可枚举
任意租户任务量；且不传 tenant_id 时 task_processor 层跨全租户聚合（无 WHERE）。
本文件锁定（行为定义以 task-7-brief 为准）：
- 无 Bearer → 401 "Token is required"（统计逻辑不得触达）。
- Bearer 有效 + 不传 tenant_id → 200，恒按自己租户查（缺省=查自己，不再全租户聚合）。
- tenant_id 指向他人租户 + 非 admin → 403 "admin only"（processor 不被调用，
  不回显他人数据）。
- tenant_id 指向他人租户 + admin（resolve_analytics_scope 放行）→ 200 按指定租户查。
- tenant_id = 自己租户 → 放行（MCP get_task_statistics 恒传自己租户的兼容面）。
- 旧路径 /task_statistics（无 /api/v1 前缀）同款语义。

说明：conftest autouse 清空 SUPABASE_* env；鉴权两件套用 monkeypatch 替身
（``main._verify_analytics_token`` 模块级替换；``resolve_tenant`` 端点内延迟
import → patch 源模块 ``services.tenant_service`` 即生效）。非 admin 跨租户
用例**不** patch ``resolve_analytics_scope``——依赖「未配置 Supabase → 本地
非 admin」真实默认（本地 fail-safe 口径一并锁定）；admin 用例 patch ``main``
命名空间（resolve_analytics_scope 为 main 模块级 import）。task_processor 用
替身整只替换（不带 with 的 TestClient 不触发 lifespan，模块级 task_processor
恒 None，不能按属性 patch——同 test_cancel_auth_v076 惯例）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_task_statistics_auth_v076.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod  # noqa: E402
import services.tenant_service as tenant_service  # noqa: E402

OWN_TENANT = "my-tenant"


class _FakeProcessor:
    """task_processor 替身：get_task_statistics 记录收到的租户并返回预置统计。"""

    def __init__(self):
        self.tenant_calls: list[str] = []

    async def get_task_statistics(self, tenant):
        self.tenant_calls.append(tenant)
        return {"total": 1}


def _client_with(monkeypatch):
    """公共夹具：整只替换 task_processor + 鉴权两件套替身。"""
    proc = _FakeProcessor()
    monkeypatch.setattr(main_mod, "task_processor", proc)
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)
    monkeypatch.setattr(tenant_service, "resolve_tenant", lambda tok: OWN_TENANT)
    # 不带 with → 不触发 lifespan（不连 PG / 不启动 worker，同 test_cancel_auth_v076 惯例）
    return TestClient(main_mod.app, raise_server_exceptions=False), proc


# ── brief 四用例 ──────────────────────────────────────────────

def test_no_token_401(monkeypatch):
    """无 Bearer → 401 "Token is required"（修复前匿名 200 全租户聚合）。"""
    client, proc = _client_with(monkeypatch)
    r = client.get("/api/v1/task_statistics")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"
    assert proc.tenant_calls == []  # 401 不得触达统计逻辑


def test_own_tenant_ok(monkeypatch):
    """Bearer 有效 + 不传 tenant_id → 200，恒按自己租户查（缺省=查自己）。"""
    client, proc = _client_with(monkeypatch)
    r = client.get("/api/v1/task_statistics", headers={"Authorization": "Bearer sk-me"})
    assert r.status_code == 200
    assert r.json()["total"] == 1  # v1 别名经 response_model 解包（见 test_v1_alias_unwraps_statistics）
    assert proc.tenant_calls == [OWN_TENANT]  # 修复前 tenant_id=None → 全租户聚合


def test_legacy_path_wrapped_contract(monkeypatch):
    """旧路径（鉴权放行后）保持 {"status","statistics"} 包裹契约不回归。"""
    client, _proc = _client_with(monkeypatch)
    r = client.get("/task_statistics", headers={"Authorization": "Bearer sk-me"})
    assert r.status_code == 200
    assert r.json() == {"status": "success", "statistics": {"total": 1}}


def test_other_tenant_403_for_non_admin(monkeypatch):
    """非 admin 指定他人租户 → 403 "admin only"，processor 不被调用（不回显他人数据）。

    依赖真实 resolve_analytics_scope 的本地默认（conftest 清空 SUPABASE_* →
    未配置 Supabase → is_admin=False），一并锁定本地 fail-safe 非 admin 口径。
    """
    client, proc = _client_with(monkeypatch)
    r = client.get("/api/v1/task_statistics?tenant_id=victim",
                   headers={"Authorization": "Bearer sk-me"})
    assert r.status_code == 403
    assert r.json()["detail"] == "admin only"
    assert proc.tenant_calls == []  # 403 路径下 processor 不应被调用


def test_admin_can_query_other(monkeypatch):
    """admin 指定他人租户 → 200 且按指定租户查（resolve_analytics_scope 放行）。"""
    client, proc = _client_with(monkeypatch)
    monkeypatch.setattr(main_mod, "resolve_analytics_scope",
                        lambda tok: {"tenant_id": OWN_TENANT, "is_admin": True})
    r = client.get("/api/v1/task_statistics?tenant_id=victim",
                   headers={"Authorization": "Bearer sk-admin"})
    assert r.status_code == 200
    assert proc.tenant_calls == ["victim"]


# ── 兼容面 / 路径变体 ─────────────────────────────────────────

def test_same_tenant_param_ok(monkeypatch):
    """tenant_id = 自己租户 → 放行（MCP get_task_statistics 恒传自己租户）。"""
    client, proc = _client_with(monkeypatch)
    r = client.get(f"/api/v1/task_statistics?tenant_id={OWN_TENANT}",
                   headers={"Authorization": "Bearer sk-me"})
    assert r.status_code == 200
    assert proc.tenant_calls == [OWN_TENANT]


def test_empty_tenant_param_falls_back_to_own(monkeypatch):
    """tenant_id 空串 → 视同缺省，按自己租户查。"""
    client, proc = _client_with(monkeypatch)
    r = client.get("/api/v1/task_statistics?tenant_id=",
                   headers={"Authorization": "Bearer sk-me"})
    assert r.status_code == 200
    assert proc.tenant_calls == [OWN_TENANT]


def test_legacy_path_without_token_401(monkeypatch):
    """旧路径 /task_statistics（无 /api/v1 前缀）同样 401——修复前匿名 200 的主路径。"""
    client, _proc = _client_with(monkeypatch)
    r = client.get("/task_statistics")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"


def test_v1_alias_unwraps_statistics(monkeypatch):
    """v1 别名解包 statistics 的响应结构不回归（TaskStatisticsResponse 字段过滤）。"""
    client, _proc = _client_with(monkeypatch)
    r = client.get("/api/v1/task_statistics", headers={"Authorization": "Bearer sk-me"})
    body = r.json()
    assert set(body.keys()) <= {"total", "pending", "running", "completed",
                                "failed", "cancelled", "avg_duration_seconds"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
