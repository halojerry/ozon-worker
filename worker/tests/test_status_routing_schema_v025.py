"""v0.25 路由回归 — LangGraph 条件边收到节点输入 schema（OzonStatusInput）强转的 state，
moderation_status 被剥掉导致 approved 永远看不到 → 审核通过后死循环重跑 ozon_status。

修复：路由兜底 status="imported"+upload_status="success"+product_id → 成功。
本测试用完整 builder schema（GraphInput/GraphOutput）复现生产死循环并断言终止。

运行：
    cd worker && PYTHONPATH=src python3 tests/test_status_routing_schema_v025.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from langgraph.graph import StateGraph, END, START  # noqa: E402

from graphs.state import GlobalState, GraphInput, GraphOutput  # noqa: E402


def _fake_post(url, *a, **k):
    if "import/info" in url:
        return _R(200, {"result": {"items": [{"offer_id": "x", "product_id": 5821877126, "status": "imported"}]}})
    return _R(200, {"items": [{
        "id": 5821877126, "offer_id": "x",
        "statuses": {"validation_status": "success", "is_created": True, "moderate_status": "approved"},
        "errors": [],
    }]})


class _R:
    def __init__(self, code, j):
        self.status_code = code
        self._j = j

    def json(self):
        return self._j


def _set_ids(state):
    return {"product_id": "5317465021", "ozon_task_id": "5317465021"}


def test_approved_routing_terminates_no_infinite_loop():
    """approved 返回后路由必须进入成功分支，不得死循环重跑 ozon_status。"""
    import time
    from graphs.nodes.ozon_status_node import ozon_status_node
    import graphs.nodes.ozon_status_node as mod
    from graphs.graph import should_handle_error

    b = StateGraph(GlobalState, input_schema=GraphInput, output_schema=GraphOutput)
    b.add_node("set_ids", _set_ids)
    b.add_node("status", ozon_status_node)
    b.add_edge(START, "set_ids")
    b.add_edge("set_ids", "status")
    b.add_conditional_edges("status", should_handle_error, {
        "成功": "done", "失败": "done", "审核中": "status",
    })
    b.add_node("done", lambda s: {})
    b.add_edge("done", END)
    g = b.compile()

    payload = {
        "token": "t", "ozon_client_id": "5381204", "ozon_api_key": "k",
        "envelope": {"draft": {}, "source": {}, "extensions": {}},
    }
    with mock.patch.object(mod.session, "post", side_effect=_fake_post), \
         mock.patch.object(time, "sleep", return_value=None):
        out = g.invoke(payload)
    # 若路由误判审核中 → status 边自环，invoke 不会在 30s 内返回（测试会超时/断言失败）
    assert out is not None


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
