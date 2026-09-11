"""v0.73 Task1 租户漂移根治：error_reports / forensics / categories 四端点租户解析统一 resolve_tenant。

生产实证：四处用 `_key_user_id(clean_token)`（key 哈希租户 user_<hex>）算租户，而任务
写入侧 submit_task 用 `resolve_tenant(token)`（Supabase tokens.user_id，如 "28"）→
错误报告快照恒 0 条、forensics 恒 404。本测试锁死：Supabase 在场时四端点拿到真实
user_id（不是 key 哈希）。

纯 mock：monkeypatch `services.tenant_service.get_supabase`（假链返回 user_id "28"）
+ 各 service 捕获 tenant_id 入参，直调端点函数（fake Request），不连 PG / 网络。
本地未配置 Supabase 时 resolve_tenant 自动回退 key 哈希（行为与旧版一致），
故此测试必须显式注入假 Supabase 才能暴露漂移。
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import os  # noqa: E402

os.environ.setdefault("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("SKIP_ZOMBIE_RECOVERY", "1")
os.environ.setdefault("SKIP_FAILED_REVIVE", "1")
os.environ.setdefault("SKIP_STORE_SYNC", "1")

import main as main_mod  # noqa: E402
import services.tenant_service as tenant_service  # noqa: E402

TOKEN = "v073tenantTok"
SK_TOKEN = "sk-v073skTok"
EXPECTED_USER_ID = "28"


class _FakeQuery:
    """模拟 supabase-py 链式查询：table().select().eq().is_().limit().execute()。"""

    def __init__(self, data):
        self._data = data

    def select(self, *cols):
        return self

    def eq(self, *a):
        return self

    def is_(self, *a):
        return self

    def limit(self, n):
        return self

    def execute(self):
        return type("Resp", (), {"data": self._data})()


class _FakeSupabase:
    def __init__(self, data):
        self._data = data

    def table(self, name):
        return _FakeQuery(self._data)


class _BoomQuery(_FakeQuery):
    """模拟 Supabase 查询故障：execute() 抛错（resolve_tenant 应兜成 503）。"""

    def execute(self):
        raise RuntimeError("supabase down")


class _BoomSupabase:
    def table(self, name):
        return _BoomQuery([])


class _FakeRequest:
    """端点直调用 fake：headers.get / query_params.get / await json() 三件套。"""

    def __init__(self, token, query=None, body=None):
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.query_params = query or {}
        self._body = body if body is not None else {}
        self.state = type("State", (), {})()  # get_tenant 同请求缓存挂点

    async def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _fake_supabase_env(monkeypatch):
    """假 Supabase 全局生效：tenant_service 走假链；main 的 analytics 鉴权本地放行。"""
    monkeypatch.setattr(tenant_service, "get_supabase",
                        lambda: _FakeSupabase([{"user_id": EXPECTED_USER_ID}]))
    monkeypatch.setattr(main_mod, "get_supabase_client", lambda: None)
    tenant_service.clear_cache()
    yield
    tenant_service.clear_cache()


def test_create_error_report_uses_real_tenant(monkeypatch):
    import services.error_report_service as ers

    cap = {}

    def fake_create(tenant_id, body, worker_version=""):
        cap["tenant"] = tenant_id
        return {"report_id": "r1", "tasks_attached": 0}

    monkeypatch.setattr(ers, "create_error_report", fake_create)
    import routes.error_reports_routes as err_routes
    from api.deps_tenant import get_tenant
    req = _FakeRequest(TOKEN, body={"title": "租户漂移测试"})
    resp = asyncio.run(err_routes.v1_create_error_report(req, tenant_id=get_tenant(req)))
    assert resp.get("status") == "ok"
    assert cap["tenant"] == EXPECTED_USER_ID, (
        f"POST /error_reports 租户漂移：期望 Supabase user_id {EXPECTED_USER_ID!r}，"
        f"实际 {cap['tenant']!r}（key 哈希租户 → 快照恒 0 条）")


def test_list_error_reports_uses_real_tenant(monkeypatch):
    import services.error_report_service as ers

    cap = {}

    def fake_list(tenant_id, limit=50, offset=0, status=None):
        cap["tenant"] = tenant_id
        return {"reports": [], "total": 0}

    monkeypatch.setattr(ers, "list_error_reports", fake_list)
    import routes.error_reports_routes as err_routes
    from api.deps_tenant import get_tenant_no_rate_limit
    req = _FakeRequest(TOKEN)
    resp = asyncio.run(err_routes.v1_list_error_reports(req, tenant_id=get_tenant_no_rate_limit(req)))
    assert resp == {"reports": [], "total": 0}
    assert cap["tenant"] == EXPECTED_USER_ID, (
        f"GET /error_reports 租户漂移：期望 {EXPECTED_USER_ID!r}，实际 {cap['tenant']!r}")


def test_task_forensics_uses_real_tenant(monkeypatch):
    import services.forensics_service as fs

    cap = {}

    def fake_forensics(tenant_id, task_id):
        cap["tenant"] = tenant_id
        return {"task": {"task_id": task_id}}

    monkeypatch.setattr(fs, "get_task_forensics", fake_forensics)
    import routes.error_reports_routes as err_routes
    from api.deps_tenant import get_tenant
    resp = asyncio.run(err_routes.v1_task_forensics(
        "00000000-0000-0000-0000-000000000000",
        tenant_id=get_tenant(_FakeRequest(TOKEN))))
    assert resp == {"task": {"task_id": "00000000-0000-0000-0000-000000000000"}}
    assert cap["tenant"] == EXPECTED_USER_ID, (
        f"GET /forensics/task 租户漂移：期望 {EXPECTED_USER_ID!r}，实际 {cap['tenant']!r}"
        f"（任务行 user_id 写侧 → 取证恒 404）")


def test_categories_attributes_uses_real_tenant(monkeypatch):
    """第四处：categories/attributes 懒拉凭证按租户取默认店铺——同样必须真实 user_id。"""
    import services.credential_service as cs
    import services.category_schema_service as css

    cap = {}

    def fake_default(tenant_id):
        cap["default"] = tenant_id
        return None

    def fake_list(tenant_id):
        cap["list"] = tenant_id
        return []

    monkeypatch.setattr(cs, "get_default_credential", fake_default)
    monkeypatch.setattr(cs, "list_credentials", fake_list)
    monkeypatch.setattr(css, "get_attributes_with_lazy_fetch",
                        lambda *a, **k: {"found": False})
    resp = asyncio.run(main_mod.v1_categories_attributes(
        _FakeRequest(TOKEN, query={"dc": "98765432", "tp": "12345678"})))
    assert resp.get("found") is False
    assert cap.get("default") == EXPECTED_USER_ID and cap.get("list") == EXPECTED_USER_ID, (
        f"GET /categories/attributes 租户漂移：期望 {EXPECTED_USER_ID!r}，"
        f"实际 {cap!r}（凭证按哈希租户查 → 懒拉永远无凭证）")


def test_sk_prefixed_token_resolves_same_tenant(monkeypatch):
    """锁死「传原始 token」语义：sk- 前缀由 resolve_tenant 内部剥（与 submit_task 同源）。"""
    import services.forensics_service as fs

    cap = {}

    def fake_forensics(tenant_id, task_id):
        cap["tenant"] = tenant_id
        return {"task": {}}

    monkeypatch.setattr(fs, "get_task_forensics", fake_forensics)
    import routes.error_reports_routes as err_routes
    from api.deps_tenant import get_tenant
    asyncio.run(err_routes.v1_task_forensics(
        "00000000-0000-0000-0000-000000000000",
        tenant_id=get_tenant(_FakeRequest(SK_TOKEN))))
    assert cap["tenant"] == EXPECTED_USER_ID, (
        f"sk- 前缀 token 租户漂移：期望 {EXPECTED_USER_ID!r}，实际 {cap['tenant']!r}")


def test_categories_attributes_fail_closed_not_swallowed(monkeypatch):
    """categories/attributes 凭证块的 try/except 不得吞掉 resolve_tenant 的 fail-closed。

    Supabase 在场但查询失败 → resolve_tenant 抛 503；该异常是租户/鉴权语义，
    必须传播（同 forensics 等端点），而不是被「凭证解析失败降级纯缓存」吞掉。
    """
    import fastapi

    monkeypatch.setattr(tenant_service, "get_supabase", lambda: _BoomSupabase())
    tenant_service.clear_cache()
    with pytest.raises(fastapi.HTTPException) as ei:
        asyncio.run(main_mod.v1_categories_attributes(
            _FakeRequest(TOKEN, query={"dc": "98765432", "tp": "12345678"})))
    assert ei.value.status_code == 503, (
        f"resolve_tenant fail-closed 被吞：期望 503 传播，实际 {ei.value.status_code}")
    tenant_service.clear_cache()
