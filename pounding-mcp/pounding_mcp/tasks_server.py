"""采集任务 REST 服务（端口 8902）——任务中心前端查询/创建采集任务 + /ask 对话入口。

端点：
  GET  /tasks          任务列表（含状态/进度/摘要）
  GET  /tasks/{id}     单个任务
  POST /tasks          创建采集任务 {kind, params}（后台执行，返回 task_id）
  POST /tasks/{id}/cancel  取消任务（MVP：标记 cancelling，子进程尽力中断）
  POST /ask            （v1）自然语言 → 意图路由 → 直接执行/后台任务/追问/确认
  OPTIONS *            CORS 预检

由 pounding-harness 网关（8766）代理为 /api/pounding/tasks/* 供前端同源调用；
/ask 直接暴露给浏览器跨源 fetch（本服务自带 CORS 头，127.0.0.1 绑定）。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .router import route_intent
from .skill_runner import run_skill_command
from .tasks import _POSITIONAL, COLLECT_KINDS, get_manager

PORT = 8902

# 直接可执行短命令（同步 subprocess）；其余长时命令走后台任务
_DIRECT_COMMANDS = ("check", "category", "search")
# 长时命令（分钟级）→ 后台 get_manager().create() 执行，前端轮询任务
_LONG_COMMANDS = ("graph", "follow", "discover")

_CORS_HEADERS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type"),
    ("Access-Control-Max-Age", "86400"),
]


class TaskHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默访问日志
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in _CORS_HEADERS:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _cors_preflight(self) -> None:
        """CORS 预检：浏览器跨源 POST 前先 OPTIONS，只回响应头即可。"""
        self.send_response(200)
        for name, value in _CORS_HEADERS:
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._cors_preflight()

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
                task_id = get_manager().create(cmd, params, source="ask")
                self._json(200, {"ok": True, "task_id": task_id, "command": cmd})
                return

            # 理论不可达：router 只输出上述命令（F/D1 均带 needs_confirmation）
            self._json(200, {"ok": False, "questions": route["questions"],
                             "pipeline": "unknown"})
        except Exception:  # noqa: BLE001 —— 统一出口，不回显内部异常
            self._json(500, {"ok": False, "error": "route failed"})

    def _read_body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if not size:
            return {}
        try:
            return json.loads(self.rfile.read(size))
        except Exception:  # noqa: BLE001 —— 非法 JSON 视为空 body
            return {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        mgr = get_manager()
        if path == "/tasks" or path == "/tasks/":
            self._json(200, {"items": mgr.list(), "kinds": COLLECT_KINDS})
            return
        if path.startswith("/tasks/"):
            task = mgr.get(path.split("/")[-1])
            if task:
                self._json(200, task)
            else:
                self._json(404, {"error": "task not found"})
            return
        if path == "/health":
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
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
            params = body.get("params", {}) or {}
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
    port = int(__import__("os").environ.get("POUNDING_TASKS_PORT", str(PORT)))
    server = ThreadingHTTPServer(("127.0.0.1", port), TaskHandler)
    print(f"[pounding-tasks] 采集任务服务 http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
