"""v0.76 T8(api-M2): _require_bearer 共享 helper + /progress 鉴权。

修复前：GET /progress/{run_id} 完全无鉴权——任何拿到 run_id（langgraph
thread_id / task uuid）的匿名请求可探测任务存在性与执行进度。本文件锁定：
- helper 缺 Bearer → 401（HTTPException，文案同 forensics/_task_status_guard）。
- helper 正常路径：剥 ``sk-`` 前缀一层 + 走 ``_verify_analytics_token`` 有效性
  校验（替身放行）→ 返回 clean token。
- /progress/{run_id} 无 Bearer → 401（修复前 404/200）。

说明：``_verify_analytics_token`` 为 main 模块级函数 → patch ``main`` 命名
空间生效（同 test_task_statistics_auth_v076 惯例）。不带 with 的 TestClient
不触发 lifespan（不连 PG / 不启动 worker）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_require_bearer_v076.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main as main_mod  # noqa: E402
from main import _require_bearer, app  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def test_require_bearer_missing_401():
    class R:  # headers 只需 dict 语义（helper 仅做 .get）
        headers = {}
    with pytest.raises(HTTPException) as e:
        _require_bearer(R())
    assert e.value.status_code == 401
    assert e.value.detail == "Token is required"


def test_require_bearer_strips_sk(monkeypatch):
    seen = []
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: seen.append(t))
    class R:
        headers = {"Authorization": "Bearer sk-abc123"}
    assert _require_bearer(R()) == "abc123"
    assert seen == ["abc123"]  # 有效性校验收到的是剥前缀后的 clean token


def test_progress_requires_token(monkeypatch):
    monkeypatch.setattr(main_mod, "_verify_analytics_token", lambda t: None)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/progress/run-xyz")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"


def test_progress_token_verify_passthrough(monkeypatch):
    """Bearer 无效 → _verify_analytics_token 的 401 原样透传（不得吞成 404/500）。"""
    def _reject(t):
        raise HTTPException(status_code=401, detail="token_invalid or account_inactive")
    monkeypatch.setattr(main_mod, "_verify_analytics_token", _reject)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/progress/run-xyz", headers={"Authorization": "Bearer bad-token"})
    assert r.status_code == 401
    assert "token_invalid" in r.json()["detail"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
