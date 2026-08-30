"""v0.62.1 P1-6: 测试环境 Supabase 隔离。

根因：测试用 sk-local 假 token 走本地降级路径（get_supabase_client() is None → 放行），
但生产/CI 若配置了真实 SUPABASE_URL/KEY，resolve_tenant 走真实 Supabase → 假 token 401
（112 条失败）。此 fixture 强制测试进程内清空 Supabase env + 重置单例，保证测试永远走
本地降级路径；依赖 Supabase 的用例自己 monkeypatch.setenv 不受影响（autouse 先清、
用例内 setenv 后覆盖）。
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_supabase_env(monkeypatch):
    """每个用例前清空 Supabase 环境变量并重置客户端单例。"""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    try:
        import storage.database.supabase_client as sbc
        sbc._supabase_client = None
    except Exception:
        pass
    yield
    try:
        import storage.database.supabase_client as sbc
        sbc._supabase_client = None
    except Exception:
        pass
