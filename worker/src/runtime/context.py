"""Minimal replacement for local runtime.runtime_ctx.context.Context."""

import uuid
from typing import Optional


class Context:
    """Minimal runtime context carrying a run_id and optional method name."""

    def __init__(self, method: str = "", run_id: Optional[str] = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.method = method

    def __repr__(self) -> str:
        return f"Context(run_id={self.run_id}, method={self.method})"


def new_context(method: str = "") -> Context:
    """Create a new Context with a fresh run_id."""
    return Context(method=method)
