"""B4 租户 guard 二期（design-b2b-tenant-guard Phase 2）：main.py 内联 GET 端点收敛回归。

Phase 2 落地形态说明（与设计文档 §3 的偏差及原因）：
main.py 内联 GET 端点收敛为单行调用 api/deps_tenant 的 Request 版 helper
（resolve_tenant_from_request / verify_bearer_from_request），而非
``Depends(get_tenant)`` 签名注入——既有回归测试（test_analytics_endpoints /
test_discovery_runs_api 等）直接 ``asyncio.run(endpoint(FakeGetRequest(...)))``
调用端点函数、绕过 FastAPI DI，签名注入会让鉴权静默旁路；Request 版 helper 在
「FastAPI 注入」与「直接调用」两条路径下逐字同源（get_tenant dependency 亦委托
同一实现）。

覆盖：
1. verify_bearer_from_request：无 token 401 / sk- 剥除 / 限流开关两形态
2. resolve_tenant_from_request：本地派生租户 + state 缓存短路
3. get_tenant / get_tenant_no_rate_limit dependency 委托同一实现
4. 六个已迁 GET 端点：helper 单行收敛（源码结构断言）+ 行为不变（401/429 直调）
5. categories/attributes：resolve_tenant 原位保持（422 校验后）

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_tenant_guard_phase2.py -q
"""
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.deps_tenant import (  # noqa: E402
    get_tenant,
    get_tenant_no_rate_limit,
    resolve_tenant_from_request,
    verify_bearer_from_request,
)
from services.tenant_service import key_derived_tenant  # noqa: E402


class FakeGetRequest:
    """直接调用型夹具（test_analytics_endpoints 同款 + 可选 state）。"""

    def __init__(self, token, query=None, with_state=False):
        self._token = token
        self.query_params = query or {}
        if with_state:
            from types import SimpleNamespace
            self.state = SimpleNamespace(tenant=None)

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self._token}" if self._token else ""}


# ============================================================
# 1/2/3. helper 语义
# ============================================================

def test_verify_bearer_missing_token_401(monkeypatch):
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    with pytest.raises(main.HTTPException) as ei:
        verify_bearer_from_request(FakeGetRequest(""))
    assert ei.value.status_code == 401
    assert ei.value.detail == "Token is required"


def test_verify_bearer_strips_sk_and_supabase_local_pass(monkeypatch):
    """本地（Supabase 未配置）verify 放行；返回 clean token（sk- 剥一次）。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    out = verify_bearer_from_request(FakeGetRequest("sk-tok-xyz"), rate_limited=False)
    assert out == "tok-xyz"


def test_verify_bearer_rate_limit_switch(monkeypatch):
    """rate_limited=True 受限流闸（429）；False 完全绕过（对齐两类现状端点）。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    monkeypatch.setattr(main.rate_limiter, "check", lambda token: (False, 0))
    with pytest.raises(main.HTTPException) as ei:
        verify_bearer_from_request(FakeGetRequest("sk-tok"), rate_limited=True)
    assert ei.value.status_code == 429
    # 无限流变体：同一限流状态下照常通过
    assert verify_bearer_from_request(FakeGetRequest("sk-tok"), rate_limited=False) == "tok"


def test_resolve_tenant_from_request_local_derived(monkeypatch):
    """本地回退：key 哈希派生租户（PRD M2 前行为）。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    req = FakeGetRequest("sk-tok-abc", with_state=True)
    assert resolve_tenant_from_request(req, rate_limited=False) == key_derived_tenant("tok-abc")
    assert req.state.tenant == key_derived_tenant("tok-abc"), "解析结果须缓存到 request.state"


def test_resolve_tenant_state_cache_short_circuits(monkeypatch):
    """state.tenant 已有值 → 直接复用，不再走 verify/限流/租户解析。"""
    from services import tenant_service as ts

    def _boom(*a, **k):
        raise AssertionError("缓存命中时不得再解析")

    monkeypatch.setattr(ts, "resolve_tenant", _boom)
    req = FakeGetRequest("sk-whatever", with_state=True)
    req.state.tenant = "user_28"
    assert resolve_tenant_from_request(req) == "user_28"


def test_get_tenant_dependencies_delegate_to_same_impl(monkeypatch):
    """get_tenant（限流）/ get_tenant_no_rate_limit（不限流）委托同一 Request 实现。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    monkeypatch.setattr(main.rate_limiter, "check", lambda token: (False, 0))
    req = FakeGetRequest("sk-tok", with_state=True)
    with pytest.raises(main.HTTPException) as ei:
        get_tenant(req)
    assert ei.value.status_code == 429
    assert get_tenant_no_rate_limit(req) == key_derived_tenant("tok")


def test_resolve_tenant_supabase_fail_closed_503(monkeypatch):
    """Supabase 已配置但查询失败 → fail-closed 503（dependency 与内联同语义）。"""
    import main
    from services import tenant_service as ts

    class _BoomSupabase:
        def table(self, name):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    monkeypatch.setattr(ts, "get_supabase", lambda: _BoomSupabase())
    monkeypatch.setattr(ts, "_tenant_cache", {})
    with pytest.raises(main.HTTPException) as ei:
        resolve_tenant_from_request(FakeGetRequest("sk-tok"), rate_limited=False)
    assert ei.value.status_code == 503


# ============================================================
# 4/5. 已迁端点：结构断言 + 行为不变
# ============================================================

# (端点函数名, 是否限流)——对齐迁移前内联序列：前三 GET 无限流，后三有限流
_MIGRATED_GETS = [
    ("v1_analytics_list_bestsellers", False),
    ("v1_discovery_list_runs", False),
    ("v1_mappings_lookup", False),
    ("v1_categories_search", True),
    ("v1_categories_attributes", True),
    ("http_commissions_lookup", True),
]


@pytest.mark.parametrize("fn_name,_rl", _MIGRATED_GETS)
def test_migrated_endpoints_converged_to_helper(fn_name, _rl):
    """六个 GET 端点：单行 helper 收敛，四段/三段内联不再各自手写。"""
    import main

    src = inspect.getsource(getattr(main, fn_name))
    assert "verify_bearer_from_request" in src, f"{fn_name} 未收敛到 deps_tenant helper"
    assert "auth[7:].strip()" not in src, f"{fn_name} 仍残留 Bearer 手工提取"
    assert "_verify_analytics_token" not in src, f"{fn_name} 仍残留 verify 手工调用"
    assert "rate_limiter.check" not in src, f"{fn_name} 仍残留限流手工调用"


@pytest.mark.parametrize("fn_name,_rl", _MIGRATED_GETS)
def test_migrated_endpoints_still_require_token(fn_name, _rl, monkeypatch):
    """行为不变锁：无 token 直调仍 401（鉴权没有被 dependency 迁移旁路）。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    with pytest.raises(main.HTTPException) as ei:
        asyncio.run(getattr(main, fn_name)(FakeGetRequest("")))
    assert ei.value.status_code == 401


class _EmptyReadConn:
    """GET 读路径空结果 fake（非限流端点走通鉴权后的业务段）。"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        if "COUNT(*)" in str(stmt):
            return type("R", (), {"scalar": lambda self: 0})()
        return type("R", (), {"fetchall": lambda self: [], "scalar": lambda self: 0})()

    def commit(self):
        pass


class _EmptyReadEngine:
    def connect(self):
        return _EmptyReadConn()

    def begin(self):
        return _EmptyReadConn()


@pytest.mark.parametrize("fn_name,_rl", _MIGRATED_GETS)
def test_migrated_endpoints_rate_limit_boundary(fn_name, _rl, monkeypatch):
    """限流边界锁：限流端点超限 429；非限流端点同状态照常走到业务（结构化响应）。"""
    import main
    from services import analytics_service

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    monkeypatch.setattr(main.rate_limiter, "check", lambda token: (False, 0))
    monkeypatch.setattr(main, "get_engine", lambda: _EmptyReadEngine())
    monkeypatch.setattr(analytics_service, "get_engine", lambda: _EmptyReadEngine())
    req = FakeGetRequest("sk-tok", query={"dc": "1", "tp": "2"} if fn_name == "v1_categories_attributes" else {})
    if _rl:
        with pytest.raises(main.HTTPException) as ei:
            asyncio.run(getattr(main, fn_name)(req))
        assert ei.value.status_code == 429
    else:
        # 非限流三端点：无限流闸 → 进业务段（bestsellers/discovery 空表、mappings 空 keyword）
        out = asyncio.run(getattr(main, fn_name)(req))
        assert out is not None


def test_categories_attributes_keeps_resolve_tenant_after_422(monkeypatch):
    """categories/attributes：resolve_tenant 保持原位（dc/tp 422 之后才解析租户）。"""
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    calls = []

    def _fake_resolve(token):
        calls.append(token)
        return "user_28"

    import services.tenant_service as ts
    monkeypatch.setattr(ts, "resolve_tenant", _fake_resolve)

    # 非法 dc/tp → 422，且未消耗租户解析
    with pytest.raises(main.HTTPException) as ei:
        asyncio.run(main.v1_categories_attributes(
            FakeGetRequest("sk-tok", query={"dc": "abc", "tp": "x"})))
    assert ei.value.status_code == 422
    assert calls == []

    # 合法 dc/tp → 解析租户（clean token，与 raw 等价）→ 下游降级响应
    import services.category_schema_service as css
    monkeypatch.setattr(css, "get_attributes_with_lazy_fetch", lambda *a, **k: {"found": False})
    out = asyncio.run(main.v1_categories_attributes(
        FakeGetRequest("sk-tok", query={"dc": "1", "tp": "2"})))
    assert out == {"found": False, "cached": False, "attributes": []}
    assert calls == ["tok"]
