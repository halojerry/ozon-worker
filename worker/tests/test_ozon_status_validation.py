"""ozon_status 校验失败如实返回单测（v0.24 F2）— 不再把 validation 失败当软成功。

F-F01（2026-09-09 审计）：轮询收敛 ozon_post 后，patch 点从 session.post
改为 graphs.nodes.ozon_status_node.ozon_post——mock 直接返回解析后的 dict，
非 200 用类型化异常（OzonNotFoundError 等）表达。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.ozon_errors import OzonNotFoundError

FAKE_RUNTIME = SimpleNamespace(context=None)

EP_IMPORT_INFO = "/v1/product/import/info"
EP_INFO_LIST = "/v3/product/info/list"


def _patch_ozon_post(import_info=None, info_list=None):
    """patch ozon_status_node.ozon_post，按 endpoint 分发。

    import_info / info_list：dict（成功返回体）或 Exception 实例（抛出）。
    返回 (patcher, calls)；calls 记录 (endpoint, body)。
    """
    from graphs.nodes import ozon_status_node as mod

    calls: list = []

    def _fake(client_id, api_key, endpoint, body, timeout=60, **kw):
        calls.append((endpoint, body or {}))
        resp = import_info if endpoint == EP_IMPORT_INFO else info_list
        if isinstance(resp, Exception):
            raise resp
        return resp

    return mock.patch.object(mod, "ozon_post", side_effect=_fake), calls


def _silent_sleep():
    import time
    orig = time.sleep
    time.sleep = lambda s: None
    return orig


def test_validation_failed_returns_failed_with_errors():
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = SimpleNamespace(
        ozon_task_id="123", product_id="",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5371047", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )

    import_info = {"result": {"items": [{"offer_id": "x_0", "product_id": 5812496806, "status": "imported"}]}}
    info_list = {"items": [{
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

    patcher, _calls = _patch_ozon_post(import_info=import_info, info_list=info_list)
    orig_sleep = _silent_sleep()
    try:
        with patcher:
            out = ozon_status_node(state, None, FAKE_RUNTIME)
    finally:
        import time
        time.sleep = orig_sleep

    assert out.status == "failed"
    assert out.moderation_status == "validation_failed"
    assert "error_attribute_values_empty" in out.error_message
    assert "8292" in out.error_message
    assert any(e.get("attribute_id") == 8292 for e in out.errors)
    assert out.product_id == "5812496806"  # 真实 product_id，而非 import 任务 ID（v0.25 修复）


def test_in_moderating_stays_pending():
    """审核中（validation success）→ 保持 pending，不误判失败。"""
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = SimpleNamespace(
        ozon_task_id="123", product_id="",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5371047", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )

    import_info = {"result": {"items": [{"offer_id": "x_0", "product_id": 1, "status": "imported"}]}}
    info_list = {"items": [{
        "id": 1, "offer_id": "x_0",
        "statuses": {"validation_status": "success", "is_created": True, "moderate_status": "in-moderating"},
        "errors": [],
    }]}

    patcher, _calls = _patch_ozon_post(import_info=import_info, info_list=info_list)
    orig_sleep = _silent_sleep()
    try:
        with patcher:
            out = ozon_status_node(state, None, FAKE_RUNTIME)
    finally:
        import time
        time.sleep = orig_sleep
    assert out.moderation_status == "pending"
    assert out.status == "pending"


def test_no_import_info_poll_with_product_id_after_imported():
    """仅有真实 product_id（无 import 任务 ID）→ 不得用 product_id 轮询 import/info（404 误判，F3）。"""
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = SimpleNamespace(
        ozon_task_id="", product_id="5812518995",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5371047", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )

    info_list = {"items": [{
        "id": 5812518995, "offer_id": "x_0",
        "statuses": {"validation_status": "success", "is_created": True, "moderate_status": "approved"},
        "errors": [],
    }]}

    patcher, calls = _patch_ozon_post(
        # 若被调用即 404（真实场景会误判失败）
        import_info=OzonNotFoundError("task not found", status_code=404),
        info_list=info_list,
    )
    orig_sleep = _silent_sleep()
    try:
        with patcher:
            out = ozon_status_node(state, None, FAKE_RUNTIME)
    finally:
        import time
        time.sleep = orig_sleep
    assert out.moderation_status == "approved"
    assert not any(EP_IMPORT_INFO in u for u, _ in calls), "不得用 product_id 轮询 import/info"
    assert any(EP_INFO_LIST in u for u, _ in calls)


def test_import_info_polled_when_ozon_task_id_present():
    """有 ozon_task_id → 阶段一用任务 ID 轮询 import/info 拿到真实 product_id，再查 info/list（v0.25 回归）。"""
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = SimpleNamespace(
        ozon_task_id="5312849091", product_id="5312849091",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5381204", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )

    import_info = {"result": {"items": [{"offer_id": "x_0", "product_id": 5814015268, "status": "imported"}]}}
    info_list = {"items": [{
        "id": 5814015268, "offer_id": "x_0",
        "statuses": {"validation_status": "success", "is_created": True, "moderate_status": "in-moderating"},
        "errors": [],
    }]}

    patcher, calls = _patch_ozon_post(import_info=import_info, info_list=info_list)
    orig_sleep = _silent_sleep()
    try:
        with patcher:
            out = ozon_status_node(state, None, FAKE_RUNTIME)
    finally:
        import time
        time.sleep = orig_sleep
    assert any(EP_IMPORT_INFO in u and str(p.get("task_id")) == "5312849091" for u, p in calls), "必须用 import 任务 ID 轮询 import/info"
    assert any(EP_INFO_LIST in u for u, _ in calls)
    assert out.moderation_status == "pending"  # in-moderating 轮询超时 → pending（软成功）


def test_import_info_404_falls_back_to_info_list():
    """ozon_task_id 被污染成 product_id（审核通过后被图重试）→ import/info 404
    必须回退 info/list 查真实状态，不得误判失败（v0.25 浴刷/面具实证）。"""
    from graphs.nodes.ozon_status_node import ozon_status_node
    from utils.ozon_errors import OzonNotFoundError

    state = SimpleNamespace(
        ozon_task_id="5821877126", product_id="5821877126",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5381204", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )

    info_list = {"items": [{
        "id": 5821877126, "offer_id": "x_0",
        "statuses": {"validation_status": "success", "is_created": True, "moderate_status": "approved"},
        "errors": [],
    }]}

    patcher, calls = _patch_ozon_post(
        import_info=OzonNotFoundError("task not found id: 5821877126", status_code=404),
        info_list=info_list,
    )
    orig_sleep = _silent_sleep()
    try:
        with patcher:
            out = ozon_status_node(state, None, FAKE_RUNTIME)
    finally:
        import time
        time.sleep = orig_sleep
    assert out.moderation_status == "approved", f"404 后应回退 info/list 查得 approved, got {out.moderation_status}"
    assert out.status == "imported"
    assert any(EP_INFO_LIST in u for u, _ in calls), "必须回退查询 info/list"
    assert EP_IMPORT_INFO in calls[0][0], "第一次仍应尝试 import/info"


def test_import_info_404_product_not_found_fails():
    """import/info 404 且回退 info/list 查无此商品 → 明确失败（不是伪造的虚假成功）。"""
    from graphs.nodes.ozon_status_node import ozon_status_node
    from utils.ozon_errors import OzonNotFoundError

    state = SimpleNamespace(
        ozon_task_id="9999999999", product_id="9999999999",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5381204", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )

    patcher, _calls = _patch_ozon_post(
        import_info=OzonNotFoundError("task not found", status_code=404),
        info_list={"items": []},
    )
    orig_sleep = _silent_sleep()
    try:
        with patcher:
            out = ozon_status_node(state, None, FAKE_RUNTIME)
    finally:
        import time
        time.sleep = orig_sleep
    assert out.moderation_status == "error", f"查无此商品应失败, got {out.moderation_status}"
    assert "PRODUCT_NOT_FOUND" in out.error_message


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
