"""Decision review log (D3 L2) — 关键判定不静默消失的审计落盘。

每次 1688 匹配的关键决策（护栏 block / 自动通过 / 人工 approve / 人工拒绝）
追加一行 JSON 到 ``data/review_log.jsonl``。模块级 Lock 保证 match_selected
P2 并行识图路径（多线程 _process_match）写入线程安全。任何异常绝不 raise
（日志失败不得影响主流程）。

Record shape（标准字段，全部可选，ts 由本函数统一写入）:
    ts, task_id, product_id, ozon_title, match_title, match_url,
    confidence, badge_eff, score, reject_reason, decision, image_urls[]

Usage:
    from scripts.lib.review_log import write_review_record
    write_review_record({"product_id": "123", "ozon_title": "...",
                         "decision": "agent_reject", "reject_reason": "..."})
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# data 目录定位与 _save_discovery_log 同约定：skill/scripts/lib/review_log.py
# → parent.parent.parent = skill/（SKILL_ROOT），review_log.jsonl 落在 skill/data/。
SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_LOG_PATH = SKILL_ROOT / "data" / "review_log.jsonl"

# ⚠️ match_selected P2 并行识图路径：_process_match / _pick_best_match 可能在
# worker 线程并发写——模块级 Lock 保证 JSONL 每行原子追加（不交错损坏）。
_lock = threading.Lock()


def write_review_record(record: dict[str, Any] | None) -> None:
    """Append one decision record as a JSON line to ``data/review_log.jsonl``.

    - ``ts`` 由本函数统一写入（ISO 8601），调用方可省略
    - 非 dict / None / 不可序列化 → 静默 no-op
    - 任何 IO/序列化异常 → fail-open（日志失败绝不 raise）
    """
    if not isinstance(record, dict) or not record:
        return
    rec = dict(record)
    if "ts" not in rec:
        rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        with _lock:
            REVIEW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(REVIEW_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - 审计日志失败必须静默
        logger.debug("review_log 写入失败（静默降级）: %s", exc)
