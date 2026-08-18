"""订单服务（P0-4）：Ozon FBS 订单实时拉取 + 状态映射 + 标准化。

数据源 = Ozon Seller API `/v4/posting/fbs/list`（实时拉取，不建表；v3 已弃用
2026-06-01 停用）。v4 差异：游标分页（cursor/has_next，无 offset/total）、
`filter.statuses` 数组（非 v3 的 status）、`products[].price` 为对象
`{amount, currency}`、financial_data `commission` 对象（非 v3 的 commission_amount）。
T4.3：按 product_id 批量 /v3/product/info/list 补订单商品主图（复用
store_sync_service._fetch_info_map_by_ids 的 int product_id 批量模式）。
状态机：Ozon raw status → 统一 7 态（前端 tab 用）：
  pending 待处理 / awaiting 待备货 / waiting 待发运 / delivering 运输中 /
  delivered 已签收 / cancelled 已取消 / other 其他
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException

from services.credential_service import get_decrypted, get_default_credential
from utils.ozon_client import ozon_post

logger = logging.getLogger(__name__)

# Ozon FBS status → 统一态映射
STATUS_MAP: dict[str, str] = {
    "awaiting_registration": "pending",
    "acceptance_in_progress": "pending",
    "arbitrary_available": "awaiting",
    "arbitrary_not_enough_for_package": "awaiting",
    "arbitrary_waiting_for_shipment": "waiting",
    "arbitrary_cancelled_by_merchant": "waiting",
    "driver_pickup": "delivering",
    "delivering": "delivering",
    "delivered": "delivered",
    "cancelled": "cancelled",
    "cancelled_by_merchant": "cancelled",
    "cancelled_by_customer": "cancelled",
    "cancelled_by_ozon": "cancelled",
    "cancelled_arbitrary": "cancelled",
    "cancelled_arbitrary_by_merchant": "cancelled",
}


def map_status(raw_status: str) -> str:
    return STATUS_MAP.get(raw_status, "other")


def _fmt_dt(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.astimezone().isoformat()
    except (ValueError, TypeError):
        return str(value)


def _price_value(p: Any) -> float:
    """商品单价提取：v3 标量 / v4 price 对象 {amount, ...} → float（兼容两者）。"""
    if not isinstance(p, dict):
        return 0.0
    price = p.get("price")
    if isinstance(price, dict):
        price = price.get("amount")
    try:
        return float(price or 0)
    except (TypeError, ValueError):
        return 0.0


def _sum_amount(products: Any) -> float:
    """汇总 financial_data.products[].price 或 posting.products[].price（订单金额）。

    v4 兼容：financial_data.products[].price 为标量（如 2500），
    posting.products[].price 为对象 {amount, currency}——_price_value 统一处理。
    """
    if not isinstance(products, list):
        return 0.0
    total = 0.0
    for p in products:
        if isinstance(p, dict):
            try:
                total += _price_value(p)
            except (TypeError, ValueError):
                continue
    return round(total, 2)


def _commission_value(p: Any) -> float:
    """平台费用提取：v3 commission_amount 标量 / v4 commission {amount, ...} 对象 → float。"""
    if not isinstance(p, dict):
        return 0.0
    commission = p.get("commission_amount")
    if commission is None:
        commission = p.get("commission") or {}
        if isinstance(commission, dict):
            commission = commission.get("amount")
    try:
        return float(commission or 0)
    except (TypeError, ValueError):
        return 0.0


def _sum_commission(financial: Any) -> float:
    """汇总 financial_data.products[].commission（平台费用）。"""
    if not isinstance(financial, dict):
        return 0.0
    products = financial.get("products")
    if not isinstance(products, list):
        return 0.0
    total = 0.0
    for p in products:
        if isinstance(p, dict):
            try:
                total += _commission_value(p)
            except (TypeError, ValueError):
                continue
    return round(total, 2)


def _extract_products(postings_items: Any) -> list[dict]:
    """posting.products[] → OrderProductOut（name/sku/quantity/price/offer_id/product_id/image）。

    T4.3：product_id 取 posting 行 product_id 或 sku（Ozon posting 中 sku == product_id，
    供 /v3/product/info/list 批量拉主图）；image 由上层 info_map 回填。
    """
    if not isinstance(postings_items, list):
        return []
    out = []
    for p in postings_items:
        if not isinstance(p, dict):
            continue
        price = _price_value(p) if p.get("price") is not None else None
        out.append({
            "name": p.get("name", ""),
            "sku": p.get("sku"),
            "quantity": int(p.get("quantity") or 0),
            "price": price,
            "offer_id": p.get("offer_id", ""),
            "product_id": p.get("product_id") or p.get("sku"),
            "image": None,
        })
    return out


def _normalize_posting(p: dict) -> dict:
    """单个 posting → OrderOut 标准化 dict。"""
    products = _extract_products(p.get("products"))
    financial = p.get("financial_data") or {}
    amount = _sum_amount(financial.get("products")) or _sum_amount(products)
    commission = _sum_commission(financial)
    delivery = p.get("delivery_method") or {}
    analytics = p.get("analytics_data") or {}
    cancellation = p.get("cancellation") or {}
    return {
        "posting_number": p.get("posting_number", ""),
        "status": map_status(p.get("status", "")),
        "raw_status": p.get("status", ""),
        "created_at": _fmt_dt(p.get("in_process_at") or p.get("shipment_date")),
        "products": products,
        "product_count": sum(x.get("quantity") or 0 for x in products),
        "total_amount": amount,
        "commission_amount": commission,
        "profit": round(amount - commission, 2) if amount or commission else None,
        "warehouse": analytics.get("warehouse") or delivery.get("warehouse") or "",
        "delivery_method": delivery.get("name", ""),
        "cancel_reason": p.get("cancel_reason") or cancellation.get("reason", ""),
        "cancellation": cancellation.get("cancellation_type", ""),
    }


def _build_filter(status: Optional[str], since_days: int) -> dict:
    # ⚠️ Ozon 要求 since/to 严格 YYYY-MM-DDTHH:MM:SSZ（isoformat 微秒+偏移会 400），
    #    且 filter 必须同时设置 since 和 to（缺 to → 400 processed_at_to must be set）。
    #    v4 兼容：状态用 statuses 数组（v3 的 status 字符串 v4 已废弃）。
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=max(1, min(int(since_days), 90)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    f: dict[str, Any] = {"since": since, "to": to}
    if status and status != "all":
        f["statuses"] = [status]
    return f


def _collect_product_ids(postings: Any) -> list[int]:
    """从 postings 收集订单商品 int product_id（v4 posting.products.sku == financial_data.product_id）。

    供 /v3/product/info/list 批量拉主图（T4.3）。保持出现顺序去重。
    """
    ids: list[int] = []
    if not isinstance(postings, list):
        return ids
    for p in postings:
        if not isinstance(p, dict):
            continue
        for prod in p.get("products") or []:
            if isinstance(prod, dict):
                _append_pid(ids, prod.get("product_id") or prod.get("sku"))
        financial = p.get("financial_data") or {}
        if isinstance(financial, dict):
            for prod in financial.get("products") or []:
                if isinstance(prod, dict):
                    _append_pid(ids, prod.get("product_id") or prod.get("sku"))
    return ids


def _append_pid(ids: list[int], pid: Any) -> None:
    if pid is None:
        return
    try:
        i = int(pid)
    except (TypeError, ValueError):
        return
    if i not in ids:
        ids.append(i)


def fetch_order_images(client_id: str, api_key: str, postings: Any) -> dict:
    """按 product_id 批量拉订单商品主图（/v3/product/info/list，复用 store_sync 限流重试）。

    Returns: {str(product_id): 主图 URL}。任何异常 fail-open 返回 {}（图片不阻断订单列表）。
    """
    ids = _collect_product_ids(postings)
    if not ids:
        return {}
    try:
        from services.store_sync_service import _fetch_info_map_by_ids  # 延迟导入防循环
        info_map = _fetch_info_map_by_ids(client_id, api_key, ids)
    except Exception as exc:
        logger.warning("订单商品图批量拉取失败（不阻断）: %s", str(exc)[:200])
        return {}
    images: dict[str, str] = {}
    for pid, info in info_map.items():
        imgs = info.get("images") if isinstance(info, dict) else None
        if isinstance(imgs, list) and imgs:
            images[str(pid)] = imgs[0]
    return images


def apply_product_images(products: list, images: dict) -> None:
    """把 {str(product_id): 主图 URL} 回填到商品行（幂等：已有 image 不覆盖）。

    product_id 缺省时回退 sku（旧缓存行无 product_id 键，sku 即 product_id）。
    """
    for prod in products:
        if not isinstance(prod, dict) or prod.get("image"):
            continue
        pid = prod.get("product_id") or prod.get("sku")
        if pid is None:
            continue
        url = images.get(str(pid))
        if url:
            prod["image"] = url


def list_orders(
    tenant_id: str,
    credential_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    since_days: int = 30,
) -> dict[str, Any]:
    """实时拉取 Ozon FBS 订单（租户隔离 + 凭证归属校验）。

    Returns: {items: [OrderOut], total, limit, offset, store: {ozon_client_id}}
    """
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

    limit = max(1, min(int(limit), 100))  # v4 limit 上限 100（v3 为 1000）
    offset = max(0, int(offset))

    body = {
        "sort_dir": "ASC",
        "filter": _build_filter(status, since_days),
        "limit": limit,
        "cursor": "",
        "with": {"analytics_data": True, "financial_data": True},
    }
    try:
        resp = ozon_post(client_id, api_key, "/v4/posting/fbs/list", body, timeout=30, language="RU")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Ozon FBS 订单拉取失败 client=%s: %s", client_id, str(exc)[:200])
        raise HTTPException(status_code=502, detail=f"Ozon 订单接口请求失败：{str(exc)[:120]}")

    # v4 响应扁平（cursor/has_next/postings）；v3 包 result——兼容两者
    result = resp.get("result") or resp
    postings = result.get("postings") or []
    items = [_normalize_posting(p) for p in postings if isinstance(p, dict)]

    # T4.3：按 product_id 批量拉订单商品主图（fail-open，不阻断列表）
    images = fetch_order_images(client_id, api_key, postings)
    for item in items:
        apply_product_images(item.get("products") or [], images)

    total = int(result.get("total") or len(items))

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "store": {"id": store_id, "ozon_client_id": client_id},
    }


# ──────────────────────────────────────────────
# P1-1 订单操作：货源/采购信息标注（本地）+ 面单代理
# ──────────────────────────────────────────────

NOTES_COLS = ("posting_number, tenant_id, source_url, source_cost, source_remark, "
              "purchase_no, purchase_carrier, purchase_tracking, created_at, updated_at")


def _note_row_to_dict(row) -> dict:
    return {
        "posting_number": row.posting_number,
        "tenant_id": row.tenant_id,
        "source_url": row.source_url or "",
        "source_cost": row.source_cost,
        "source_remark": row.source_remark or "",
        "purchase_no": row.purchase_no or "",
        "purchase_carrier": row.purchase_carrier or "",
        "purchase_tracking": row.purchase_tracking or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_order_notes(tenant_id: str, posting_number: str) -> dict:
    """读取订单标注（无记录返回空模板，不 404——先标注后同步订单）。"""
    from storage.database.db import get_engine
    from sqlalchemy import text
    with get_engine().connect() as conn:
        row = conn.execute(text(
            f"SELECT {NOTES_COLS} FROM order_notes "
            "WHERE posting_number=:pn AND tenant_id=:tenant_id"
        ), {"pn": posting_number, "tenant_id": tenant_id}).fetchone()
    if row is None:
        return {
            "posting_number": posting_number,
            "tenant_id": tenant_id,
            "source_url": "", "source_cost": None, "source_remark": "",
            "purchase_no": "", "purchase_carrier": "", "purchase_tracking": "",
            "created_at": None, "updated_at": None,
        }
    return _note_row_to_dict(row)


def upsert_order_notes(tenant_id: str, posting_number: str, data: dict) -> dict:
    """写入/更新订单标注（upsert on posting_number + tenant_id 校验归属）。"""
    from storage.database.db import get_engine
    from sqlalchemy import text

    source_cost = data.get("source_cost")
    if source_cost is not None:
        try:
            source_cost = float(source_cost)
        except (TypeError, ValueError):
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="source_cost 必须是数字")
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO order_notes (posting_number, tenant_id, source_url, source_cost, "
            "source_remark, purchase_no, purchase_carrier, purchase_tracking) "
            "VALUES (:pn, :tenant_id, :su, :sc, :sr, :pno, :pc, :pt) "
            "ON CONFLICT (posting_number) DO UPDATE SET "
            "source_url=EXCLUDED.source_url, source_cost=EXCLUDED.source_cost, "
            "source_remark=EXCLUDED.source_remark, purchase_no=EXCLUDED.purchase_no, "
            "purchase_carrier=EXCLUDED.purchase_carrier, purchase_tracking=EXCLUDED.purchase_tracking, "
            "updated_at=NOW()"
        ), {
            "pn": posting_number,
            "tenant_id": tenant_id,
            "su": str(data.get("source_url") or ""),
            "sc": source_cost,
            "sr": str(data.get("source_remark") or ""),
            "pno": str(data.get("purchase_no") or ""),
            "pc": str(data.get("purchase_carrier") or ""),
            "pt": str(data.get("purchase_tracking") or ""),
        })
        row = conn.execute(text(
            f"SELECT {NOTES_COLS} FROM order_notes WHERE posting_number=:pn"
        ), {"pn": posting_number}).fetchone()
    return _note_row_to_dict(row)


def get_order_label(
    tenant_id: str,
    posting_number: str,
    credential_id: Optional[str] = None,
) -> dict:
    """面单 PDF 代理：/v2/posting/fbs/package-label → 返回 PDF bytes（base64 由路由层编码）。

    Returns: {"posting_number", "content_type", "label_base64"}
    """
    import base64
    if credential_id:
        client_id, api_key = get_decrypted(tenant_id, str(credential_id))
    else:
        default = get_default_credential(tenant_id)
        if default is None:
            raise HTTPException(
                status_code=400,
                detail="未配置默认店铺：请传 credential_id 或先在店铺管理设置默认店铺",
            )
        client_id, api_key = default["ozon_client_id"], default["api_key"]
    try:
        resp = ozon_post(
            client_id, api_key, "/v2/posting/fbs/package-label",
            {"posting_number": posting_number}, timeout=30, language="RU",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("面单拉取失败 client=%s: %s", client_id, str(exc)[:200])
        raise HTTPException(status_code=502, detail=f"Ozon 面单接口请求失败：{str(exc)[:120]}")
    result = resp.get("result") or {}
    pdf_b64 = result.get("pdf") or result.get("label") or ""
    if not pdf_b64:
        raise HTTPException(status_code=502, detail="Ozon 未返回面单 PDF")
    return {
        "posting_number": posting_number,
        "content_type": "application/pdf",
        "label_base64": pdf_b64 if isinstance(pdf_b64, str) else base64.b64encode(pdf_b64).decode(),
    }


# ──────────────────────────────────────────────
# P1-2 订单写入操作：备货发货 / 取消（真实影响，谨慎调用）
# ──────────────────────────────────────────────

def _resolve_credential(tenant_id: str, credential_id: Optional[str]) -> tuple[str, str]:
    """凭证解析（credential_id 或默认店铺）；无默认 → 400。"""
    if credential_id:
        return get_decrypted(tenant_id, str(credential_id))
    default = get_default_credential(tenant_id)
    if default is None:
        raise HTTPException(
            status_code=400,
            detail="未配置默认店铺：请传 credential_id 或先在店铺管理设置默认店铺",
        )
    return default["ozon_client_id"], default["api_key"]


def _ozon_action(endpoint: str, body: dict, client_id: str, api_key: str, what: str) -> dict:
    """统一写入操作包装：ozon_post → result；失败 502。"""
    try:
        resp = ozon_post(client_id, api_key, endpoint, body, timeout=30, language="RU")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("%s失败 client=%s: %s", what, client_id, str(exc)[:200])
        raise HTTPException(status_code=502, detail=f"Ozon {what}接口请求失败：{str(exc)[:120]}")
    return resp.get("result") or {}


def ship_order(
    tenant_id: str,
    posting_number: str,
    credential_id: Optional[str] = None,
) -> dict:
    """备货发货：/v4/posting/fbs/ship（对标上品帮批量备货）。

    packages[].products 从订单商品映射（product_id=sku, quantity）；
    传入空时省略 products 让 Ozon 自动取单内全部商品（调用方需确认订单属主）。
    """
    client_id, api_key = _resolve_credential(tenant_id, credential_id)
    packages = [{
        "posting_number": posting_number,
        "packages_count": 1,
    }]
    result = _ozon_action(
        "/v4/posting/fbs/ship", {"packages": packages}, client_id, api_key, "备货发货")
    return {"ok": True, "posting_number": posting_number, "result": result}


def list_cancel_reasons(
    tenant_id: str,
    posting_number: str,
    credential_id: Optional[str] = None,
) -> list[dict]:
    """取消原因列表：/v1/posting/fbs/cancel-reason → [{id, title}]。"""
    client_id, api_key = _resolve_credential(tenant_id, credential_id)
    result = _ozon_action(
        "/v1/posting/fbs/cancel-reason", {"posting_number": posting_number},
        client_id, api_key, "取消原因查询")
    reasons = result.get("cancel_reasons") if isinstance(result, dict) else result
    out = []
    for r in reasons or []:
        if isinstance(r, dict):
            out.append({"id": int(r.get("id") or 0), "title": str(r.get("title") or "")})
    return out


def cancel_order(
    tenant_id: str,
    posting_number: str,
    cancel_reason_id: int,
    credential_id: Optional[str] = None,
) -> dict:
    """取消订单：/v2/posting/fbs/cancel（对标毛子取消货件选原因）。"""
    client_id, api_key = _resolve_credential(tenant_id, credential_id)
    result = _ozon_action(
        "/v2/posting/fbs/cancel",
        {"posting_number": posting_number, "cancel_reason_id": int(cancel_reason_id)},
        client_id, api_key, "取消订单")
    return {"ok": True, "posting_number": posting_number, "result": result}


# ──────────────────────────────────────────────
# P1-3 订单批量操作：批量面单 / 批量备货（失败隔离，对标上品帮批量打单）
# ──────────────────────────────────────────────

def batch_order_labels(
    tenant_id: str,
    posting_numbers: list[str],
    credential_id: Optional[str] = None,
) -> dict:
    """批量面单：逐单调 get_order_label，失败隔离。

    Returns: {"ok": bool, "items": [OrderLabelResponse], "failed": [{posting_number, error}]}
    """
    items: list[dict] = []
    failed: list[dict] = []
    for pn in posting_numbers:
        try:
            items.append(get_order_label(tenant_id, pn, credential_id))
        except HTTPException as exc:
            failed.append({"posting_number": pn, "error": str(exc.detail)})
        except Exception as exc:
            failed.append({"posting_number": pn, "error": str(exc)[:120]})
    return {"ok": len(failed) == 0, "items": items, "failed": failed}


def batch_ship_orders(
    tenant_id: str,
    posting_numbers: list[str],
    credential_id: Optional[str] = None,
) -> dict:
    """批量备货发货：逐单调 ship_order，失败隔离。

    Returns: {"ok": bool, "shipped": [posting_number], "failed": [{posting_number, error}]}
    """
    shipped: list[str] = []
    failed: list[dict] = []
    for pn in posting_numbers:
        try:
            ship_order(tenant_id, pn, credential_id)
            shipped.append(pn)
        except HTTPException as exc:
            failed.append({"posting_number": pn, "error": str(exc.detail)})
        except Exception as exc:
            failed.append({"posting_number": pn, "error": str(exc)[:120]})
    return {"ok": len(failed) == 0, "shipped": shipped, "failed": failed}


# ──────────────────────────────────────────────
# P2c 消息催评：内置模板 + chat/start + send/message + 发送记录
# ──────────────────────────────────────────────

MESSAGE_TEMPLATES = [
    {
        "key": "passport",
        "name": "催护照",
        "text": "Здравствуйте! Товар, который вы покупаете: [货件编号] ([商品名称]), "
                "Вы еще не заполнили паспорт, поторопитесь заполнить паспортные данные "
                "и я организую доставку в кратчайшие сроки!",
    },
    {
        "key": "pickup",
        "name": "催取货",
        "text": "Здравствуйте! Ваш товар [货件编号] прибыл в пункт выдачи, "
                "поторопитесь и заберите посылку. Желаю вам хорошего дня!",
    },
    {
        "key": "review",
        "name": "索好评",
        "text": "Здравствуйте! Вы получили товар [货件编号]? Если вы довольны покупкой, "
                "пожалуйста, оставьте отзыв. Спасибо и хорошего дня!",
    },
]


def get_message_templates() -> list[dict]:
    """内置消息模板（静态，纯读）。"""
    return MESSAGE_TEMPLATES


def _fill_template(template_text: str, posting_number: str, product_name: str = "") -> str:
    """占位符替换：[货件编号]→posting_number，[商品名称]→product_name（截断 60）。"""
    name = (product_name or "")[:60]
    return template_text.replace("[货件编号]", posting_number).replace("[商品名称]", name or posting_number)


def list_order_messages(tenant_id: str, limit: int = 50, offset: int = 0) -> dict:
    """发送记录（租户隔离，时间倒序）。"""
    from storage.database.db import get_engine
    from sqlalchemy import text
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT posting_number, template_key, message, chat_id, status, error, created_at "
            "FROM order_messages WHERE tenant_id=:tid "
            "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        ), {"tid": tenant_id, "limit": limit, "offset": offset}).fetchall()
        total = conn.execute(text(
            "SELECT COUNT(*) FROM order_messages WHERE tenant_id=:tid"
        ), {"tid": tenant_id}).scalar()
    items = [{
        "posting_number": str(r[0]),
        "template_key": str(r[1] or ""),
        "message": str(r[2] or ""),
        "chat_id": str(r[3] or ""),
        "status": str(r[4] or "sent"),
        "error": str(r[5] or ""),
        "created_at": r[6].isoformat() if r[6] else None,
    } for r in rows]
    return {"items": items, "total": int(total or 0), "limit": limit, "offset": offset}


def send_order_message(
    tenant_id: str,
    posting_number: str,
    message: str,
    template_key: str = "custom",
    credential_id: Optional[str] = None,
) -> dict:
    """发送订单消息：chat/start → send/message → 本地记录（成功/失败都留痕）。

    message 1-1000 字符（Ozon 契约），超长截断到 1000。
    """
    from storage.database.db import get_engine
    from sqlalchemy import text

    client_id, api_key = _resolve_credential(tenant_id, credential_id)
    msg = (message or "").strip()
    if not msg:
        raise HTTPException(status_code=422, detail="消息内容不能为空")
    msg = msg[:1000]

    chat_id = ""
    status = "sent"
    error = ""
    try:
        # 1. chat/start 按订单建立聊天
        start_result = _ozon_action(
            "/v1/chat/start", {"posting_number": posting_number}, client_id, api_key, "开启聊天")
        chat_id = str((start_result or {}).get("chat_id") or "")
        if not chat_id:
            raise HTTPException(status_code=502, detail="Ozon 未返回 chat_id")
        # 2. send/message
        _ozon_action(
            "/v1/chat/send/message", {"chat_id": chat_id, "message": msg},
            client_id, api_key, "发送消息")
    except HTTPException as exc:
        status = "failed"
        error = str(exc.detail)[:300]

    # 本地记录（成功/失败都留痕）
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO order_messages "
            "(tenant_id, posting_number, template_key, message, chat_id, status, error) "
            "VALUES (:tid, :pn, :tk, :msg, :chat, :st, :err)"
        ), {
            "tid": tenant_id, "pn": posting_number, "tk": template_key,
            "msg": msg, "chat": chat_id, "st": status, "err": error,
        })

    if status == "failed":
        raise HTTPException(status_code=502, detail=error or "消息发送失败")
    return {"ok": True, "posting_number": posting_number, "chat_id": chat_id, "message": msg}
