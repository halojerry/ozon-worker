# -*- coding: utf-8 -*-
"""
v0.77.1 — T0.4 无商品佐证闸取证三元组（error_code/failed_stage/error_message）。

生产实证（2026-09-18 升级核验）：46 条「任务完成但未创建 Ozon 商品(product_id
缺失)，已按失败处理」——listing_result_log 对应行 error_message/error_code **全空**。
根因：task_processor 无商品闸只写 harness 内部键 `_harness_error`（下划线前缀，
不进 listing_result_log；writer 只读 `graph_result["error_code"]/"error_message"]`，
见 utils/listing_result_log.py :240-241）→ 失败点无留痕，无法定位是「创建商品
API 失败」还是「管线没走到创建」。

契约：
- 闸命中时终态 result JSONB 必须带取证三元组：
  error_code="PRODUCT_NOT_CREATED"、failed_stage="final_product_evidence_check"、
  error_message=人话（缺失时补，不覆盖已有更具体消息）；
- writeback/notify 等既有失败链行为不变（仍 failed、_harness_error 原语义）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_no_product_error_code_v077.py -q
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# 复用 writeback 测试的 fake engine 驱动（单一事实源，不复制 harness）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_task_processor_writeback as tb  # noqa: E402


def _marker():
    """延迟导入：RED 阶段函数不存在 → 测试内 ImportError 即「特性缺失」失败信号，
    不阻塞同文件行为级用例（①）与其他文件的收集。"""
    from utils.task_processor import _mark_no_real_product_failure
    return _mark_no_real_product_failure


# ── ① 行为级：无商品佐证的「成功」图结果 → failed 终态 + 取证三元组进 result JSONB ──
def test_no_product_failure_carries_forensics():
    engine, calls, _events = tb._run_process_next({
        "upload_status": "success",   # 图执行"完成"
        "moderation_status": "",
        "product_id": None,           # 但无商品佐证 → T0.4 闸命中
    })
    sql, params = tb._terminal_update(engine)
    assert "status = 'failed'" in sql, "无商品佐证必须落 failed（T0.4 语义不变）"
    assert calls and calls[0][1] == "failed", f"writeback 应为 failed: {calls}"

    parsed = json.loads(params["result_json"])
    assert parsed.get("error_code") == "PRODUCT_NOT_CREATED", (
        f"终态 result 必须带 error_code=PRODUCT_NOT_CREATED（listing_result_log "
        f"error_code 列自动取到），实际 {parsed.get('error_code')!r}"
    )
    assert parsed.get("failed_stage") == "final_product_evidence_check", (
        f"终态 result 必须带阶段名，实际 {parsed.get('failed_stage')!r}"
    )
    assert "product_id 缺失" in str(parsed.get("error_message") or ""), (
        f"error_message 必须人话可读，实际 {parsed.get('error_message')!r}"
    )


# ── ② 纯函数：空 dict → 三元组齐全 ──
def test_marker_sets_all_three_fields():
    gr = {}
    _marker()(gr, "任务完成但未创建 Ozon 商品（product_id 缺失），已按失败处理")
    assert gr["error_code"] == "PRODUCT_NOT_CREATED"
    assert gr["failed_stage"] == "final_product_evidence_check"
    assert "product_id 缺失" in gr["error_message"]


# ── ③ 纯函数：已有更具体 error_message → 不覆盖，code/stage 照补 ──
def test_marker_preserves_existing_message():
    gr = {"error_message": "ozon_status 轮询 phase1 超时（具体现场）"}
    _marker()(gr, "generic message")
    assert gr["error_code"] == "PRODUCT_NOT_CREATED"
    assert gr["failed_stage"] == "final_product_evidence_check"
    assert gr["error_message"] == "ozon_status 轮询 phase1 超时（具体现场）"
