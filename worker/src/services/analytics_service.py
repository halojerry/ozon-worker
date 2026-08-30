"""P2b: 榜单浏览服务 — 读取 skill 上报的 ozon-bestsellers 榜单数据。

只读：SELECT ozon_bestsellers（按 token/类目筛选 + 排序），不写任何数据。
"""

import logging
from typing import Any, Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)


def list_bestsellers(
    token: str,
    category: Optional[str] = None,
    order_by: str = "ordering_amount",
    limit: int = 50,
    offset: int = 0,
    brand: str = "",
    min_sales: Optional[float] = None,
    max_sales: Optional[float] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> dict[str, Any]:
    """全局浏览榜单（T4b.1：去掉 contributed_by_token_id 过滤，A 采集 B 可看）。

    order_by ∈ {ordering_amount 订购金额, ordering_count 订购数量, avg_price_rub 均价}
    保留 contributed_by_token_id 贡献者列（干净 token）供前端标注贡献者。
    token 仅作鉴权入参（端点层已验证），不再作数据过滤。
    """
    allowed_order = {"ordering_amount", "ordering_count", "avg_price_rub"}
    sort_col = order_by if order_by in allowed_order else "ordering_amount"
    order_dir = "DESC"

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where: list[str] = []
    params: dict = {"limit": limit, "offset": offset}
    if category:
        where.append("category_path ILIKE :cat")
        params["cat"] = f"%{category}%"
    if brand:
        where.append("brand ILIKE :brand")
        params["brand"] = f"%{brand}%"
    if min_sales is not None:
        where.append("ordering_count >= :min_sales")
        params["min_sales"] = min_sales
    if max_sales is not None:
        where.append("ordering_count <= :max_sales")
        params["max_sales"] = max_sales
    if min_price is not None:
        where.append("avg_price_rub >= :min_price")
        params["min_price"] = min_price
    if max_price is not None:
        where.append("avg_price_rub <= :max_price")
        params["max_price"] = max_price

    where_sql = " AND ".join(where) if where else "TRUE"
    filter_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT sku_or_id, brand, category_path, ordering_amount, ordering_count, avg_price_rub, "
            f"contributed_by_token_id "
            f"FROM ozon_bestsellers WHERE {where_sql} "
            f"ORDER BY {sort_col} {order_dir} NULLS LAST LIMIT :limit OFFSET :offset"
        ), params).fetchall()
        total = conn.execute(text(
            f"SELECT COUNT(*) FROM ozon_bestsellers WHERE {where_sql}"
        ), filter_params).scalar()

    items = [{
        "sku_or_id": str(r[0]),
        "brand": str(r[1] or ""),
        "category_path": str(r[2] or ""),
        "ordering_amount": float(r[3]) if r[3] is not None else None,
        "ordering_count": int(r[4]) if r[4] is not None else None,
        "avg_price_rub": float(r[5]) if r[5] is not None else None,
        "contributed_by_token_id": str(r[6] or ""),
    } for r in rows]
    return {"items": items, "total": int(total or 0), "limit": limit, "offset": offset}
