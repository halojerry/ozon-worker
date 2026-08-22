"""直接 HTTP 调 worker REST 端点的薄封装 —— 与 skill CLI subprocess 模式分开。

现有 19 个工具是 `run_skill_command`（subprocess 调 skill CLI）的薄封装；**本模块是新模式**：
analyze_store / run_store_action 是对 worker API 的**直接 HTTP 操作**，不是 skill CLI 命令，
所以用标准库 `urllib.request` 做 GET/POST（不引入新依赖——pounding-mcp 的 venv 只有 fastmcp）。

端点（worker `routes/store_sync_routes.py` + `routes/store_actions_routes.py`，均挂在 `/api/v1/stores/`）：
    GET  {WORKER_URL}/api/v1/stores/{store_id}/analysis   店铺分析（todo 6）
    POST {WORKER_URL}/api/v1/stores/{store_id}/actions    店铺执行（todo 7）

鉴权：Bearer token 优先（worker 的 `_authenticate` 读 `Authorization: Bearer <token>`）。
token 来源：env WORKER_TOKEN > skill settings.json 的 `mxou_token`。worker 不支持 query token 兜底。

失败约定：worker 不可达 / HTTP != 2xx → 返回 error dict（含 http_status），**不 raise 不慢等**。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# skill 目录：优先环境变量，否则按「本文件在 pounding-mcp/pounding_mcp/ 下 → ../../skill」推导
_DEFAULT_SKILL_DIR = Path(__file__).resolve().parent.parent.parent / "skill"
SKILL_DIR = Path(os.environ.get("OZON_SKILL_DIR", str(_DEFAULT_SKILL_DIR)))

_DEFAULT_WORKER_URL = "https://worker.mxou.cn"

_TIMEOUT_S = 30


def get_worker_url() -> str:
    """Worker 地址：env WORKER_URL > MXOU_API_BASE > 生产默认（对齐 skill `_const.CLOUD_API_BASE`）。"""
    return (os.environ.get("WORKER_URL")
            or os.environ.get("MXOU_API_BASE")
            or _DEFAULT_WORKER_URL).rstrip("/")


def get_worker_token() -> str:
    """worker 鉴权 token：env WORKER_TOKEN 优先，其次 skill settings.json 的 `mxou_token`。

    worker 的 `_authenticate` 读 `Authorization: Bearer <token>`；跨进程（MCP 独立进程）无法
    直接复用 skill 的 config_store 内存态，这里直接读 settings.json 文件（JSON，stdlib 即可）。
    """
    env_token = os.environ.get("WORKER_TOKEN", "").strip()
    if env_token:
        return env_token
    settings_file = SKILL_DIR / "data" / "config" / "settings.json"
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        return str(data.get("mxou_token", "")).strip()
    except Exception:
        return ""


def _request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    """发一个 HTTP 请求，返回解析后的 JSON dict；任何失败返回 error dict（不 raise）。"""
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": True, "raw": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.read else ""
        try:
            detail = json.loads(raw) if raw else {}
        except Exception:
            detail = raw
        if not isinstance(detail, dict):
            detail = {"error": detail}
        return {"ok": False, "http_status": int(exc.code), "error": str(detail)[:500], "raw": raw}
    except Exception as exc:  # noqa: BLE001 — 网络/超时/解析失败统一兜底为 error dict
        return {"ok": False, "http_status": 0, "error": f"Worker 不可达: {exc}"}


def analyze_store(store_id: str) -> dict:
    """GET /api/v1/stores/{store_id}/analysis → 店铺结构化分析。失败返回 error dict。"""
    base = get_worker_url()
    url = f"{base}/api/v1/stores/{store_id}/analysis"
    return _request("GET", url, get_worker_token())


def run_store_action(store_id: str, operation: str, payload: dict[str, Any] | None = None) -> dict:
    """POST /api/v1/stores/{store_id}/actions → 单店执行（改价/库存/归档/活动）。

    payload 为 operation 相关的请求体字段（不含 operation，helper 自动注入）。
    operation ∈ {bulk_update_prices, bulk_update_stocks, bulk_archive, actions_register,
                 seller_action_discount}。失败返回 error dict（不 raise）。
    """
    base = get_worker_url()
    url = f"{base}/api/v1/stores/{store_id}/actions"
    body = dict(payload or {})
    body.setdefault("operation", operation)
    return _request("POST", url, get_worker_token(), body)
