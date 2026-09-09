# -*- coding: utf-8 -*-
"""
v0.73 Task6 — 上传静默收口：import 无 task_id 显式失败 + product_id 语义纯化（Issue6 上游）

生产实证 task 678df8ae：终态「任务完成但未创建 Ozon 商品（product_id 缺失）」——上游
ozon_upload_node 曾把 import task_id 冒充 product_id（注释自称「向后兼容」）：
- /v3/product/import 返回 200 且有 task_id → product_id=str(task_id)（语义污染：
  T0.4 闸 / min_price pre_product_id 闸 / 下游展示都拿到假商品 ID）；
- 200 但 task_id 空 → 旧错误信息为英文 "Ozon response missing task_id"（无响应摘要，
  现场难对账），本任务收口为中文显式 failed + 响应摘要（截断）。
- import_submitted（import-by-sku 已提交待回 product_id）的 pending 等待态是 v0.22 P2a
  合法设计，保持不动。

契约：
- 200+task_id → upload_status="success"、ozon_task_id=task_id（专用通道不变）、
  product_id=None（真实 product_id 由 ozon_status_node 轮询 import/info 后回填）。
- 200+无 task_id → upload_status="failed" + error_message 含「未返回 import task_id」
  + 响应摘要。
- 非 200/异常 → 既有显式 failed 不回退（锁行为）。
- T0.4 闸 `_has_real_product_evidence` 终态防线语义不变：本改动让它更早更干净
  （product_id 从源头不再等于 import task_id）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_upload_silent_v073.py -q
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.ozon_upload_node import ozon_upload_node
from graphs.state import OzonUploadInput
from utils.ozon_errors import OzonError

TASK_ID = "7312849091234"
OFFER = "offer-v073"


def _payload():
    """最小合法 payload（节点对缺字段只告警不阻断）"""
    return {"items": [{"name": "Тест", "offer_id": OFFER, "price": "100", "old_price": "120"}]}


def _state(import_submitted=False):
    return OzonUploadInput(
        ozon_payload=_payload(),
        ozon_client_id="123",
        ozon_api_key="key",
        sku_id=OFFER,
        import_submitted=import_submitted,
    )


def _run(state, ozon_post_return=None, ozon_post_exc=None):
    """跑 upload 节点：mock 配额 + offer 查找 + import POST（对齐 test_upsert_by_offer_v069）。"""
    with patch("graphs.nodes.ozon_upload_node.ozon_check_quota",
               return_value={"ok": True, "daily_used": 1, "daily_limit": 100,
                             "total_used": 1, "total_limit": 1000,
                             "remaining_daily": 99, "remaining_total": 999}), \
         patch("graphs.nodes.ozon_upload_node.find_product_by_offer", return_value=None), \
         patch("graphs.nodes.ozon_upload_node.session"), \
         patch("graphs.nodes.ozon_upload_node.ozon_post") as ozon_post_mock:
        if ozon_post_exc is not None:
            ozon_post_mock.side_effect = ozon_post_exc
        else:
            ozon_post_mock.return_value = ozon_post_return
        out = ozon_upload_node(state, None, SimpleNamespace(context=None))
    return out


# ── ① 200+task_id：success、ozon_task_id 专用通道、product_id 不再冒充 task_id ──
def test_import_200_with_task_id_product_id_purified():
    out = _run(_state(), ozon_post_return={"result": {"task_id": TASK_ID}})
    assert out.upload_status == "success"
    assert out.ozon_task_id == TASK_ID, "task_id 必须走 ozon_task_id 专用通道"
    assert not out.product_id, (
        f"import task_id 不得再冒充 product_id（真实商品 ID 由 ozon_status 回填），"
        f"实际 product_id={out.product_id!r}"
    )


# ── ② 200+无 task_id（result 空对象）→ 显式 failed + 中文错误 + 响应摘要 ──
def test_import_200_without_task_id_explicit_failed():
    out = _run(_state(), ozon_post_return={"result": {}})
    assert out.upload_status == "failed", "200 但无 task_id = 上传未落地，必须显式 failed"
    assert not out.product_id
    assert "未返回 import task_id" in (out.error_message or ""), (
        f"error_message 应含「未返回 import task_id」，实际: {out.error_message!r}"
    )


# ── ③ 200+连 result 键都没有 → 同一显式 failed 出口 ──
def test_import_200_missing_result_key_failed():
    out = _run(_state(), ozon_post_return={})
    assert out.upload_status == "failed"
    assert "未返回 import task_id" in (out.error_message or "")


# ── ④ 200+无 task_id：error_message 带响应摘要（截断）便于对账 ──
def test_import_200_without_task_id_error_carries_response_summary():
    body = {"result": {}, "meta": "unexpected-shape", "x" * 50: "y" * 50}
    out = _run(_state(), ozon_post_return=body)
    assert out.upload_status == "failed"
    # 摘要应携带响应片段（这里用稳定字段名断言，不锁截断长度）
    assert "meta" in (out.error_message or ""), (
        f"error_message 应带响应摘要便于对账，实际: {out.error_message!r}"
    )


# ── ⑤ OzonError（非 200/重试耗尽）→ 既有显式 failed 不回退（锁行为）──
def test_ozon_error_explicit_failed_locked():
    out = _run(_state(), ozon_post_exc=OzonError("BOOM", status_code=500))
    assert out.upload_status == "failed"
    assert not out.product_id
    assert "Ozon API error" in (out.error_message or "")


# ── ⑥ 网络异常 → 既有显式 failed 不回退（锁行为）──
def test_request_exception_failed_locked():
    out = _run(_state(), ozon_post_exc=requests.exceptions.RequestException("conn reset"))
    assert out.upload_status == "failed"
    assert not out.product_id
    assert "Ozon API request exception" in (out.error_message or "")


# ── ⑦ import_submitted pending 等待态保持不动（v0.22 P2a 合法设计，无 error_message）──
def test_import_submitted_pending_kept():
    out = _run(_state(import_submitted=True), ozon_post_return={"result": {"task_id": TASK_ID}})
    assert out.upload_status == "pending"
    assert not out.product_id
    assert not (out.error_message or ""), "合法等待态不得携带错误信息"


# ── ⑧ T0.4 闸语义不变：新形态（product_id=None + task_id 通道）与旧污染形态同样
#      过不了终态佐证闸；真实 product_id 回填后照样通过 ──
def test_t04_gate_semantics_unchanged():
    from utils.task_processor import _has_real_product_evidence

    # 改动后：upload success 尚未轮询回填 → 无真实商品佐证 → failed（更早更干净）
    assert _has_real_product_evidence({
        "product_id": None, "ozon_task_id": TASK_ID, "upload_status": "success",
    }) is False
    # 改动前旧污染形态：product_id==task_id → 同样 False（T0.4 行为不变）
    assert _has_real_product_evidence({
        "product_id": TASK_ID, "ozon_task_id": TASK_ID, "upload_status": "success",
    }) is False
    # ozon_status 轮询回填真实 product_id → 通过
    assert _has_real_product_evidence({
        "product_id": "6254565982", "ozon_task_id": TASK_ID, "upload_status": "success",
    }) is True


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"  OK {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
