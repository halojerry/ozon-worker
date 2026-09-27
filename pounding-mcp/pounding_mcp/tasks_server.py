"""采集任务 REST 服务（端口 8902）——任务中心前端查询/创建采集任务 + /ask 对话入口。

端点：
  GET  /tasks          任务列表（含状态/进度/摘要）【需鉴权】
  GET  /tasks/{id}     单个任务【需鉴权】
  POST /tasks          创建采集任务 {kind, params}（后台执行，返回 task_id）【需鉴权】
  POST /tasks/{id}/cancel  取消任务（MVP：标记 cancelling，子进程尽力中断）【需鉴权】
  POST /ask            （v1）自然语言 → 意图路由 → 直接执行/后台任务/追问/确认【需鉴权】
  GET  /health         存活探测（免鉴权，只回 {"ok": true} 不泄漏信息）
  OPTIONS *            预检（固定 204 空体）

鉴权（v0.76 T18 cicd-H1）：除 OPTIONS 与 /health 外一律校验
`Authorization: Bearer <token>`，失败 401。token 来源：env `POUNDING_TASKS_TOKEN`
优先；未设则启动时 `secrets.token_urlsafe(24)` 生成并往 stderr 打一行
`TASKS_TOKEN=<t>`（harness/用户从输出取）。

CORS（v0.76 T18）：`Access-Control-Allow-Origin: *` 整组头已删除——同机消费方走
非浏览器通道；此前任意网页可 drive-by 驱动 skill CLI（--auto-submit 真实下单）。
浏览器侧属 harness 改造（读 TASKS_TOKEN 注入），登记联动项。

params 白名单（v0.76 T18）：POST /tasks 的 params 只放行 `ALLOWED_PARAM_KEYS[kind]`
声明过的键（见 skill_runner.py，按 skill CLI 真实 flag 集声明），未知键丢弃；
白名单外的命令兜底闸在 `skill_runner._build_argv`（丢弃 + stderr 提示）。

由 pounding-harness 网关（8766）代理为 /api/pounding/tasks/* 供前端同源调用。
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .router import route_intent
from .skill_runner import ALLOWED_PARAM_KEYS, _filter_params, run_skill_command
from .tasks import _POSITIONAL, COLLECT_KINDS, get_manager

PORT = 8902

# 请求体上限（字节）：任务创建/对话入口都是小 JSON，1MB 已远超合理 payload
_BODY_MAX_BYTES = 1_000_000

# 直接可执行短命令（同步 subprocess）；其余长时命令走后台任务。
# v2 起 "query"（router 查进度意图）也同步执行——不带 --watch 的单次状态查询是
# 只读秒级操作，与 check 同类（--watch 轮询最长 900s，属交互层纪律，router 不带）。
_DIRECT_COMMANDS = ("check", "category", "search", "query")
# 长时命令（分钟级）→ 后台 get_manager().create() 执行，前端轮询任务
_LONG_COMMANDS = ("graph", "follow", "discover", "discover_multi", "discover_task")

# /ask 裸位置参数的 CLI 参数名修正：_args_to_params 把一切裸位置参数统一记到
# params["query"] 键下，而 query 命令的 CLI 位置参数名是 task_id（tasks._POSITIONAL
# 只登记了 search/category 的 "query" 键）——按命令就地改名透传，防落成 --query
# 伪 flag 打不中 CLI。
_ASK_BARE_PARAM_TO_POSITIONAL: dict[str, str] = {"query": "task_id"}


def _load_tasks_token() -> str:
    """确定网关令牌：env `POUNDING_TASKS_TOKEN` 优先；未设则随机生成。

    生成时往 stderr 打一行 `TASKS_TOKEN=<t>`（只打一次）——本服务由
    `python -m pounding_mcp.tasks_server` 拉起，模块导入即进程启动，
    harness/用户从输出取 token 注入调用方。"""
    env_token = os.environ.get("POUNDING_TASKS_TOKEN", "").strip()
    if env_token:
        return env_token
    token = secrets.token_urlsafe(24)
    print(f"TASKS_TOKEN={token}", file=sys.stderr, flush=True)
    return token


_TASKS_TOKEN = _load_tasks_token()


def _check_auth(headers: dict) -> bool:
    """Bearer 令牌校验（纯函数）：`Authorization: Bearer <_TASKS_TOKEN>` 精确匹配。

    headers 是 {头名: 值} 平铺 dict（头名大小写不敏感取值）；
    比较走 hmac.compare_digest 防时序侧信道。"""
    auth = ""
    for key, value in (headers or {}).items():
        if str(key).lower() == "authorization":
            auth = value or ""
            break
    expected = f"Bearer {_TASKS_TOKEN}"
    return hmac.compare_digest(auth.encode("utf-8"), expected.encode("utf-8"))


def _body_size_ok(n: int) -> bool:
    """请求体字节数是否在上限内（纯函数）。"""
    return n <= _BODY_MAX_BYTES


class _BodyTooLarge(Exception):
    """请求体超过上限（_read_body 抛出，HTTP 层转 413）。"""


class TaskHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默访问日志
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        """Bearer 鉴权闸：通过返回 True；失败回 401 JSON（不泄漏 token 信息）。"""
        if _check_auth(dict(self.headers.items())):
            return True
        self._json(401, {"error": "unauthorized"})
        return False

    def _content_length(self) -> int:
        try:
            return int(self.headers.get("Content-Length", "0") or 0)
        except (TypeError, ValueError):
            return 0

    def _reject_oversize(self) -> bool:
        """POST 统一 body 上限闸：超限回 413 并返回 True（已应答）。"""
        if _body_size_ok(self._content_length()):
            return False
        self._json(413, {"error": "body too large"})
        return True

    def do_OPTIONS(self) -> None:
        # CORS 已移除：预检固定 204 空体，无 Access-Control-* 头
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _args_to_params(self, args: list[str]) -> dict:
        """路由 args（--flag value 平铺）→ run_skill_command/manager 的 params dict。"""
        params: dict[str, object] = {}
        i = 0
        while i < len(args):
            a = args[i]
            if a.startswith("--"):
                name = a[2:].replace("-", "_")
                if i + 1 < len(args) and not args[i + 1].startswith("--"):
                    params[name] = args[i + 1]
                    i += 2
                else:
                    params[name] = True  # store_true flag
                    i += 1
            else:
                params.setdefault("query", a)  # 位置参数（search/category 的 query）
                i += 1
        return params

    def _exec_routed(self, route: dict) -> dict:
        """按路由执行 skill 命令（位置参数走 cli 定义，flag 走 params）。"""
        cmd = route["command"]
        params = self._args_to_params(route["args"])
        positional = [params.pop(n) for n in _POSITIONAL.get(cmd, []) if n in params]
        cli_name = _ASK_BARE_PARAM_TO_POSITIONAL.get(cmd)
        if cli_name and "query" in params:
            positional.append(params.pop("query"))
        return run_skill_command(cmd, *positional, **params)

    def _handle_ask(self) -> None:
        """POST /ask：自然语言 → 意图路由 → 执行/追问/确认。异常统一 500，不回显内部细节。"""
        try:
            body = self._read_body()
            route = route_intent(str(body.get("text", "")))

            # 歧义/缺对象 → 不执行，用 questions 追问
            if route["needs_clarification"]:
                self._json(200, {"ok": False, "questions": route["questions"],
                                 "pipeline": "unknown"})
                return

            # 写类命令 → 只回显待确认，绝不自动执行
            if route["needs_confirmation"]:
                self._json(200, {"ok": True, "needs_confirmation": True,
                                 "command": route["command"], "args": route["args"],
                                 "pipeline": route["pipeline"]})
                return

            cmd = route["command"]
            if cmd in _DIRECT_COMMANDS:
                result = self._exec_routed(route)
                self._json(200, {"ok": True, "command": cmd, "output": result})
                return

            if cmd in _LONG_COMMANDS:
                params = self._args_to_params(route["args"])
                allowed = ALLOWED_PARAM_KEYS.get(cmd)
                if allowed is not None:
                    params = _filter_params(params, allowed)
                task_id = get_manager().create(cmd, params, source="ask")
                self._json(200, {"ok": True, "task_id": task_id, "command": cmd})
                return

            # 理论不可达：router 只输出上述命令（F/D1 均带 needs_confirmation）
            self._json(200, {"ok": False, "questions": route["questions"],
                             "pipeline": "unknown"})
        except _BodyTooLarge:
            self._json(413, {"error": "body too large"})
        except Exception:  # noqa: BLE001 —— 统一出口，不回显内部异常
            self._json(500, {"ok": False, "error": "route failed"})

    def _read_body(self) -> dict:
        size = self._content_length()
        if not size:
            return {}
        if not _body_size_ok(size):
            raise _BodyTooLarge()
        try:
            return json.loads(self.rfile.read(size))
        except Exception:  # noqa: BLE001 —— 非法 JSON 视为空 body
            return {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            # 存活探测免鉴权：只回固定体，不泄漏 token/任务信息
            self._json(200, {"ok": True})
            return
        if not self._authorized():
            return
        mgr = get_manager()
        if path == "/tasks" or path == "/tasks/":
            self._json(200, {"items": mgr.list(), "kinds": COLLECT_KINDS})
            return
        if path.startswith("/tasks/"):
            task_id = path.split("/")[-1]
            task = mgr.get(task_id)
            if task:
                task = dict(task)
                task["log_tail"] = mgr.log_tail(task_id, 40)  # v0.70 日志可视化
                self._json(200, task)
            else:
                self._json(404, {"error": "task not found"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        if self._reject_oversize():
            return
        path = urlparse(self.path).path
        if path == "/ask":
            self._handle_ask()
            return
        mgr = get_manager()
        if path == "/tasks" or path == "/tasks/":
            body = self._read_body()
            kind = body.get("kind", "")
            if kind not in COLLECT_KINDS:
                self._json(400, {"error": f"unknown kind: {kind}, 可用: {list(COLLECT_KINDS)}"})
                return
            allowed = ALLOWED_PARAM_KEYS.get(kind)
            if allowed is None:
                self._json(400, {"error": f"kind 无参数白名单: {kind}"})
                return
            params = body.get("params", {}) or {}
            if not isinstance(params, dict):
                self._json(400, {"error": "params 必须是对象"})
                return
            params = _filter_params(params, allowed)
            source = body.get("source", "manual")
            task_id = mgr.create(kind, params, source=source)
            self._json(201, {"task_id": task_id, "status": "running"})
            return
        if path.startswith("/tasks/") and path.endswith("/cancel"):
            task_id = path.split("/")[-2]
            ok = mgr.cancel(task_id)
            self._json(200, {"ok": ok, "task_id": task_id})
            return
        self._json(404, {"error": "not found"})


def main() -> None:
    port = int(os.environ.get("POUNDING_TASKS_PORT", str(PORT)))
    server = ThreadingHTTPServer(("127.0.0.1", port), TaskHandler)
    print(f"[pounding-tasks] 采集任务服务 http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
