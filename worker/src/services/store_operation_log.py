"""店铺操作审计日志（append-only）— 写操作 helper。

todo 3：新增独立 `_write_operation_log`，供后续 todo 7 执行端点接线。
它只负责「插入 store_operation_log 一行」，不负责业务调用方逻辑（职责分离）。

与 `_set_sync_error`（store_sync_service）区别：那是 UPSERT 单店一行；
本表 append-only，一次操作插一行，无业务唯一键（靠自增 id 区分）。

关键约束（评审 M4）：
1. before 从真实来源读——优先参数；为 None 时读 ozon_products_cache（price/stock）
   或调 Ozon `/v3/product/info/list` 实拿当前状态。**绝不凭空编造 before**。
2. before 仍无来源 → 写 NULL + logger.warning（不编造）。
3. result 不依赖成功率：pending/failed 也写（append 语义）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)


def _read_before_from_cache(
    tenant_id: str, credential_id: str, target_id: str
) -> Optional[dict]:
    """从 ozon_products_cache 读当前 price/stock 作 before（本地、免 API）。

    target_id 优先按 product_id 精确匹配，退化按 offer_id 匹配。
    无命中 → 返回 None（交由 Ozon 实读兜底）。
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT price, stock FROM ozon_products_cache "
                "WHERE tenant_id=:t AND credential_id=:c "
                "AND (product_id=:tid OR offer_id=:tid) LIMIT 1"
            ),
            {"t": tenant_id, "c": credential_id, "tid": str(target_id)},
        ).fetchone()
    if row is None:
        return None
    return {"price": row.price, "stock": row.stock}


def _read_before_from_ozon(
    tenant_id: str, credential_id: str, target_id: str
) -> Optional[dict]:
    """调 Ozon `/v3/product/info/list` 实拿当前 price/stock 作 before。

    需要真实凭证（credential_service.get_decrypted）；失败/无数据 → warning + None。
    """
    from services import credential_service
    from utils.ozon_client import ozon_post

    try:
        client_id, api_key = credential_service.get_decrypted(tenant_id, credential_id)
    except Exception as exc:
        logger.warning(
            "op_log before 凭证解析失败 tenant=%s store=%s target=%s: %s",
            tenant_id, credential_id, target_id, str(exc)[:200],
        )
        return None

    body = (
        {"product_id": [int(target_id)]}
        if str(target_id).isdigit()
        else {"offer_id": [str(target_id)]}
    )
    try:
        resp = ozon_post(client_id, api_key, "/v3/product/info/list", body,
                         timeout=30, language="RU")
    except Exception as exc:
        logger.warning(
            "op_log before Ozon 实读失败 tenant=%s store=%s target=%s: %s",
            tenant_id, credential_id, target_id, str(exc)[:200],
        )
        return None

    items = (resp.get("result") or {}).get("items") or []
    if not items:
        logger.warning(
            "op_log before Ozon 实读无数据 tenant=%s store=%s target=%s（写 NULL）",
            tenant_id, credential_id, target_id,
        )
        return None
    it = items[0]
    price = it.get("price")
    price_el = None
    if isinstance(price, dict):
        price_el = price.get("price") or price.get("marketing_price")
    elif price is not None:
        price_el = price
    stock = (it.get("stocks") or {}).get("present")
    if isinstance(stock, dict):
        stock = stock.get("present")
    return {"price": price_el, "stock": stock}


def _resolve_before(
    tenant_id: str, credential_id: str, target_id: str, before: Optional[dict]
) -> Optional[dict]:
    """before 来源链：参数 → ozon_products_cache → Ozon 实读。

    全落空 → None（上层写 NULL + warning，绝不编造）。
    """
    if before is not None:
        return before
    cached = _read_before_from_cache(tenant_id, credential_id, target_id)
    if cached is not None:
        return cached
    return _read_before_from_ozon(tenant_id, credential_id, target_id)


def _write_operation_log(
    tenant_id: str,
    credential_id: str,
    store_id: str,
    operation: str,
    target_id: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    result: str = "pending",
    error: Optional[str] = None,
    operator: Optional[str] = None,
) -> None:
    """插入 store_operation_log 一行（append-only，无唯一键）。

    这是**唯一**写操作审计入口；todo 7 端点只负责业务 + 计算 after，不重复插入逻辑。
    result 不依赖成功率：pending/failed 同样落一行。

    Args:
        tenant_id: 租户（user_id）
        credential_id: 店铺凭证 UUID
        store_id: 店铺展示 ID（通常即 credential_id 字符串）
        operation: 如 "update_price"/"update_stock"/"archive"
        target_id: 目标标识（product_id 或 offer_id）
        before: 动作前快照 + 事件数据源，传 None → 走 cache/Ozon 实读；仍无 → NULL
        after: 动作后快照（调用方业务产物）
        result: pending/success/failed（不依赖成功率）
        error: 失败时的错误信息（成功为 None）
        operator: 操作者（用户/agent）
    """
    before = _resolve_before(tenant_id, credential_id, target_id, before)
    if before is None:
        logger.warning(
            "op_log before 无来源写 NULL tenant=%s store=%s target=%s op=%s",
            tenant_id, store_id, target_id, operation,
        )

    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO store_operation_log "
                "(tenant_id, credential_id, store_id, operation, target_id, "
                " before, after, result, error, operator) "
                "VALUES "
                "(:tenant_id, :credential_id, :store_id, :operation, :target_id, "
                " :before, :after, :result, :error, :operator)"
            ),
            {
                "tenant_id": tenant_id,
                "credential_id": credential_id,
                "store_id": store_id,
                "operation": operation,
                "target_id": str(target_id),
                "before": json.dumps(before, ensure_ascii=False) if before is not None else None,
                "after": json.dumps(after, ensure_ascii=False) if after is not None else None,
                "result": result,
                "error": error,
                "operator": operator,
            },
        )
