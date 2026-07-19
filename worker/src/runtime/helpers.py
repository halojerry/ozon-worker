"""Minimal replacement for graph_helper, error classifier, stream runners, etc."""

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


# ── graph_helper ──

class GraphHelper:
    """Minimal replacement for coze_coding_utils.helper.graph_helper."""

    @staticmethod
    def is_agent_proj() -> bool:
        return False  # This is always a workflow project

    @staticmethod
    def is_dev_env() -> bool:
        return os.environ.get("APP_ENV", "").upper() == "DEV"

    @staticmethod
    def get_graph_instance(module_path: str) -> Any:
        from graphs.graph import main_graph
        return main_graph  # Already compiled, not callable

    @staticmethod
    def get_graph_node_func_with_inout(graph: Any, node_id: str):
        """Get a node's function and its input/output types by node ID."""
        # Walk the compiled graph's nodes to find the matching node
        nodes = getattr(graph, 'nodes', {})
        for name, node_data in nodes.items():
            if name == node_id or name.endswith(f".{node_id}"):
                func = getattr(node_data, 'func', None) or getattr(node_data, 'afunc', None)
                if func is None:
                    continue
                # Try to get input/output types from function annotations
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.values())
                input_cls = None
                output_cls = None
                if params and params[0].annotation != inspect.Parameter.empty:
                    input_cls = params[0].annotation
                if sig.return_annotation != inspect.Signature.empty:
                    output_cls = sig.return_annotation
                return func, input_cls, output_cls
        raise ValueError(f"Node '{node_id}' not found in graph. Available: {list(nodes.keys())}")

    @staticmethod
    def get_agent_instance(module_path: str, ctx: Any = None) -> Any:
        raise NotImplementedError("Agent mode not supported")


graph_helper = GraphHelper()


# ── Error classifier ──

class ClassifiedError:
    def __init__(self, code: str, message: str, category: Any):
        self.code = code
        self.message = message
        self.category = category


class ErrorCategory:
    def __init__(self, name: str):
        self.name = name


class ErrorClassifier:
    """Minimal error classifier."""

    def classify(self, error: Exception, context: Dict[str, Any] = None) -> ClassifiedError:
        return ClassifiedError(
            code=type(error).__name__,
            message=str(error),
            category=ErrorCategory("UNKNOWN"),
        )


def classify_error(error: Exception, context: Dict[str, Any] = None) -> ClassifiedError:
    return ErrorClassifier().classify(error, context)


# ── Stream runners ──

class RunOpt:
    """Minimal replacement for RunOpt."""
    def __init__(self, timeout: int = 900, **kwargs):
        self.timeout = timeout


class WorkflowStreamRunner:
    """Minimal workflow stream runner."""

    def stream(self, payload: Dict[str, Any], graph: Any, run_config: Any, ctx: Any):
        """Synchronous stream of graph execution chunks."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            async def _stream():
                async for chunk in graph.astream(payload, config=run_config, context=ctx):
                    yield chunk
            gen = _stream()
            while True:
                try:
                    chunk = loop.run_until_complete(gen.__anext__())
                    yield chunk
                except StopAsyncIteration:
                    break
        finally:
            loop.close()


class AgentStreamRunner:
    """Stub - not used in workflow mode."""

    def stream(self, payload, graph, run_config, ctx):
        raise NotImplementedError("Agent mode not supported")


# Stream handlers for FastAPI
def agent_stream_handler(*args, **kwargs):
    raise NotImplementedError("Agent mode not supported")


def workflow_stream_handler(*args, **kwargs):
    raise NotImplementedError("Use local stream implementation")


def to_stream_input(data: Dict[str, Any]) -> Dict[str, Any]:
    return data


def to_client_message(data: Dict[str, Any]) -> Dict[str, Any]:
    return data
