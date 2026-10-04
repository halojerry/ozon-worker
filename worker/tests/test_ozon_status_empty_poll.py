"""ozon_status 空/异常回传分层语义回归（2026-10-04 事故 6516590295「假失败真在架」）。

事故：任务 6516590295 终态 failed_stage=ozon_status，但卡实际 moderate_status=approved
（测试店凭证直查 Ozon API 证实）——「假失败真在架」（对照 2026-09-24 follow×5 口径）。

修复语义分层（graphs/nodes/ozon_status_node.py）：
  ① 轮询空 / 瞬时异常（OzonError 5xx·429、网络 Timeout/ConnectionError 裸异常、
     非预期响应结构）→ 有界重试（OZON_STATUS_TRANSIENT_RETRIES，默认 3）；
  ② 重试耗尽 → 存疑复核一次（直查 /v3/product/info/list 拿 moderate_status 定生死）；
  ③ 复核仍取不到 → failed 且 error_code=OZON_STATUS_UNAVAILABLE（状态不可得），
     绝不伪造成 Ozon 拒绝。

本文件锁死假失败路径：
  - 瞬时异常首次命中绝不直接 failed（旧行为：任何非 404 OzonError → 立即 failed）
  - 网络裸异常（Timeout，ozon_client 不包装）绝不外抛进兜底 except 直接 failed
  - 非预期结构（items 非 list / 响应非 dict）视同空回传 → timeout/pending，不失败
  - 404 回退后首次空读绝不立即 PRODUCT_NOT_FOUND（有界重试后恢复 → 成功）
  - 存疑复核 approved → 成功出口；rejected → 真实拒绝出口；pending → 软出口
  - 复核不可得 → OZON_STATUS_UNAVAILABLE（错误信息写明「状态不可得」）

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_ozon_status_empty_poll.py -q
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.ozon_errors import OzonNotFoundError, OzonServerError

FAKE_RUNTIME = SimpleNamespace(context=None)

EP_IMPORT_INFO = "/v1/product/import/info"
EP_INFO_LIST = "/v3/product/info/list"


def _state(**kw) -> SimpleNamespace:
    base = dict(
        ozon_task_id="123", product_id="",
        ozon_payload={"items": [{"offer_id": "x_0"}]},
        ozon_client_id="5371047", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
        pricing_info={}, moderation_retry_count=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _patch_ozon_post_seq(import_info_seq=None, info_list_seq=None):
    """patch ozon_status_node.ozon_post：按 endpoint 分发 + 序列消费。

    序列项 = dict（成功返回体）或 Exception 实例（抛出）。
    长度 >1 时逐项消费；长度 ==1 时永远重复该项（「每次都这样」语义）。
    返回 (patcher, calls)；calls 记录 (endpoint, body)。
    """
    from graphs.nodes import ozon_status_node as mod

    calls: list = []
    seqs = {
        EP_IMPORT_INFO: list(import_info_seq or []),
        EP_INFO_LIST: list(info_list_seq or []),
    }

    def _fake(client_id, api_key, endpoint, body, timeout=60, **kw):
        calls.append((endpoint, body or {}))
        seq = seqs[endpoint]
        if not seq:
            resp = {}
        else:
            resp = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(resp, Exception):
            raise resp
        return resp

    return mock.patch.object(mod, "ozon_post", side_effect=_fake), calls


def _silent_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


APPROVED_ITEM = {"items": [{
    "id": 5812496806, "offer_id": "x_0",
    "statuses": {"validation_status": "success", "is_created": True,
                 "moderate_status": "approved"},
    "errors": [],
}]}


# ============================ 分层①: 瞬时异常有界重试 ============================

def test_transient_5xx_never_fails_immediately(monkeypatch):
    """瞬时 5xx 重试期内恢复 → 正常拿到 approved（旧代码首次 OzonError 即 failed）。"""
    _silent_sleep(monkeypatch)
    monkeypatch.setenv("OZON_STATUS_TRANSIENT_RETRIES", "3")
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="123", product_id="5812496806")
    patcher, calls = _patch_ozon_post_seq(
        import_info_seq=[
            OzonServerError("upstream 502", status_code=502),
            OzonServerError("upstream 502", status_code=502),
            {"result": {"items": [{"offer_id": "x_0", "product_id": 5812496806,
                                   "status": "imported"}]}},
        ],
        info_list_seq=[{"items": []}, APPROVED_ITEM],
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "imported"
    assert out.moderation_status == "approved"
    assert out.upload_status == "success"
    assert out.error_code != "OZON_STATUS_UNAVAILABLE"
    assert any(EP_INFO_LIST in u for u, _ in calls)


# ==================== 分层②③: 重试耗尽 → 存疑复核 → 状态不可得 ====================

def test_transport_timeout_exhausted_then_arbitration_approved(monkeypatch):
    """网络超时重试耗尽 → 存疑复核读到 approved → 成功出口（卡真实存活，不失败）。"""
    _silent_sleep(monkeypatch)
    monkeypatch.setenv("OZON_STATUS_TRANSIENT_RETRIES", "1")
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="123", product_id="5812496806")
    patcher, calls = _patch_ozon_post_seq(
        import_info_seq=[requests.exceptions.Timeout("read timeout")],
        info_list_seq=[APPROVED_ITEM],
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "imported"
    assert out.moderation_status == "approved"
    assert out.upload_status == "success"
    assert out.error_code == ""  # 成功出口无错误码（非 OZON_STATUS_UNAVAILABLE）
    # import/info 至少 2 次（重试 1 次 + 耗尽 1 次），复核走 info/list
    assert sum(1 for u, _ in calls if EP_IMPORT_INFO in u) >= 2
    assert any(EP_INFO_LIST in u for u, _ in calls)


def test_transport_timeout_exhausted_status_unavailable(monkeypatch):
    """网络超时重试耗尽 + 复核不可得 → failed 但错误码=OZON_STATUS_UNAVAILABLE（状态不可得，非伪造拒绝）。"""
    _silent_sleep(monkeypatch)
    monkeypatch.setenv("OZON_STATUS_TRANSIENT_RETRIES", "1")
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="123", product_id="")
    patcher, calls = _patch_ozon_post_seq(
        import_info_seq=[requests.exceptions.ConnectionError("conn reset")],
        info_list_seq=[],  # 无候选 pid → 复核不发起
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "failed"
    assert out.error_code == "OZON_STATUS_UNAVAILABLE"
    assert "状态不可得" in out.error_message
    # 绝不伪造成 Ozon 拒绝/校验失败语义
    assert out.moderation_status != "validation_failed"
    assert out.failed_stage == "ozon_status"
    # product_id 缺失时回退读 ozon_task_id（数字）→ 复核仍会尝试（查无 → 不可得）


def test_catchall_unexpected_exception_arbitrates_approved(monkeypatch):
    """漏网异常（非 Ozon/非 requests）→ 兜底先存疑复核，复核 approved → 成功出口。"""
    _silent_sleep(monkeypatch)
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="123", product_id="5812496806")
    patcher, _calls = _patch_ozon_post_seq(
        import_info_seq=[RuntimeError("unexpected boom")],
        info_list_seq=[APPROVED_ITEM],
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "imported"
    assert out.moderation_status == "approved"
    assert out.upload_status == "success"


def test_catchall_unexpected_exception_status_unavailable(monkeypatch):
    """漏网异常 + 复核不可得 → OZON_STATUS_UNAVAILABLE（旧代码此处是裸 str(e) failed）。"""
    _silent_sleep(monkeypatch)
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="123", product_id="")
    patcher, _calls = _patch_ozon_post_seq(
        import_info_seq=[RuntimeError("unexpected boom")],
        info_list_seq=[],
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "failed"
    assert out.error_code == "OZON_STATUS_UNAVAILABLE"
    assert "状态不可得" in out.error_message


# ============================ 分层①: 结构异常视同空回传 ============================

@pytest.mark.parametrize("bad_resp", [
    ["top-level-not-a-dict"],            # 响应顶层是 list
    {"result": None},                    # result 为 None（旧代码 AttributeError → 兜底 failed）
    {"result": {"items": "not-a-list"}},  # items 非 list
    {"result": {"items": [None, "junk"]}},  # item 非 dict（被过滤 → 空）
])
def test_structure_anomaly_treated_as_empty_never_failed(monkeypatch, bad_resp):
    """非预期响应结构 → 视同空回传 → 10 次有界轮询耗尽落 timeout/pending，绝不 failed。"""
    _silent_sleep(monkeypatch)
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="123", product_id="")
    patcher, _calls = _patch_ozon_post_seq(import_info_seq=[bad_resp])
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "timeout", f"结构异常必须落 timeout 软出口，实际 {out.status}"
    assert out.moderation_status == "pending"
    assert out.upload_status == "timeout"


# ==================== 分层①: 404 回退后空读有界重试（不再首轮即判死） ====================

def test_fallback_404_empty_then_recovered(monkeypatch):
    """404 回退后前 2 次空读（复制延迟）→ 第 3 次读到 approved → 成功（旧代码首次空读即 PRODUCT_NOT_FOUND）。"""
    _silent_sleep(monkeypatch)
    monkeypatch.setenv("OZON_STATUS_FALLBACK_EMPTY_RETRIES", "3")
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="5821877126", product_id="5821877126")
    patcher, calls = _patch_ozon_post_seq(
        import_info_seq=[OzonNotFoundError("task not found", status_code=404)],
        info_list_seq=[{"items": []}, {"items": []}, APPROVED_ITEM],
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "imported"
    assert out.moderation_status == "approved"
    info_calls = [1 for u, _ in calls if EP_INFO_LIST in u]
    assert len(info_calls) >= 3, "前两次空读必须有界重试而非立即判死"


def test_fallback_404_persistent_empty_fails_product_not_found(monkeypatch):
    """404 回退 + 持续空读（有界重试耗尽）→ PRODUCT_NOT_FOUND failed 语义保留。"""
    _silent_sleep(monkeypatch)
    monkeypatch.setenv("OZON_STATUS_FALLBACK_EMPTY_RETRIES", "2")
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="9999999999", product_id="9999999999")
    patcher, calls = _patch_ozon_post_seq(
        import_info_seq=[OzonNotFoundError("task not found", status_code=404)],
        info_list_seq=[{"items": []}],
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "failed"
    assert "PRODUCT_NOT_FOUND" in out.error_message
    assert out.moderation_status == "error"
    info_calls = [1 for u, _ in calls if EP_INFO_LIST in u]
    assert len(info_calls) == 2, "空读必须有界（耗尽即终判），不得空轮询 10 分钟"


# ============================ 存疑复核裁决三分支 ============================

def test_arbitration_rejected_is_real_failure(monkeypatch):
    """复核确认 rejected → 真实拒绝出口（VARIANT_MODERATE_REJECTED），失败语义不变。"""
    _silent_sleep(monkeypatch)
    monkeypatch.setenv("OZON_STATUS_TRANSIENT_RETRIES", "1")
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="123", product_id="5812496806")
    rejected = {"items": [{
        "id": 5812496806, "offer_id": "x_0",
        "statuses": {"validation_status": "success", "is_created": True,
                     "moderate_status": "rejected"},
        "errors": [{"code": "SOME_REAL_ERROR", "level": "ERROR_LEVEL_ERROR",
                    "message": "bad attr"}],
    }]}
    patcher, _calls = _patch_ozon_post_seq(
        import_info_seq=[OzonServerError("upstream 500", status_code=500)],
        info_list_seq=[rejected],
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "error"
    assert out.error_code == "VARIANT_MODERATE_REJECTED"
    assert out.moderation_status == "error"
    assert out.failed_stage == "ozon_status"
    assert "存疑复核确认审核被拒" in out.error_message


def test_arbitration_pending_is_soft_exit(monkeypatch):
    """复核读到 in-moderating（审核进行中）→ pending 软出口，绝不 failed。"""
    _silent_sleep(monkeypatch)
    monkeypatch.setenv("OZON_STATUS_TRANSIENT_RETRIES", "1")
    from graphs.nodes.ozon_status_node import ozon_status_node

    state = _state(ozon_task_id="123", product_id="5812496806")
    moderating = {"items": [{
        "id": 5812496806, "offer_id": "x_0",
        "statuses": {"validation_status": "success", "is_created": True,
                     "moderate_status": "in-moderating"},
        "errors": [],
    }]}
    patcher, _calls = _patch_ozon_post_seq(
        import_info_seq=[OzonServerError("upstream 500", status_code=500)],
        info_list_seq=[moderating],
    )
    with patcher:
        out = ozon_status_node(state, None, FAKE_RUNTIME)

    assert out.status == "pending"
    assert out.moderation_status == "pending"
    assert out.upload_status == "pending"
    assert out.status != "failed"


# ==================== completed 终态残留 failed_stage 剥离测试见 ====================
# tests/test_failed_stage_v073.py 第 8 节（_completed_persist_view）——该文件是
# failed_stage 教义（v0.73 归零纪律）的权威测试位。


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                if name.startswith("test_structure_anomaly"):
                    # parametrize 用例直接跑参数化会缺 fixture——此处跳过（pytest 入口为准）
                    print(f"SKIP {name} (parametrized)")
                    continue
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
