"""v0.67: 上架结果留存分析表 writer（listing_result_log）——每任务一行事实留存。

在 task_processor 三终态（failed/rejected/completed）挂点调用，把 GraphInput(payload)
+ GraphOutput(graph_result) 的关键事实沉淀到 listing_result_log（append-only，
upsert by task_db_id 幂等），替代「completed 7 天物理删」的数据丢失
（main.py cleanup 注释自认该有 archive 表）。

纪律（对齐 category_match_log._log_match_attempt / attr_match_log.log_attr_match）：
- 非致命：整函数 try/except，DB 不可用/缺列/类型异常 → logger.warning 返回，绝不阻塞终态落库
- task_db_id（= ozon_product_tasks.id = config.configurable.thread_id）为空 → 跳过
- ⚠️ title_ru/offer_id 为预留列（后续 fetch_back 扩展再填，本 writer 恒空）
- ⚠️ match_layer/match_confidence 待 GraphOutput 透出 category_match_meta 后接入
  （当前 GraphOutput 仅 errors/notice 两字段，本 writer 该两列留空）
- description_category_id/type_id/类目双语路径仅在信封带 Skill 类目（follow/discover）
  时回填；1688 graph 管线最终类目由 worker 内部决定、不在 GraphOutput 契约内 → 留空
  （后续扩展 GraphOutput 或从 product_task_index 回填）
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

_logger = logging.getLogger(__name__)


# ============================================================
# 纯提取 helpers（便于单测）
# ============================================================

def _envelope(payload: Optional[dict]) -> dict:
    return ((payload or {}).get("envelope") or {}) if isinstance(payload, dict) else {}


def _draft(payload: Optional[dict]) -> dict:
    env = _envelope(payload)
    d = env.get("draft") or {}
    return d if isinstance(d, dict) else {}


def _extensions(payload: Optional[dict]) -> dict:
    env = _envelope(payload)
    ext = env.get("extensions") or {}
    return ext if isinstance(ext, dict) else {}


def _source(payload: Optional[dict]) -> dict:
    env = _envelope(payload)
    s = env.get("source") or {}
    return s if isinstance(s, dict) else {}


def pipeline_source(payload: Optional[dict]) -> str:
    """graph/discover/follow 三态判定。

    extensions.follow_sell=True → follow_type 存在（真跟卖 hand/api）→ follow；
    follow_sell=True 但无 follow_type（skill discover 信封）→ discover；否则 graph。
    """
    ext = _extensions(payload)
    if ext.get("follow_sell"):
        return "follow" if ext.get("follow_type") else "discover"
    return "graph"


def source_category_path_of(payload: Optional[dict]) -> str:
    """1688 源类目路径（三源兜底，对齐 assemble/learning 写法）。"""
    draft = _draft(payload)
    src = _source(payload)
    return str(
        draft.get("source_category_path")
        or draft.get("source_category")
        or src.get("source_category_path")
        or ""
    ).strip()


def source_category_leaf_of(path: str) -> str:
    """路径末段（去掉分隔符）；空路径 → 空串。"""
    if not path:
        return ""
    # 兼容 >、/、中文顿号/空格分隔
    import re
    parts = [p.strip() for p in re.split(r"[>/>|]+", path) if p.strip()]
    return parts[-1] if parts else path.strip()


def source_category_id_of(payload: Optional[dict]) -> Optional[int]:
    draft = _draft(payload)
    src = _source(payload)
    raw = draft.get("source_category_id") or src.get("category_id") or src.get("source_category_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _ozon_category_ids(payload: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    """信封带 Skill 类目（follow/discover）时回填 dc/tp；否则 (None, None)。"""
    oc = _draft(payload).get("ozon_category") or {}
    if not isinstance(oc, dict):
        return None, None
    try:
        dc = int(oc.get("description_category_id") or 0) or None
    except (TypeError, ValueError):
        dc = None
    try:
        tp = int(oc.get("type_id") or 0) or None
    except (TypeError, ValueError):
        tp = None
    return dc, tp


def derive_final_status(graph_result: dict) -> str:
    """终态归因：approved / declined / pending / failed。

    - upload_status=success 且 moderation_status=approved → approved
    - moderation_status=error/rejected/declined 或 errors 非空 或 upload=rejected_unfixable
      → declined（含 upload=success 的矛盾态，对齐 product_summary._ozon_status：
      error 即 declined；对齐 task_processor._mod_rejected 判定）
    - upload_status in (success, imported) 且 moderation 非 approved → pending
    - 其他 → failed
    """
    up = str(graph_result.get("upload_status") or "")
    mod = str(graph_result.get("moderation_status") or "")
    errors = graph_result.get("errors") or []
    if not isinstance(errors, list):
        errors = []
    if up == "success" and mod == "approved":
        return "approved"
    if mod in ("error", "rejected", "declined") or errors or up == "rejected_unfixable":
        return "declined"
    if up in ("success", "imported") and mod != "approved":
        return "pending"
    return "failed"


# ============================================================
# 写入口
# ============================================================

def write_listing_result_log(
    payload: dict,
    graph_result: dict,
    task_db_id: str,
    tenant_id: str,
    retry_count: int = 0,
) -> None:
    """写入 listing_result_log 一行（upsert by task_db_id，幂等；非致命）。

    Args:
        payload: GraphInput（含 envelope.draft/source/extensions）
        graph_result: GraphOutput dict（终态，task_processor 落库前）
        task_db_id: ozon_product_tasks.id（str(uuid)，= config thread_id）
        tenant_id: 用户 ID（token 派生）
        retry_count: 任务累计重试次数
    """
    try:
        task_db_id = str(task_db_id or "").strip()
        if not task_db_id:
            _logger.debug("listing_result_log skip: task_db_id empty")
            return

        gr = graph_result if isinstance(graph_result, dict) else {}
        draft = _draft(payload)
        src = _source(payload)

        # ── 1688 侧 ──
        src_cat_path = source_category_path_of(payload)
        dc, tp = _ozon_category_ids(payload)
        # ✅ v0.67.1 wave②: graph 管线真值优先（GlobalState 图终态= N4/R4 同步后终值，
        # 经 GraphOutput 透传）；空值回落信封（follow/discover 信封仍是权威——跟卖
        # UPDATE 由 Ozon 保留原卡类目）。wave 实证：graph 单信封是 skill 猜测。
        _g_dc = str(gr.get("description_category_id") or "").strip()
        _g_tp = str(gr.get("type_id") or "").strip()
        if _g_dc.isdigit() and _g_tp.isdigit() and int(_g_dc) > 0 and int(_g_tp) > 0:
            dc, tp = int(_g_dc), int(_g_tp)
        _meta = gr.get("category_match_meta") if isinstance(gr.get("category_match_meta"), dict) else {}
        _g_weight = gr.get("final_weight_g")
        _g_dims = gr.get("final_dims_mm") if isinstance(gr.get("final_dims_mm"), dict) else {}

        # ── Ozon 侧 ──
        pi = gr.get("pricing_info") or {}
        if not isinstance(pi, dict):
            pi = {}
        errors_raw = gr.get("errors") or []
        if not isinstance(errors_raw, list):
            errors_raw = []

        def _f(v, default=None):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        def _i(v, default=None):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        ozon_product_id = gr.get("product_id")
        if ozon_product_id is None:
            # 多 SKU/变体：uploaded_products 第一条的 product_id
            ups = gr.get("uploaded_products") or []
            if isinstance(ups, list) and ups:
                first = ups[0] if isinstance(ups[0], dict) else {}
                ozon_product_id = first.get("product_id")

        # ── 类目双语路径（信封带 dc/tp 才查；查不到留空）──
        category_path_zh = ""
        category_path_ru = ""
        if dc and tp:
            from storage.database.db import get_session
            from storage.database.shared.model import CategoryTreeNode
            session = get_session()
            try:
                for lang in ("ZH_HANS", "RU"):
                    row = session.execute(
                        select(CategoryTreeNode.full_path).where(
                            CategoryTreeNode.description_category_id == dc,
                            CategoryTreeNode.type_id == tp,
                            CategoryTreeNode.language == lang,
                        ).limit(1)
                    ).first()
                    if row and row[0]:
                        if lang == "ZH_HANS":
                            category_path_zh = str(row[0])
                        else:
                            category_path_ru = str(row[0])
            finally:
                session.close()

        # 错误信息截断
        error_message = str(gr.get("error_message") or "")[:2000]
        error_code = str(gr.get("error_code") or "")[:50]

        now = datetime.datetime.now(datetime.timezone.utc)
        values: Dict[str, Any] = {
            "task_db_id": task_db_id,
            "tenant_id": str(tenant_id or "")[:50],
            "ozon_client_id": str((payload or {}).get("ozon_client_id") or "")[:50] or None,
            "completed_at": now,
            # 1688 侧
            "source_url": str(draft.get("purchase_url") or "")[:2000] or None,
            "source_item_id": str(draft.get("item_id") or "")[:2000] or None,
            "source_sku_id": str(draft.get("sku_id") or "")[:2000] or None,
            "source_title_cn": str(draft.get("title") or "")[:2000] or None,
            "supplier": str(draft.get("supplier") or "")[:2000] or None,
            "source_category_path": src_cat_path[:2000] or None,
            "source_category_leaf": source_category_leaf_of(src_cat_path)[:500] or None,
            "source_category_id": source_category_id_of(payload),
            "purchase_cost": _f(draft.get("purchase_cost")),
            # Ozon 侧
            "ozon_product_id": str(ozon_product_id or "")[:50] or None,
            "offer_id": None,
            "title_ru": None,
            "description_category_id": dc,
            "type_id": tp,
            "category_path_zh": category_path_zh or None,
            "category_path_ru": category_path_ru or None,
            "price": _f(pi.get("price")),
            "old_price": _f(pi.get("old_price")),
            "promo_price": _f(pi.get("promo_price")),
            "currency_code": str(pi.get("currency_code") or "")[:10] or None,
            "weight_g": (_i(_g_weight) if _g_weight else None) or _i(draft.get("weight")),
            "dims_mm": _g_dims or (draft.get("dimensions") or None),
            "variants": draft.get("variants") or [],
            # 结果与归因
            "final_status": derive_final_status(gr),
            "moderation_status": str(gr.get("moderation_status") or "")[:30] or None,
            "error_code": error_code or None,
            "error_message": error_message or None,
            "errors": errors_raw,
            # ✅ v0.67.1 wave①: 各轮审核拒绝原文累积（decline_errors 全链透传落点；
            # 含俄语 texts 原文，errors 列只有 code 级结构、二者互补）
            "moderation_texts": (gr.get("decline_errors") or None),
            "retry_count": _i(retry_count, 0),
            # ✅ v0.67.1 wave②: GraphOutput 透出 category_match_meta 后接入
            # （match_layer ∈ Skill/L0/L1/R2b；无 meta 不编造）
            "match_layer": str(_meta.get("match_layer") or "")[:10] or None,
            "match_confidence": (float(_meta["confidence"])
                                 if isinstance(_meta.get("confidence"), (int, float)) else None),
            "pipeline_source": pipeline_source(payload),
            "pricing_info": pi or None,
            "fetch_back_summary": gr.get("fetch_back_summary") or gr.get("fetch_back_result") or None,
        }

        from storage.database.db import get_session
        from storage.database.shared.model import ListingResultLog

        session = get_session()
        try:
            # 全字段覆盖（除 id/created_at）——同 task 行重跑/终态修正时保留最新事实
            stmt = pg_insert(ListingResultLog).values(**values).on_conflict_do_update(
                index_elements=["task_db_id"],
                set_={k: v for k, v in values.items() if k != "task_db_id"},
            )
            session.execute(stmt)
            session.commit()
        finally:
            session.close()
        _logger.info(
            "✅ listing_result_log 留存: task=%s status=%s pipeline=%s dc=%s tp=%s",
            task_db_id, values.get("final_status"), values.get("pipeline_source"), dc, tp,
        )
    except Exception as e:
        _logger.warning("listing_result_log 写入失败（非致命）: %s", e)
