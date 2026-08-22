"""M2.1: 在售商品列表业务层 — 租户隔离 + 分页查询 product_task_index。

- 只读：SELECT product_task_index LEFT JOIN ozon_product_tasks 提取审核状态，不写任何数据
- 租户隔离：WHERE tenant_id=:tenant_id（A 租户看不到 B 租户的商品）
- 分页：ORDER BY created_at DESC + LIMIT/OFFSET；total 用 COUNT(*) 独立查询
- moderation_status：尽力从任务 result JSONB 提取（LEFT JOIN，无 → null），不实时调 Ozon
  （缓存语义：任务终态即最新，避免速率限制）
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# t.result->>'moderation_status'：LEFT JOIN 提取 JSONB 键（无匹配/键缺失 → NULL）
_SELECT_COLS = (
    "i.product_id, i.offer_id, i.task_id, i.draft_id, i.credential_id, i.created_at, "
    "t.result->>'moderation_status' AS moderation_status"
)


def _row_to_item(row) -> dict:
    return {
        "product_id": str(row[0]),
        "offer_id": str(row[1]),
        "task_id": str(row[2]),
        "draft_id": str(row[3]) if row[3] is not None else None,
        "credential_id": str(row[4]) if row[4] is not None else None,
        "created_at": row[5],
        "moderation_status": row[6],
    }


def list_products(tenant_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """按租户查询在售商品列表（created_at DESC），返回 {items, total, limit, offset}。"""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_SELECT_COLS} "
            "FROM product_task_index i "
            "LEFT JOIN ozon_product_tasks t ON t.id = i.task_id "
            "WHERE i.tenant_id=:tenant_id ORDER BY i.created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ), {"tenant_id": tenant_id, "limit": limit, "offset": offset}).fetchall()
        total = conn.execute(text(
            "SELECT COUNT(*) FROM product_task_index WHERE tenant_id=:tenant_id"
        ), {"tenant_id": tenant_id}).scalar()
    return {
        "items": [_row_to_item(r) for r in rows],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }


# ──────────────────────────────────────────────
# v0.50 在线商品实时拉取（修复「配置店铺看不到在线商品」）
# ──────────────────────────────────────────────


def list_ozon_products(
    tenant_id: str,
    credential_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """实时拉取 Ozon 店铺在线商品（对标上品帮 /goodsManage、毛子 /product/online）。

    两步拼接：
      1. /v3/product/list {filter:{visibility:'ALL'}} → [product_id, offer_id]
      2. /v1/product/info/list {product_id:[...]} → name/images/price/stocks
    （product_task_index 只含本系统上架商品，此端点覆盖店铺全部商品。）

    Returns: {items: [OzonProductOut], total, limit, offset, store}
    """
    from fastapi import HTTPException
    from services.credential_service import get_decrypted, get_default_credential
    from utils.ozon_client import ozon_post
    from utils.ozon_pagination import paginate

    if credential_id:
        client_id, api_key = get_decrypted(tenant_id, str(credential_id))
        store_id = str(credential_id)
    else:
        default = get_default_credential(tenant_id)
        if default is None:
            raise HTTPException(
                status_code=400,
                detail="未配置默认店铺：请传 credential_id 或先在店铺管理设置默认店铺",
            )
        client_id, api_key = default["ozon_client_id"], default["api_key"]
        store_id = default["id"]

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    try:
        items_raw = paginate(
            client_id, api_key, "/v3/product/list",
            {"filter": {"visibility": "ALL"}, "limit": limit, "offset": offset, "sort_dir": "ASC"},
            cursor_style="offset", post_fn=ozon_post, timeout=30, language="RU",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Ozon 商品列表拉取失败 client=%s: %s", client_id, str(exc)[:200])
        raise HTTPException(status_code=502, detail=f"Ozon 商品列表接口请求失败：{str(exc)[:120]}")

    total = len(items_raw)

    product_ids = [str(it.get("product_id")) for it in items_raw if isinstance(it, dict) and it.get("product_id")]
    info_map: dict[str, dict] = {}
    if product_ids:
        try:
            # ⚠️ /v3/product/info/list 批量查询必须传整数数组（字符串数组返回空 items）
            int_ids = [int(pid) for pid in product_ids if str(pid).isdigit()]
            if int_ids:
                # ⚠️ Ozon 对 info/list 有速率限制：高频下静默返回空 items（不报错）。
                #    空结果时退避重试（1s/2s），避免误判「商品无详情」。
                import time as _time
                info_items: list = []
                for _attempt in range(3):
                    info_resp = ozon_post(
                        client_id, api_key, "/v3/product/info/list",
                        {"product_id": int_ids}, timeout=30, language="RU",
                    )
                    info_items = (info_resp.get("result") or {}).get("items") or []
                    if info_items:
                        break
                    if _attempt < 2:
                        _time.sleep(1 + _attempt)
                if not info_items:
                    logger.warning("Ozon info/list 重试 3 次仍空（疑似限流）ids=%s", int_ids[:5])
                for it in info_items:
                    if isinstance(it, dict) and it.get("id"):
                        info_map[str(it.get("id"))] = it
        except Exception as exc:
            logger.warning("Ozon 商品详情拉取失败（降级返回列表）client=%s: %s", client_id, str(exc)[:200])

    items = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("product_id"))
        info = info_map.get(pid) or {}
        images = info.get("images") or []
        # ⚠️ /v3/product/info/list 结构：price 是顶层字符串、currency_code 顶层、
        #    stocks.stocks[0].present（嵌套数组）
        price = None
        try:
            price = float(info.get("price")) if info.get("price") else None
        except (TypeError, ValueError):
            price = None
        currency = str(info.get("currency_code") or "")
        stock = None
        stocks_wrap = info.get("stocks") or {}
        stock_list = stocks_wrap.get("stocks") if isinstance(stocks_wrap, dict) else None
        if isinstance(stock_list, list) and stock_list:
            stock = int((stock_list[0] or {}).get("present") or 0)
        items.append({
            "product_id": pid,
            "offer_id": str(it.get("offer_id") or info.get("offer_id") or ""),
            "name": info.get("name") or str(it.get("offer_id") or pid),
            "image": images[0] if isinstance(images, list) and images else None,
            "price": price,
            "stock": stock,
            "currency": currency,
        })

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "store": {"id": store_id, "ozon_client_id": client_id},
    }


# ──────────────────────────────────────────────
# P1a 在线商品批量操作（改价/库存/归档，真实影响）
# ──────────────────────────────────────────────


def _resolve_cred(tenant_id: str, credential_id: str | None) -> tuple[str, str]:
    """凭证解析（credential_id 或默认店铺）；无默认 → 400。"""
    from fastapi import HTTPException
    from services.credential_service import get_decrypted, get_default_credential
    if credential_id:
        return get_decrypted(tenant_id, str(credential_id))
    default = get_default_credential(tenant_id)
    if default is None:
        raise HTTPException(
            status_code=400,
            detail="未配置默认店铺：请传 credential_id 或先在店铺管理设置默认店铺",
        )
    return default["ozon_client_id"], default["api_key"]


def _bulk_action(endpoint: str, body: dict, tenant_id: str, credential_id: str | None, what: str) -> dict:
    """统一批量写入包装：ozon_post → result；失败 502。"""
    from fastapi import HTTPException
    from utils.ozon_client import ozon_post
    client_id, api_key = _resolve_cred(tenant_id, credential_id)
    try:
        resp = ozon_post(client_id, api_key, endpoint, body, timeout=30, language="RU")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("%s失败 client=%s: %s", what, client_id, str(exc)[:200])
        raise HTTPException(status_code=502, detail=f"Ozon {what}失败：{str(exc)[:120]}")
    return resp.get("result") or {}


def bulk_update_prices(
    tenant_id: str,
    prices: list[dict],
    credential_id: str | None = None,
) -> dict:
    """批量改价：/v1/product/import/prices {prices:[{offer_id, price, old_price?, min_price?}]}。"""
    return {
        "ok": True,
        "result": _bulk_action(
            "/v1/product/import/prices", {"prices": prices},
            tenant_id, credential_id, "批量改价"),
    }


def bulk_update_stocks(
    tenant_id: str,
    stocks: list[dict],
    credential_id: str | None = None,
) -> dict:
    """批量改库存：/v2/products/stocks {stocks:[{offer_id, product_id, stock}]}。"""
    return {
        "ok": True,
        "result": _bulk_action(
            "/v2/products/stocks", {"stocks": stocks},
            tenant_id, credential_id, "批量改库存"),
    }


def bulk_archive(
    tenant_id: str,
    product_ids: list[str],
    archive: bool,
    credential_id: str | None = None,
) -> dict:
    """批量归档/恢复：/v1/product/archive 或 /v1/product/unarchive {product_id:[...]}。"""
    ids = [int(pid) for pid in product_ids if str(pid).isdigit()]
    endpoint = "/v1/product/archive" if archive else "/v1/product/unarchive"
    return {
        "ok": True,
        "result": _bulk_action(endpoint, {"product_id": ids}, tenant_id, credential_id,
                               "批量归档" if archive else "批量恢复"),
    }
