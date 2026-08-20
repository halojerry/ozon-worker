"""T3 鉴权门：/run /stream_run /node_run /v1/chat/completions 四个裸奔端点补鉴权+限流。

无/空 token → 401、限流超限 → 429、有效 token → 通过。mock Supabase，不真实请求。
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod


class FakeRequest:
    def __init__(self, body: dict):
        self._body = body
        self.headers = {}
        self.query_params = {}

    async def body(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")

    async def json(self):
        return self._body


def _valid_supabase() -> MagicMock:
    """返回 tokens 表命中一条有效记录的 Supabase mock。"""
    fake = MagicMock()
    chain = fake.table.return_value.select.return_value.eq.return_value.is_.return_value.execute
    chain.return_value.data = [{
        "user_id": "u1",
        "key": "tok123",
        "remain_quota": 999,
        "status": 1,
        "expired_time": -1,
        "unlimited_quota": False,
    }]
    return fake


def _fake_new_context(method: str = "", headers=None, **kwargs):
    """本地 runtime/context.py 是精简 stub（new_context 不收 headers），测试侧兼容。"""
    from runtime.context import Context
    return Context(method=method)


def _call_run(body: dict):
    return asyncio.run(main_mod.http_run(FakeRequest(body)))


def _call_stream(body: dict):
    return asyncio.run(main_mod.http_stream_run(FakeRequest(body)))


def _call_node(body: dict):
    return asyncio.run(main_mod.http_node_run("some_node", FakeRequest(body)))


def _call_chat(body: dict):
    return asyncio.run(main_mod.openai_chat_completions(FakeRequest(body)))


# ============================================================
# 1. _authenticate_token 直接单测（真实逻辑，mock Supabase）
# ============================================================

def test_authenticate_token_empty_401():
    with pytest.raises(HTTPException) as exc:
        main_mod._authenticate_token("")
    assert exc.value.status_code == 401


def test_authenticate_token_none_401():
    with pytest.raises(HTTPException) as exc:
        main_mod._authenticate_token(None)
    assert exc.value.status_code == 401


def test_authenticate_token_rate_limited_429():
    with patch.object(main_mod.rate_limiter, "check", return_value=(False, 0)), pytest.raises(HTTPException) as exc:
        main_mod._authenticate_token("sk-tok123")
    assert exc.value.status_code == 429


def test_authenticate_token_valid_returns_user_id():
    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)):
        assert main_mod._authenticate_token("sk-tok123") == main_mod._key_user_id("tok123")


# ============================================================
# 2. 四个端点鉴权门（handler 级，真实 _authenticate_token）
# ============================================================

@pytest.mark.parametrize("call,body", [
    (_call_run, {"no_token": True}),
    (_call_stream, {"no_token": True}),
    (_call_node, {"no_token": True}),
    (_call_chat, {"no_token": True}),
], ids=["run", "stream_run", "node_run", "chat"])
def test_auth_gate_missing_token_401(call, body):
    with pytest.raises(HTTPException) as exc:
        call(body)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("call,body", [
    (_call_run, {"token": ""}),
    (_call_stream, {"token": ""}),
    (_call_node, {"token": ""}),
    (_call_chat, {"token": ""}),
], ids=["run", "stream_run", "node_run", "chat"])
def test_auth_gate_empty_token_401(call, body):
    with pytest.raises(HTTPException) as exc:
        call(body)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("call,body", [
    (_call_run, {"token": "sk-tok123"}),
    (_call_stream, {"token": "sk-tok123"}),
    (_call_node, {"token": "sk-tok123"}),
    (_call_chat, {"token": "sk-tok123"}),
], ids=["run", "stream_run", "node_run", "chat"])
def test_auth_gate_rate_limited_429(call, body):
    with patch.object(main_mod.rate_limiter, "check", return_value=(False, 0)), pytest.raises(HTTPException) as exc:
        call(body)
    assert exc.value.status_code == 429


# ============================================================
# 3. 有效 token 通过（下游 mock，只验证鉴权放行）
# ============================================================

def test_auth_gate_run_valid_token_passes():
    async def fake_run(payload, ctx):
        return {"status": "ok"}

    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=_valid_supabase()), \
         patch("main.new_context", side_effect=_fake_new_context), \
         patch.object(main_mod.service, "run", side_effect=fake_run):
        result = _call_run({"token": "sk-tok123"})
    assert result["status"] == "ok"


def test_auth_gate_stream_run_valid_token_passes():
    async def fake_gen():
        yield {"event": "done"}

    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=_valid_supabase()), \
         patch("main.new_context", side_effect=_fake_new_context), \
         patch.object(main_mod.graph_helper, "is_agent_proj", return_value=False), \
         patch("main.workflow_stream_handler", return_value=fake_gen()):
        result = _call_stream({"token": "sk-tok123"})
    assert isinstance(result, StreamingResponse)
    assert result.status_code == 200


def test_auth_gate_node_run_valid_token_passes():
    async def fake_run_node(node_id, payload, ctx):
        return {"node_id": node_id}

    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=_valid_supabase()), \
         patch("main.new_context", side_effect=_fake_new_context), \
         patch.object(main_mod.service, "run_node", side_effect=fake_run_node):
        result = _call_node({"token": "sk-tok123"})
    assert result["node_id"] == "some_node"


def test_auth_gate_chat_valid_token_passes():
    async def fake_handle(payload, ctx):
        return {"choices": [{"message": {"content": "hi"}}]}

    with patch.object(main_mod.rate_limiter, "check", return_value=(True, 10)), \
         patch("main.get_supabase_client", return_value=_valid_supabase()), \
         patch("main.new_context", side_effect=_fake_new_context), \
         patch.object(main_mod.openai_handler, "handle", side_effect=fake_handle, create=True):
        result = _call_chat({"token": "sk-tok123", "messages": [{"role": "user", "content": "hi"}]})
    assert result["choices"][0]["message"]["content"] == "hi"
