"""属性匹配审计日志 writer（v0.40 Phase 5）。

每次属性解析写入 attr_match_log 审计表，用于：
- attempted_fill_rate：每次改动即时回归（should_fill 已填数/应填数）
- verified_fill_rate：月度校准（fetch-back 回读确认 vs 发送侧）
- A/B 评估与误配复盘（照抄 category_match_log 先例）

纪律：
- 非致命：DB 不可用/写入失败 → warning 返回，绝不阻塞管线
- task_id 为空 → 跳过（同 category_match_log._log_match_attempt）
- source_value 截断 500、candidates_json 截断 15 条
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)


def log_attr_match(
    task_id: str,
    attr_id: int,
    attr_name: str,
    source_value: str,
    status: str,
    match_layer: str,
    dictionary_value_id: int = 0,
    confidence: float = 0.0,
    source: str = "",
    should_fill: bool = True,
    candidates: Optional[List[dict]] = None,
) -> None:
    """写入 attr_match_log 一行（非致命，失败仅 warning）。

    Args:
        task_id: 任务 ID（空则跳过）
        attr_id: Ozon 属性 ID
        attr_name: 属性名（中/俄）
        source_value: 1688 源值
        status: matched/llm_disambiguated/skipped/no_source/aspect_skipped
        match_layer: exact/contains/jieba/synonym/unique/hazard_safe/llm/none
        dictionary_value_id: 命中字典值 ID（未命中 0）
        confidence: 置信度 0-1
        source: provenance（learned_approved/fetch_back_corrected/...）
        should_fill: 是否应填（False=系统生成/强制默认，不计入填满率分母）
        candidates: 存活候选（截断 15 条）
    """
    if not task_id:
        _logger.debug("attr_match_log skip: task_id empty")
        return
    try:
        import psycopg2 as _pg
        from storage.database.db import get_db_url as _gdu
        conn = _pg.connect(_gdu())
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO attr_match_log (task_id, attr_id, attr_name, source_value,
                    candidate_count, status, match_layer, confidence,
                    dictionary_value_id, source, should_fill, candidates_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (
                task_id,
                int(attr_id or 0),
                str(attr_name or "")[:200],
                str(source_value or "")[:500],
                len(candidates or []),
                str(status or "")[:30],
                str(match_layer or "")[:30],
                float(confidence or 0.0),
                int(dictionary_value_id or 0),
                str(source or "")[:50],
                bool(should_fill),
                json.dumps([{
                    "id": c.get("id") or c.get("dictionary_value_id") or 0,
                    "value": str(c.get("value") or "")[:100],
                } for c in (candidates or [])[:15]], ensure_ascii=False),
            ))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        _logger.warning("attr_match_log write failed (non-fatal): %s", e)


def compute_attempted_fill_rate(
    schema: List[dict],
    filled_ids: List[int],
) -> Dict[str, Any]:
    """计算 attempted_fill_rate = 应填已填 / 应填总数（Phase 0 compute_gap 复用）。

    返回 {should_fill, filled, attempted_fill_rate}。系统生成属性不计入分母。
    """
    from utils.attr_gap import compute_gap  # type: ignore
    report = compute_gap(schema, {}, filled_ids)
    return {
        "should_fill": report.should_fill,
        "filled": report.filled,
        "attempted_fill_rate": report.attempted_fill_rate,
    }
