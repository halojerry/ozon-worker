"""T3 鉴权门（v0.76）：_authenticate_token 直测——无/空 token 401、限流 429、有效 token 放行。

四个裸奔端点（/run /stream_run /node_run /v1/chat/completions）与 /cancel/{run_id}
已于 2026-10 随 platform-compat 调试面整体退役删除，端点级用例随删；本文件保留
token 校验核心单测（活端点共用该唯一入口），mock Supabase，不真实请求。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod


# ============================================================
# _authenticate_token 直接单测（真实逻辑，mock Supabase）
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
