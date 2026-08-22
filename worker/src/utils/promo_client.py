"""promo_client — 营销/活动（Seller Promo）客户端。

todo 7：执行端点「营销大师」能力。把 Ozon Seller API 的「活动报名 + 自建促销」
封装为同步方法，统一走 `utils.ozon_client.ozon_post`（worker 的同步 requests
transport）——**不调用 ozon-mcp 的异步 BaseClient**（服务端约束：仅同步）。

端点契约来源（**真实提取**，勿凭记忆编造）：
    docs/refs/ozon-mcp/data/seller_swagger.json
    本模块全部端点为「/v1/actions…」或「/v1/seller-actions…」（Seller API）。

调用约定（重要）：
    - list_actions /v1/actions（swagger 为 GET），但 worker 运输层仅 `ozon_post`
      支持 POST —— 我们按 POST 调用（Ozon 对这些端点容忍 POST）。测试断言其为
      worker ozon_post（endpoint=/v1/actions），**绝不触碰 /api/client/*（Performance
      API，需独立广告 OAuth，属 roadmap）**。
    - add_action_products（/v1/seller-actions/products/add）=「活动报名」。
    - create_discount（/v1/seller-actions/create/discount）=「自建促销」。

⚠️ 废弃规避：`/v1/seller-actions/update/ozon-card-discount` 等已被 Ozon 从 Seller
API spec v2.1 移除（deprecated_methods.yaml），**本模块永不使用** —— Ozon Card
折扣管理已迁至卖家 UI，无公开 API。

参数/必填字段以 swagger schema 为准（req body `required` 数组）：
    list_actions:            {limit?, offset?}（endpoint 无 body 参数，Ozon 容忍）
    action_products:         {action_id必填, offset, limit?}
    create_discount:         {date_end必填, date_start必填, min_action_percent必填, title?}
    create_voucher:          {title必填, budget必填, date_start必填, date_end必填,
                              discount_type必填(PERCENT|CURRENCY), discount_value必填,
                              voucher_parameters必填{count_codes必填, is_private必填,
                              type必填(ONE|MULTIPLE|UNIQUE)}, user_ids?}
    list_seller_actions:     {limit必填, offset?, search?, action_type?[DISCOUNT|VOUCHER_DISCOUNT|...],
                              status?[ACTIVE|ENDED|PLANNED|PAUSED], action_ids?}
    add_action_products:     {action_id必填, products必填[{sku必填, discount_percent?, currency?[RUB..CNY]}]}

Performance API 前缀：promo_client 白名单禁止任何 `api/client` 端点（专指广告投放）。
"""
from __future__ import annotations

from typing import Any

# Performance API 前缀：广告投放用（独立 OAuth），promo_client 不触碰（在 roadmap）。
PERFORMANCE_API_PREFIX = "/api/client"

# 本模块允许使用的 Seller 端点白名单（测试 test_no_performance_api_called 锁定）
ENDPOINT_LIST_ACTIONS = "/v1/actions"
ENDPOINT_ACTION_PRODUCTS = "/v1/actions/products"
ENDPOINT_CREATE_DISCOUNT = "/v1/seller-actions/create/discount"
ENDPOINT_CREATE_VOUCHER = "/v1/seller-actions/create/voucher"
ENDPOINT_LIST_SELLER_ACTIONS = "/v1/seller-actions/list"
ENDPOINT_ADD_ACTION_PRODUCTS = "/v1/seller-actions/products/add"

ALLOWED_ENDPOINTS = frozenset({
    ENDPOINT_LIST_ACTIONS,
    ENDPOINT_ACTION_PRODUCTS,
    ENDPOINT_CREATE_DISCOUNT,
    ENDPOINT_CREATE_VOUCHER,
    ENDPOINT_LIST_SELLER_ACTIONS,
    ENDPOINT_ADD_ACTION_PRODUCTS,
})

# 便捷映射：方法名 → endpoint（测试用它枚举本模块实际使用的端点，反查白名单）
METHOD_ENDPOINTS: dict[str, str] = {
    "list_actions": ENDPOINT_LIST_ACTIONS,
    "action_products": ENDPOINT_ACTION_PRODUCTS,
    "create_discount": ENDPOINT_CREATE_DISCOUNT,
    "create_voucher": ENDPOINT_CREATE_VOUCHER,
    "list_seller_actions": ENDPOINT_LIST_SELLER_ACTIONS,
    "add_action_products": ENDPOINT_ADD_ACTION_PRODUCTS,
}

# Ozon 字典属性/枚举硬约束（swagger enum，勿放行枚举外值）
DISCOUNT_TYPES = frozenset({"PERCENT", "CURRENCY"})
VOUCHER_TYPES = frozenset({"ONE", "MULTIPLE", "UNIQUE"})


def _post(client_id: str, api_key: str, endpoint: str, body: dict[str, Any], timeout: int = 30) -> dict:
    """统一 POST 包装：返回 `result`；异常由 ozon_post 抛出（供上层 502/403 映射）。"""
    if endpoint not in ALLOWED_ENDPOINTS:
        raise ValueError(f"promo_client 白名单拒绝端点 {endpoint}")
    # 延迟导入（对齐 shelf_service._bulk_action）：模块顶层 import 会让测试
    # patch utils.ozon_client.ozon_post 失效，本地 import 保持可 mock。
    from utils.ozon_client import ozon_post

    resp = ozon_post(client_id, api_key, endpoint, body, timeout=timeout, language="RU")
    return resp.get("result") or {}


def list_actions(
    client_id: str,
    api_key: str,
    limit: int | None = None,
    offset: int | None = None,
    timeout: int = 30,
) -> dict:
    """参与中的营销活动列表（/v1/actions）。"""
    body: dict[str, Any] = {}
    if limit is not None:
        body["limit"] = limit
    if offset is not None:
        body["offset"] = offset
    return _post(client_id, api_key, ENDPOINT_LIST_ACTIONS, body, timeout=timeout)


def action_products(
    client_id: str,
    api_key: str,
    action_id: int,
    offset: int = 0,
    limit: int | None = None,
    timeout: int = 30,
) -> dict:
    """某活动下的商品列表（/v1/actions/products）。action_id 必填。"""
    body: dict[str, Any] = {"action_id": action_id, "offset": offset}
    if limit is not None:
        body["limit"] = limit
    return _post(client_id, api_key, ENDPOINT_ACTION_PRODUCTS, body, timeout=timeout)


def create_discount(
    client_id: str,
    api_key: str,
    date_start: str,
    date_end: str,
    min_action_percent: float,
    title: str | None = None,
    timeout: int = 30,
) -> dict:
    """自建折扣活动（/v1/seller-actions/create/discount）。

    required: date_end/date_start/min_action_percent（swagger 断言）；title 可选。
    """
    body: dict[str, Any] = {
        "date_start": date_start,
        "date_end": date_end,
        "min_action_percent": min_action_percent,
    }
    if title:
        body["title"] = title
    return _post(client_id, api_key, ENDPOINT_CREATE_DISCOUNT, body, timeout=timeout)


def create_voucher(
    client_id: str,
    api_key: str,
    *,
    title: str,
    budget: int,
    date_start: str,
    date_end: str,
    discount_type: str,
    discount_value: float,
    voucher_parameters: dict[str, Any],
    user_ids: list[str] | None = None,
    timeout: int = 30,
) -> dict:
    """自建优惠券（/v1/seller-actions/create/voucher）。

    discount_type ∈ {PERCENT, CURRENCY}；voucher_parameters.type ∈ {ONE, MULTIPLE, UNIQUE}，
    voucher_parameters.count_codes/is_private 必填。均属 swagger 必填断言。
    """
    if discount_type not in DISCOUNT_TYPES:
        raise ValueError(f"discount_type 必须是 {sorted(DISCOUNT_TYPES)}，收到 {discount_type!r}")
    vp = dict(voucher_parameters)
    if vp.get("type") not in VOUCHER_TYPES:
        raise ValueError(f"voucher_parameters.type 必须是 {sorted(VOUCHER_TYPES)}，收到 {vp.get('type')!r}")
    body: dict[str, Any] = {
        "title": title,
        "budget": budget,
        "date_start": date_start,
        "date_end": date_end,
        "discount_type": discount_type,
        "discount_value": discount_value,
        "voucher_parameters": vp,
    }
    if user_ids:
        body["user_ids"] = user_ids
    return _post(client_id, api_key, ENDPOINT_CREATE_VOUCHER, body, timeout=timeout)


def list_seller_actions(
    client_id: str,
    api_key: str,
    limit: int,
    offset: int | None = None,
    search: str | None = None,
    action_type: list[str] | None = None,
    status: list[str] | None = None,
    action_ids: list[str] | None = None,
    timeout: int = 30,
) -> dict:
    """卖家自建活动列表（/v1/seller-actions/list）。limit 必填。"""
    body: dict[str, Any] = {"limit": limit}
    if offset is not None:
        body["offset"] = offset
    if search:
        body["search"] = search
    if action_type:
        body["action_type"] = action_type
    if status:
        body["status"] = status
    if action_ids:
        body["action_ids"] = action_ids
    return _post(client_id, api_key, ENDPOINT_LIST_SELLER_ACTIONS, body, timeout=timeout)


def add_action_products(
    client_id: str,
    api_key: str,
    action_id: int,
    products: list[dict[str, Any]],
    timeout: int = 30,
) -> dict:
    """「活动报名」：把商品加进某卖家活动（/v1/seller-actions/products/add）。

    action_id 整数必填；products 每项 {sku 必填, discount_percent?, currency?（RUB..CNY）}。
    """
    body: dict[str, Any] = {"action_id": action_id, "products": products}
    return _post(client_id, api_key, ENDPOINT_ADD_ACTION_PRODUCTS, body, timeout=timeout)
