"""v0.76 T11(race-H1): resolve_tenant 必须消费 tokens.status——封禁/过期 token 不得提交任务。

背景：auth/verify 校验 tokens.status（!=1 拒），但 submit_task 链的 resolve_tenant
只 select user_id——被封禁 token 仍可照常提交任务。本测试锁死 submit 面同闸。

status 语义（与 auth/verify 对齐，见裁决）：
- 1 = active 放行；
- None / 行无该字段（schema 容错/本地 mock）→ 放行；
- 字符串数字按 int() 归一（Supabase 返回形态），int("2")=2 → 拒；
- int() 转换失败的脏值 → 保守放行 + warning（fail-open 仅限脏值，0/2/3/4 必拦）。

缓存纪律：被拒 token 不得写入 _tenant_cache（拒绝发生在缓存写入之前）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi import HTTPException  # noqa: E402

import services.tenant_service as ts  # noqa: E402


def _fake_supabase(row):
    """与 supabase-py 真实链形态对齐：table().select().eq().is_().limit().execute()。"""

    class T:
        def select(self, cols):
            return self

        def eq(self, *a, **k):
            return self

        def is_(self, *a, **k):
            return self

        def limit(self, n):
            return self

        def execute(self):
            class R:
                data = [row] if row else []

            return R()

    class SB:
        def table(self, name):
            return T()

    return SB()


@pytest.fixture(autouse=True)
def _clean_cache():
    ts.clear_cache()
    yield
    ts.clear_cache()


def test_status_disabled_rejected(monkeypatch):
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": 2}))
    with pytest.raises(HTTPException) as e:
        ts.resolve_tenant("sk-banned")
    assert e.value.status_code == 401


def test_status_null_ok(monkeypatch):
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": None}))
    assert ts.resolve_tenant("sk-ok") == "7"


def test_status_active_ok(monkeypatch):
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": 1}))
    assert ts.resolve_tenant("sk-ok") == "7"


def test_status_missing_field_ok(monkeypatch):
    """schema 容错：行无 status 键（v0.73 mock 形态/字段缺失）→ 放行。"""
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7}))
    assert ts.resolve_tenant("sk-ok") == "7"


def test_status_str_number_normalized(monkeypatch):
    """Supabase 返回形态：字符串数字按 int() 归一——"2" 必拦，"1" 放行。"""
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": "2"}))
    with pytest.raises(HTTPException) as e:
        ts.resolve_tenant("sk-banned-str")
    assert e.value.status_code == 401

    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": "1"}))
    assert ts.resolve_tenant("sk-ok-str") == "7"


def test_status_dirty_value_fail_open_with_warning(monkeypatch, caplog):
    """脏值（int() 失败）保守放行 + warning——fail-open 仅限脏值，正常封禁值必拦。"""
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": "banned"}))
    with caplog.at_level("WARNING", logger="services.tenant_service"):
        assert ts.resolve_tenant("sk-dirty") == "7"
    assert any("status" in r.message.lower() or "status" in r.getMessage().lower() for r in caplog.records)


def test_banned_token_not_cached(monkeypatch):
    """被拒 token 不得写缓存：拒绝后再查仍走 Supabase（换好行立即放行）。"""
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": 2}))
    with pytest.raises(HTTPException):
        ts.resolve_tenant("sk-flip")
    assert "sk-flip".removeprefix("sk-") not in ts._tenant_cache

    # 同 token 换成 active 行：无缓存残留 → 立即放行（若误写缓存会返回旧值/仍拒）
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": 1}))
    assert ts.resolve_tenant("sk-flip") == "7"


def test_reject_before_cache_write_order():
    """锁死实现顺序：401 分支必须位于缓存写入之前（防未来重构回退）。"""
    import inspect

    src = inspect.getsource(ts.resolve_tenant)
    reject_pos = src.find('detail="token_invalid or account_inactive"')
    cache_pos = src.find("_tenant_cache[clean]")
    assert reject_pos != -1 and cache_pos != -1
    assert reject_pos < cache_pos, "拒绝必须先于缓存写入（被拒 token 不落缓存）"
