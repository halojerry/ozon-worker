"""Minimal replacement for local runtime.runtime_ctx.context.Context."""

import uuid
from typing import Any, Optional


class Context:
    """Minimal runtime context carrying a run_id and optional method name."""

    def __init__(self, method: str = "", run_id: Optional[str] = None,
                 headers: Optional[Any] = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.method = method
        # T12 附带修复（2026-09-16）：main.py 四个 HTTP 入口（/run /stream_run
        # /cancel /node_run）一直传 headers=request.headers，本签名此前不收——
        # 鉴权通过后必 TypeError→500。无任何消费方读它，仅挂到 ctx 备查。
        self.headers = headers

    def __repr__(self) -> str:
        return f"Context(run_id={self.run_id}, method={self.method})"


def new_context(method: str = "", headers: Optional[Any] = None) -> Context:
    """Create a new Context with a fresh run_id."""
    return Context(method=method, headers=headers)
