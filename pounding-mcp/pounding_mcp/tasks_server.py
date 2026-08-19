"""采集任务 REST 服务（端口 8902）——任务中心前端查询/创建采集任务。

端点：
  GET  /tasks          任务列表（含状态/进度/摘要）
  GET  /tasks/{id}     单个任务
  POST /tasks          创建采集任务 {kind, params}（后台执行，返回 task_id）
  POST /tasks/{id}/cancel  取消任务（MVP：标记 cancelling，子进程尽力中断）

由 pounding-harness 网关（8766）代理为 /api/pounding/tasks/* 供前端同源调用。
"""

from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .tasks import get_manager, COLLECT_KINDS

PORT = 8902


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

    def _read_body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if not size:
            return {}
        try:
            return json.loads(self.rfile.read(size))
        except Exception:
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
