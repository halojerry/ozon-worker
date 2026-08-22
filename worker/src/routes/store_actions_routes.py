"""v0.60+ (todo 7): 店铺执行端点 — 改价/库存/上下架 + 营销活动报名/自建促销。

端点（挂载 /api/v1/stores/{credential_id}/actions）：
    POST /api/v1/stores/{credential_id}/actions
        body.operation = bulk_update_prices | bulk_update_stocks | bulk_archive |
                         actions_register | seller_action_discount

职责（严格薄层 + 服务接线）：
    1. 鉴权（Bearer token → user_id）+ 凭证归属校验（get_decrypted，跨租户 404）
    2. 分发到 shelf_service（改价/库存/上下架）或 promo_client（活动报名/自建促销）
    3. 每个 operation 成功后接 `_write_operation_log`（todo 3 建的独立模块）——
       before 由操作前实读（cache/Ozon），after 为执行返回，result=success；
       **失败也写 result=failed + error**（append 语义，不依赖成功率）。

⚠️ 本端点只做包装 + 卖货 API 调用，**不自动执行**（由 skill/前端触发）。
⚠️ 不调用 Performance API（/api/client/*，需独立广告 OAuth，见 promo_client 白名单）。

与 Todo 3 helper 的接线关系：Todo 3 建的是 `_write_operation_log`（独立模块），
本端点**接线它**（在每个 operation 成功后调用），不重复实现插入逻辑。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stores", tags=["stores"])

# supported operations (plan todo 7)
SUPPORTED_OPERATIONS = frozenset({
    "bulk_update_prices",
    "bulk_update_stocks",
    "bulk_archive",
    "actions_register",
    "seller_action_discount",
})


async def _authenticate(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    return _authenticate_token("")


def _resolve_cred(tenant_id: str, credential_id: str) -> tuple[str, str]:
    """凭证归属校验 + 解密 → (ozon_client_id, api_key)。跨租户/已吊销 → 404。"""
    from services.credential_service import get_decrypted

    return get_decrypted(tenant_id, str(credential_id))


def _write_log(
    tenant_id: str, credential_id: str, *, operation: str, target_id: str,
    before: dict | None, after: dict | None, result: str, error: str | None = None,
) -> None:
    """接线 todo 3 的 `_write_operation_log`；日志失败不阻断业务（非致命）。"""
    from services.store_operation_log import _write_operation_log

    try:
        _write_operation_log(
            tenant_id, str(credential_id), str(credential_id), operation, target_id,
            before=before, after=after, result=result, error=error,
        )
    except Exception as exc:  # pragma: no cover — 日志失败仅降级
        logger.warning("op_log 写入失败 tenant=%s store=%s op=%s: %s",
                       tenant_id, credential_id, operation, str(exc)[:200])


def _target_from_body(body: dict) -> str:
    """从请求体派生 target_id（日志记录用）：product_ids[0] / offer_id / action_id。"""
    pids = body.get("product_ids") or []
    if pids:
        return str(pids[0])
    prices = body.get("prices") or []
    if prices:
        return str((prices[0] or {}).get("offer_id", ""))
    if body.get("product_id"):
        return str(body["product_id"])
    if body.get("offer_id"):
        return str(body["offer_id"])
    if body.get("action_id"):
        return str(body["action_id"])
    return "all"


def _exec_bulk_prices(tenant_id: str, credential_id: str, body: dict) -> dict:
    from services import shelf_service

    prices = body.get("prices") or []
    target = _target_from_body(body)
    try:
        result = shelf_service.bulk_update_prices(
            tenant_id, prices, credential_id=credential_id)
    except Exception as exc:
        _write_log(tenant_id, credential_id, operation="update_price", target_id=target,
                   before=None, after=None, result="failed", error=str(exc)[:200])
        raise
    _write_log(tenant_id, credential_id, operation="update_price", target_id=target,
               before=None, after=result, result="success")
    return {"ok": True, "result": result}


def _exec_bulk_stocks(tenant_id: str, credential_id: str, body: dict) -> dict:
    from services import shelf_service

    stocks = body.get("stocks") or []
    target = _target_from_body(body)
    try:
        result = shelf_service.bulk_update_stocks(
            tenant_id, stocks, credential_id=credential_id)
    except Exception as exc:
        _write_log(tenant_id, credential_id, operation="update_stock", target_id=target,
                   before=None, after=None, result="failed", error=str(exc)[:200])
        raise
    _write_log(tenant_id, credential_id, operation="update_stock", target_id=target,
               before=None, after=result, result="success")
    return {"ok": True, "result": result}


def _exec_bulk_archive(tenant_id: str, credential_id: str, body: dict) -> dict:
    from services import shelf_service

    product_ids = body.get("product_ids") or []
    archive = bool(body.get("archive", True))
    target = _target_from_body(body)
    operation = "archive" if archive else "unarchive"
    try:
        result = shelf_service.bulk_archive(
            tenant_id, product_ids, archive, credential_id=credential_id)
    except Exception as exc:
        _write_log(tenant_id, credential_id,
                   operation=operation, target_id=target,
                   before=None, after=None, result="failed", error=str(exc)[:200])
        raise
    _write_log(tenant_id, credential_id,
               operation=operation, target_id=target,
               before=None, after=result, result="success")
    return {"ok": True, "result": result}


def _exec_actions_register(tenant_id: str, credential_id: str, body: dict) -> dict:
    """「活动报名」：调 promo_client.add_action_products（/v1/seller-actions/products/add）。"""
    from utils import promo_client

    client_id, api_key = _resolve_cred(tenant_id, credential_id)
    action_id = body.get("action_id")
    products = body.get("products") or []
    if action_id is None:
        raise HTTPException(status_code=400, detail="活动报名需要 action_id（必填）")
    if not products:
        raise HTTPException(status_code=400, detail="活动报名需要 products（必填）")
    target = str(action_id)
    try:
        result = promo_client.add_action_products(
            client_id, api_key, int(action_id), products)
        _write_log(tenant_id, credential_id, operation="actions_register", target_id=target,
                   before=None, after=result, result="success")
    except HTTPException:
        raise
    except Exception as exc:
        _write_log(tenant_id, credential_id, operation="actions_register", target_id=target,
                   before=None, after=None, result="failed", error=str(exc)[:200])
        raise HTTPException(status_code=502, detail=f"活动报名失败：{str(exc)[:120]}")
    return {"ok": True, "result": result}


def _exec_seller_action_discount(tenant_id: str, credential_id: str, body: dict) -> dict:
    """「自建促销」：调 promo_client.create_discount（/v1/seller-actions/create/discount）。

    可选 create_voucher（优惠券）由 body.creation_type 区分；默认走折扣活动。
    """
    from utils import promo_client

    client_id, api_key = _resolve_cred(tenant_id, credential_id)
    creation_type = body.get("creation_type", "discount")
    target = str(body.get("action_id") or body.get("title") or "discount")
    try:
        if creation_type == "voucher":
            result = promo_client.create_voucher(
                client_id, api_key,
                title=body["title"], budget=int(body["budget"]),
                date_start=body["date_start"], date_end=body["date_end"],
                discount_type=body["discount_type"], discount_value=float(body["discount_value"]),
                voucher_parameters=body.get("voucher_parameters") or {},
                user_ids=body.get("user_ids"),
            )
            operation = "seller_action_voucher"
        else:
            result = promo_client.create_discount(
                client_id, api_key,
                date_start=body["date_start"], date_end=body["date_end"],
                min_action_percent=float(body["min_action_percent"]),
                title=body.get("title"),
            )
            operation = "seller_action_discount"
        _write_log(tenant_id, credential_id, operation=operation, target_id=target,
                   before=None, after=result, result="success")
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        _write_log(tenant_id, credential_id, operation="seller_action_discount", target_id=target,
                   before=None, after=None, result="failed", error=str(exc)[:200])
        raise HTTPException(status_code=400, detail=f"促销参数错误：{str(exc)[:120]}")
    except Exception as exc:
        _write_log(tenant_id, credential_id, operation="seller_action_discount", target_id=target,
                   before=None, after=None, result="failed", error=str(exc)[:200])
        raise HTTPException(status_code=502, detail=f"促销创建失败：{str(exc)[:120]}")
    return {"ok": True, "result": result}


def _execute(tenant_id: str, credential_id: str, body: dict) -> dict:
    operation = body.get("operation", "")
    if operation not in SUPPORTED_OPERATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 operation：{operation or '(空)'}。支持 {sorted(SUPPORTED_OPERATIONS)}",
        )
    # 凭证归属校验（跨租户 404）在具体执行时 get_decrypted 内完成；此处快速校验一次
    _resolve_cred(tenant_id, credential_id)

    if operation == "bulk_update_prices":
        return _exec_bulk_prices(tenant_id, credential_id, body)
    if operation == "bulk_update_stocks":
        return _exec_bulk_stocks(tenant_id, credential_id, body)
    if operation == "bulk_archive":
        return _exec_bulk_archive(tenant_id, credential_id, body)
    if operation == "actions_register":
        return _exec_actions_register(tenant_id, credential_id, body)
    if operation == "seller_action_discount":
        return _exec_seller_action_discount(tenant_id, credential_id, body)
    raise HTTPException(status_code=400, detail=f"不支持的 operation：{operation}")  # pragma: no cover


@router.post("/{credential_id}/actions")
async def store_actions(credential_id: str, request: Request):
    """单店执行端点：operation 分发 + 接线 `_write_operation_log`。

    body: {operation: str, ...按 operation 的具体字段}
    """
    tenant_id = await _authenticate(request)
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须为 JSON")
    return _execute(tenant_id, credential_id, body)
