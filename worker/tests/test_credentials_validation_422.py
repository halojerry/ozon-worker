"""路由层 422 回归：credentials 手拆 body 的 pydantic 校验失败 → 422（非裸 500）。

背景（2026-09-02 链路测试实证）：调用方误用信封词汇 `ozon_api_key` 请求
POST /api/v1/credentials（schema 字段是 `api_key`）。因 credentials_routes 用裸
Request + `model_validate` 手拆 body（非 FastAPI body 参数），pydantic.ValidationError
不命中 422 处理器 → 冒泡成 Starlette 默认裸 500「Internal Server Error」无 detail
（main.py 无 exception_handler）。

本文件纯 mock、无 PG 依赖：422 分支发生在加密/DB 之前，不触达 credential_service。
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod

TENANT_A = main_mod._key_user_id("tokA")
TOKEN_MAP = {"tokA": TENANT_A}


class FakeSupabase:
    """tokens 表 fake：key → user_id 映射（仅够 _authenticate_token 通过）。"""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def table(self, name):
        return _FakeTokensTable(self._mapping)


class _FakeTokensTable:
    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping
        self._key = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        if col == "key":
            self._key = val
        return self

    def is_(self, col, val):
        return self

    def execute(self):
        uid = self._mapping.get(self._key)
        if uid is None:
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[{
            "user_id": uid, "key": self._key, "remain_quota": 999,
            "status": 1, "expired_time": -1, "unlimited_quota": False,
        }])


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    """放行限流 + Supabase tokens 按 key 分租户（无需真实 PG / CREDENTIAL_MASTER_KEY）。"""
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=FakeSupabase(TOKEN_MAP)):
        yield


def _headers() -> dict:
    return {"Authorization": "Bearer sk-tokA"}


def test_create_missing_api_key_422_not_500():
    """body 用 ozon_api_key（信封词汇）→ 必填 api_key 缺失 → 422，不再裸 500。"""
    resp = TestClient(main_mod.app).post("/api/v1/credentials", json={
        "ozon_client_id": "5381204",
        "ozon_api_key": "0b4d15cf-70a2-4505-9764-f64ac169b52f",
        "shop_name": "测试店铺5381204",
        "is_default": True,
    }, headers=_headers())
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "请求体校验失败" in detail
    assert "api_key" in detail


def test_rotate_missing_api_key_422_not_500():
    """PATCH /credentials/{id} 缺必填 api_key → 422（同为手拆 body 校验）。"""
    resp = TestClient(main_mod.app).patch(
        "/api/v1/credentials/00000000-0000-0000-0000-000000000000",
        json={"shop_name": "只改名不轮换"},
        headers=_headers(),
    )
    assert resp.status_code == 422, resp.text
    assert "api_key" in resp.json()["detail"]
