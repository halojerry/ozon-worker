"""v0.69 T0.3: 类目闸阻断 → 自动入采集箱（带 top-3 推荐类目），代替无声 failed。

生产背景：R2b 并列候选 LLM 弃权/俄语标题×中文候选树弃权 → 全阻断，用户拍板
「采纳不了的自动入采集箱（带 top-3 候选推荐）代替无声 failed」。

职责边界（改前必读）：
- 本模块是**唯一**的「阻断入箱」写侧；follow_sell_import_node 门控仲裁**不**建
  draft（仲裁不过置空汇入 assemble 全闸，入箱只在 assemble 层做一次）。
- R1 敏感 veto 出口**不**入箱（需资质类不自动推荐）；标题为空出口不入箱
  （create_draft 对缺 title 必 400，物理不可用）。
- draft_service.create_draft 是服务层唯一创建入口（凭证剥离/字段校验/图片镜像
  全复用）；本模块不做 SQL INSERT，只做幂等查询 + payload 组装。
- 幂等键：tenant_id + payload->'draft'->>'item_id' 且**无任何 draft_submissions
  行**（= 采集箱待人工/pending；已提交过的 draft 视为用户已在处理，重复阻断时
  建新行携带最新推荐）。product_drafts 无状态列，submission 归零即 pending。
- 入箱失败必须非致命（调用方 try/except + warning，不阻断任务 failed 落库）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# ── v0.69 T0.3: R2b 置信度分层采纳阈值（_r2b_confirm_adoption 消费，可单测）──
# 同大类（LLM 选中候选与池内源词命中锚点同顶层大类）→ 现行第四段的强化版阈值；
# 跨大类 → 更高门槛（解锁双语正确选择如水暖风机，域守卫靠高置信 + R1 池剔除兜底）。
R2B_ADOPT_CONF_SAME_TOP = 0.5
R2B_ADOPT_CONF_CROSS_TOP = 0.75

# 入箱 draft 的 source 标记（product_drafts.source 为自由文本：skill/webui/csv 之外新增）
BLOCKED_DRAFT_SOURCE = "worker"


def category_recommendations(candidates: list, limit: int = 3) -> list[dict]:
    """候选 → 推荐类目 top-N（纯函数）：[{description_category_id, type_id, name, confidence}]。

    confidence 取候选 similarity（文本相似度，非 LLM 置信度——候选无 LLM 置信度时
    这是唯一可溯源信号）；无效 dc（<=0/非数字）剔除。
    """
    out: list[dict] = []
    for c in (candidates or []):
        if len(out) >= limit:
            break
        if not isinstance(c, dict):
            continue
        try:
            dc = int(c.get("description_category_id") or 0)
            tp = int(c.get("type_id") or 0)
        except (TypeError, ValueError):
            continue
        if dc <= 0:
            continue
        try:
            sim = round(float(c.get("similarity") or 0), 4)
        except (TypeError, ValueError):
            sim = 0.0
        out.append({
            "description_category_id": dc,
            "type_id": tp,
            "name": str(c.get("full_path") or c.get("category_path")
                        or c.get("node_name") or ""),
            "confidence": sim,
        })
    return out


def find_reusable_blocked_draft(tenant_id: str, item_id: str) -> Optional[str]:
    """幂等查询：同 tenant + 同 item_id 且从未提交过的 draft → 返回其 id（否则 None）。

    「pending 状态」口径：product_drafts 无状态列，draft_submissions 无行 = 未上架
    待人工（v0.69 T0.3 拍板）；已有 submission（含 failed）→ 视为用户已在处理，
    不复用（下次阻断建新行带最新推荐）。
    """
    item_id = str(item_id or "").strip()
    if not tenant_id or not item_id:
        return None
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT d.id FROM product_drafts d "
                "WHERE d.tenant_id = :t "
                "  AND d.payload->'draft'->>'item_id' = :iid "
                "  AND NOT EXISTS ("
                "      SELECT 1 FROM draft_submissions s WHERE s.draft_id = d.id) "
                "ORDER BY d.updated_at DESC LIMIT 1"
            ), {"t": str(tenant_id), "iid": item_id}).fetchone()
        return str(row[0]) if row else None
    except Exception as e:
        logger.warning("blocked_draft 幂等查询失败（按新建处理）: %s", e)
        return None


def create_blocked_draft(tenant_id: str, envelope: dict, candidates: list,
                         blocked_reason: str) -> Optional[dict]:
    """阻断商品入采集箱（幂等）。

    Args:
        tenant_id: 租户（state.user_id）。
        envelope: 原始信封 {draft, source, extensions}（无凭证；裸 draft dict 也兼容，
            自动补 {"draft": ...} 包装）。
        candidates: 类目候选（sim 序）→ 取 top-3 作推荐。
        blocked_reason: 阻断原因（人读，落 extensions.blocked_reason）。

    Returns:
        {"draft_id": str, "reused": bool, "top1_name": str, "top1_confidence": float}
        建不了（无 title 被服务层 400 / PG 异常等）→ None（调用方按非致命处理）。
    """
    if not isinstance(envelope, dict) or not envelope:
        return None
    if not envelope.get("draft"):
        envelope = {"draft": envelope}
    recs = category_recommendations(candidates, limit=3)
    draft_sec = envelope.get("draft") or {}
    item_id = str(draft_sec.get("item_id") or draft_sec.get("sku_id") or "").strip()

    # 幂等：已存在 pending（未提交）同类 draft → 复用不重复建
    existing = find_reusable_blocked_draft(tenant_id, item_id) if item_id else None
    if existing:
        logger.info("📥 blocked_draft 幂等复用: tenant=%s item=%s draft=%s",
                    tenant_id, item_id[:40], existing)
        return _box_info(existing, reused=True, recs=recs)

    payload = dict(envelope)
    payload["extensions"] = {
        **(envelope.get("extensions") or {}),
        "category_recommendations": recs,
        "blocked_reason": str(blocked_reason or "")[:500],
        "blocked_stage": "category_match",
    }
    # 延迟 import（服务层连带 fastapi/credentials 依赖）；失败非致命由调用方兜底
    from services.draft_service import create_draft
    row = create_draft(tenant_id, {"envelope": payload, "source": BLOCKED_DRAFT_SOURCE})
    draft_id = str((row or {}).get("id") or "")
    if not draft_id:
        return None
    logger.info("📥 blocked_draft 已入采集箱: draft=%s tenant=%s item=%s recs=%d",
                draft_id, tenant_id, item_id[:40], len(recs))
    return _box_info(draft_id, reused=False, recs=recs)


def _box_info(draft_id: str, reused: bool, recs: list) -> dict:
    top1 = recs[0] if recs else None
    return {
        "draft_id": str(draft_id),
        "reused": bool(reused),
        "top1_name": str(top1.get("name") or "") if top1 else "",
        "top1_confidence": float(top1.get("confidence") or 0.0) if top1 else 0.0,
    }


def format_box_notice(info: Optional[dict]) -> str:
    """入箱结果 → 人读 notice 片段（空 info → 空串，调用方直接拼接）。"""
    if not info or not info.get("draft_id"):
        return ""
    if info.get("top1_name"):
        return (f"已入采集箱 draft_id={info['draft_id']}，"
                f"推荐类目 Top1={info['top1_name']}(置信度 {float(info['top1_confidence']):.2f})")
    return f"已入采集箱 draft_id={info['draft_id']}（无候选推荐）"
