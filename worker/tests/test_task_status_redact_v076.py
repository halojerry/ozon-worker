"""v0.76 T3(crypto-H1): task_status 响应 payload 内 token/ozon_api_key 必须脱敏。

背景：payload JSONB 存提交时 GraphInput 原文（顶层 token / ozon_api_key 明文），
task_status 出口此前原样回显——任何拿到 task uuid + 有效 Bearer 的人可读凭证。
修复：http_task_status 组装最终 response 前对 payload 做键名级递归脱敏
（``_redact_payload``，深拷贝语义不改入参）。旧路径与 /api/v1 别名共用同一
组装函数（v1_task_status 直通 http_task_status），单点应用即双路径生效；
MCP get_task_status 走 REST 回调，自动同源。
注：/api/v1 别名的 response_model（TaskStatusResponse 无 payload 字段）本就
整键剥离 payload——旧路径无 response_model 才是明文出网主面，脱敏断言打旧路径。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_task_status_redact_v076.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod  # noqa: E402


# ── 纯函数单测 ────────────────────────────────────────────────

def test_redact_top_and_nested():
    p = {"token": "sk-live-abc", "ozon_api_key": "AK-1", "draft": {"title": "t", "token": "sk-nested"},
         "items": [{"api_key": "K", "keep": 1}], "count": 3}
    out = main_mod._redact_payload(p)
    assert out["token"] == "[REDACTED]" and out["ozon_api_key"] == "[REDACTED]"
    assert out["draft"]["token"] == "[REDACTED]" and out["draft"]["title"] == "t"
    assert out["items"][0]["api_key"] == "[REDACTED]" and out["items"][0]["keep"] == 1
    assert out["count"] == 3
    assert p["token"] == "sk-live-abc"   # 不改入参（深拷贝语义）


def test_redact_case_insensitive_key_and_non_str_value_kept():
    """键名匹配大小写不敏感；非字符串值（dict/int/None）不替换、继续下钻。"""
    p = {"Token": "sk-up", "OZON_API_KEY": "AK-up", "token": {"nested": "dict-value"},
         "password": None, "secret": 12345, "keep_me": "plain"}
    out = main_mod._redact_payload(p)
    assert out["Token"] == "[REDACTED]" and out["OZON_API_KEY"] == "[REDACTED]"
    assert out["token"] == {"nested": "dict-value"}
    assert out["password"] is None and out["secret"] == 12345
    assert out["keep_me"] == "plain"


def test_redact_non_dict_passthrough():
    """payload 顶层非 dict（None/list/str）→ 原样透传不报错。"""
    assert main_mod._redact_payload(None) is None
    assert main_mod._redact_payload([1, 2]) == [1, 2]
    assert main_mod._redact_payload("raw") == "raw"


# ── HTTP 出口（旧路径 + /api/v1 别名共用 http_task_status） ───

class _FakeProcessor:
    """task_processor 替身：get_task_status 返回预置 row（异步真签名）。"""

    def __init__(self, row):
        self._row = row

    async def get_task_status(self, task_id):
        return self._row


def _fake_row(payload):
    return {
        "id": "t1", "tenant_id": "999", "status": "completed", "priority": 0,
        "payload": payload, "result": None, "error_message": None,
        "retry_count": 0, "max_retries": 3, "created_at": None, "updated_at": None,
        "started_at": None, "completed_at": None, "timeout_seconds": 1800,
        "progress": None,
    }


def _client_with(monkeypatch, payload):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_mod, "task_processor", _FakeProcessor(_fake_row(payload)))
    # 应急门放行鉴权：本组测试只验脱敏不验鉴权（鉴权/租户语义已由
    # test_task_status_auth_v073.py 锁定）。
    monkeypatch.setenv("TASK_STATUS_AUTH", "0")
    # 不带 with → 不触发 lifespan（不连 PG / 不启动 worker，同 test_draft_ai_surface 惯例）
    return TestClient(main_mod.app)


def test_task_status_http_response_redacted(monkeypatch):
    """旧路径 /task_status/{id} 无 response_model → payload 真实出网（主泄漏面），
    必须脱敏且业务数据保留。"""
    client = _client_with(monkeypatch, {"token": "sk-secret-xyz",
                                        "envelope": {"ozon_api_key": "AK-9", "draft": {"title": "x"}}})
    r = client.get("/task_status/t1", headers={"Authorization": "Bearer sk-probe"})
    assert r.status_code == 200
    assert "sk-secret-xyz" not in r.text and "AK-9" not in r.text
    assert r.json()["payload"]["envelope"]["draft"]["title"] == "x"   # 业务数据保留


def test_task_status_v1_alias_no_plaintext(monkeypatch):
    """/api/v1 别名：response_model=TaskStatusResponse（无 payload 字段）本就整键剥离
    payload（第二道防线）——锁「明文不出网」+「payload 不回填」防 response_model
    未来误加 payload 字段时裸奔。"""
    client = _client_with(monkeypatch, {"token": "sk-alias-1"})
    r = client.get("/api/v1/task_status/t1", headers={"Authorization": "Bearer sk-probe"})
    assert r.status_code == 200
    assert "sk-alias-1" not in r.text
    assert "payload" not in r.json()


def test_task_status_non_dict_payload_passthrough(monkeypatch):
    """payload 顶层非 dict → 原样透传，端点不 500。"""
    client = _client_with(monkeypatch, "not-a-dict")
    r = client.get("/task_status/t1", headers={"Authorization": "Bearer sk-probe"})
    assert r.status_code == 200
    assert r.json()["payload"] == "not-a-dict"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
