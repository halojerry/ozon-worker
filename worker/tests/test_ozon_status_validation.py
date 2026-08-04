"""ozon_status 校验失败如实返回单测（v0.24 F2）— 不再把 validation 失败当软成功。"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

FAKE_RUNTIME = SimpleNamespace(context=None)


def test_validation_failed_returns_failed_with_errors():
    from graphs.nodes import ozon_status_node as mod
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = SimpleNamespace(
        ozon_task_id="123", product_id="",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5371047", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )

    import time
    orig_sleep = time.sleep
    time.sleep = lambda s: None

    class _RespImport:
        status_code = 200
        def json(self):
            return {"result": {"items": [{"offer_id": "x_0", "product_id": 5812496806, "status": "imported"}]}}

    class _RespInfoList:
        status_code = 200
        def json(self):
            return {"items": [{
                "id": 5812496806,
                "offer_id": "x_0",
                "statuses": {"validation_status": "pending", "is_created": False, "moderate_status": ""},
                "errors": [{
                    "code": "error_attribute_values_empty",
                    "attribute_id": 8292,
                    "attribute_name": "Объединить на одной карточке",
                    "level": "ERROR_LEVEL_ERROR",
                    "message": "Attribute value empty",
                    "texts": {"description": "Это обязательное поле"},
                }],
            }]}

    def _post(url, *a, **k):
        if "import/info" in url:
            return _RespImport()
        if "info/list" in url:
            return _RespInfoList()
        return type("R", (), {"status_code": 404, "text": ""})()

    with mock.patch.object(mod.session, "post", side_effect=_post):
        try:
            out = ozon_status_node(state, None, FAKE_RUNTIME)
        finally:
            time.sleep = orig_sleep

    assert out.status == "failed"
    assert out.moderation_status == "validation_failed"
    assert "error_attribute_values_empty" in out.error_message
    assert "8292" in out.error_message
    assert any(e.get("attribute_id") == 8292 for e in out.errors)


def test_in_moderating_stays_pending():
    """审核中（validation success）→ 保持 pending，不误判失败。"""
    from graphs.nodes import ozon_status_node as mod
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = SimpleNamespace(
        ozon_task_id="123", product_id="",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5371047", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )
    import time
    orig_sleep = time.sleep
    time.sleep = lambda s: None

    class _R:
        status_code = 200
        def json(self):
            return {"result": {"items": [{"offer_id": "x_0", "product_id": 1, "status": "imported"}]}}

    class _RL:
        status_code = 200
        def json(self):
            return {"items": [{
                "id": 1, "offer_id": "x_0",
                "statuses": {"validation_status": "success", "is_created": True, "moderate_status": "in-moderating"},
                "errors": [],
            }]}

    def _post(url, *a, **k):
        return _R() if "import/info" in url else _RL()

    with mock.patch.object(mod.session, "post", side_effect=_post):
        try:
            out = ozon_status_node(state, None, FAKE_RUNTIME)
        finally:
            time.sleep = orig_sleep
    assert out.moderation_status == "pending"
    assert out.status == "pending"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
