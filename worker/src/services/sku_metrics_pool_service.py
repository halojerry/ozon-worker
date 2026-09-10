"""数据池 v1 服务：贡献收包合并 + 读侧补采指令（对标 goldminer 读-回馈 + 毛子 sku3 指令式）。

红线：只存指标；sku 归一 int64 无 _0 后缀（what_to_sell 契约，同 worker
ozon_session_client.build_what_to_sell_payload 的剥离规则）；归因数组 cap 防滥用。
category_name_zh 让卡片显示「美容和卫生 > 洗发水」式官方中文名（上品帮卡片对标）。
"""
from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

SKU_SYNC_MAX_ITEMS = 12
SKU_QUERY_MAX = 50
_SOURCE_CAP = 10
_CONTRIB_CAP = 10
STALE_DAYS = 14


def _norm_sku(raw) -> int | None:
    try:
        digits = str(raw).split("_", 1)[0].strip()
        return int(digits) if digits.isdigit() else None
    except (TypeError, ValueError):
        return None


def _cap_append(existing: list, value: str | None, cap: int) -> list:
    if not value or value in (existing or []):
        return existing or []
    return (list(existing or []) + [str(value)])[-cap:]


def _upsert_one(session, model, sku: int, item: dict, sales, variant,
                source_company_id, contributed_by) -> bool:
    """单条 upsert：SAVEPOINT 包住 select+merge+add+flush（B2a-3 原子化）。

    旧实现逐条 SELECT-then-INSERT + 末尾单 commit——并发同 sku 两请求，后
    commit 方 IntegrityError 整批（≤12 条）丢失。现在唯一键冲突在 SAVEPOINT
    内的 flush 处暴露：回滚该 savepoint 后 re-select 已存在行再合并一次
    （有界重试 1 次），仍冲突计 skipped 不炸整批。
    """
    from sqlalchemy.exc import IntegrityError
    for _attempt in (1, 2):
        try:
            with session.begin_nested():
                row = session.query(model).filter_by(sku=sku).one_or_none()
                if row is None:
                    row = model(sku=sku)
                    session.add(row)
                if isinstance(sales, dict):
                    row.sales_payload = sales
                if isinstance(variant, dict):
                    row.variant_payload = variant
                if item.get("category_dc"):
                    row.category_dc = int(item["category_dc"])
                if item.get("category_tp"):
                    row.category_tp = int(item["category_tp"])
                row.source_company_ids = _cap_append(row.source_company_ids, source_company_id, _SOURCE_CAP)
                row.contributed_by_token_ids = _cap_append(row.contributed_by_token_ids, contributed_by, _CONTRIB_CAP)
                # 冲突必须在 SAVEPOINT 内显式暴露（flush），否则拖到末尾单 commit
                # 才爆，整批报废——正是本函数要根治的旧行为
                session.flush()
            return True
        except IntegrityError:
            if _attempt == 2:
                break
    logger.warning("sku_metrics_pool upsert 写入冲突放弃 sku=%s（有界重试耗尽）", sku)
    return False


def upsert_seller_sync_items(session, items, *, source_company_id=None, contributed_by=None):
    from storage.database.shared.model import SkuMetricsPool
    accepted = skipped = 0
    for item in (items or [])[:SKU_SYNC_MAX_ITEMS]:
        if not isinstance(item, dict):
            skipped += 1
            continue
        sku = _norm_sku(item.get("sku"))
        if sku is None:
            skipped += 1
            continue
        sales = item.get("sales_payload") or None
        variant = item.get("variant_payload") or None
        if sales is None and variant is None:
            skipped += 1
            continue
        if not _upsert_one(session, SkuMetricsPool, sku, item, sales, variant,
                           source_company_id, contributed_by):
            skipped += 1
            continue
        accepted += 1
    session.commit()
    return {"accepted": accepted, "skipped": skipped}


def _category_name_zh(session, dc, tp) -> str | None:
    if not dc or not tp:
        return None
    from sqlalchemy import text
    # ⚠️ SQLAlchemy text() 裸 ::cast 陷阱——一律 CAST(:bind AS type)，见记忆 sqlalchemy-jsonb-cast-trap
    rows = session.execute(text(
        "SELECT node_name, full_path FROM category_tree_nodes "
        "WHERE description_category_id = CAST(:dc AS bigint) AND type_id = CAST(:tp AS bigint) "
        "AND language = 'ZH_HANS' LIMIT 1"
    ), {"dc": int(dc), "tp": int(tp)}).fetchall()
    if not rows:
        return None
    return " > ".join(p for p in (rows[0].full_path or "").split("/") if p) or rows[0].node_name


def query_sku_metrics(session, skus):
    from storage.database.shared.model import SkuMetricsPool
    clean = [s for s in (_norm_sku(x) for x in (skus or [])[:SKU_QUERY_MAX]) if s]
    if not clean:
        return []
    stale_cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=STALE_DAYS)
    out = []
    for row in session.query(SkuMetricsPool).filter(SkuMetricsPool.sku.in_(clean)).all():
        updated = row.updated_at
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=datetime.timezone.utc)
        out.append({
            "sku": row.sku,
            "sales_payload": row.sales_payload,
            "variant_payload": row.variant_payload,
            "category_dc": row.category_dc,
            "category_tp": row.category_tp,
            "category_name_zh": _category_name_zh(session, row.category_dc, row.category_tp),
            "needs_sales_sync": row.sales_payload is None or updated is None or updated < stale_cut,
            "needs_variant_sync": row.variant_payload is None,
            "updated_at": updated.isoformat() if updated else None,
        })
    return out
