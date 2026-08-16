"""C3: 蓝海关键词库管理服务 — 管理员导入/浏览/删除 blue_ocean_queries。

与 analytics_service 同风格（raw SQL text() + 绑定参数，get_engine() 连接池）。
管理员导入的行固定贡献者 `admin_import` + 来源 `admin`，与 skill 用户上报
（contributed_by_token_id=用户 token, source=fetched）天然隔离；
去重键 (query, contributed_by_token_id) 复用表级 UniqueConstraint
uq_blue_ocean_query_token——同一关键词重复导入走 ON CONFLICT 更新，不产生重复行。
"""

import csv
import io
from typing import Any, Final, Optional

from sqlalchemy import text

from storage.database.db import get_engine

# 管理员导入专用身份（skill 上报通道不感知，用户数据零污染）
ADMIN_TOKEN_ID: Final[str] = "admin_import"
ADMIN_SOURCE: Final[str] = "admin"

_INT_FIELDS: Final[tuple[str, ...]] = ("count", "uniq_queries_wca")
_FLOAT_FIELDS: Final[tuple[str, ...]] = (
    "ca", "avg_ca_rub", "avg_count_items", "items_views", "uniq_sellers",
)

_INVALID = object()  # 数字强转失败哨兵（None/"" 合法语义是「空」）

_UPSERT_SQL = text("""
    INSERT INTO blue_ocean_queries
        (query, count, ca, avg_ca_rub, avg_count_items, items_views,
         uniq_queries_wca, uniq_sellers, contributed_by_token_id, source)
    VALUES
        (:query, :count, :ca, :avg_ca_rub, :avg_count_items, :items_views,
         :uniq_queries_wca, :uniq_sellers, :contributed_by_token_id, :source)
    ON CONFLICT (query, contributed_by_token_id) DO UPDATE SET
        count = EXCLUDED.count,
        ca = EXCLUDED.ca,
        avg_ca_rub = EXCLUDED.avg_ca_rub,
        avg_count_items = EXCLUDED.avg_count_items,
        items_views = EXCLUDED.items_views,
        uniq_queries_wca = EXCLUDED.uniq_queries_wca,
        uniq_sellers = EXCLUDED.uniq_sellers,
        source = EXCLUDED.source
    RETURNING (xmax = 0) AS inserted
""")

_SELECT_COLS = (
    "id, query, count, ca, avg_ca_rub, avg_count_items, items_views, "
    "uniq_queries_wca, uniq_sellers, source, created_at"
)


def _coerce_int(value: Any, default: int = 0) -> int | Any:
    """整数字段强转；None/空串 → default，坏值 → _INVALID 哨兵。"""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return _INVALID


def _coerce_float(value: Any) -> float | Any:
    """浮点字段强转；None/空串 → None，坏值 → _INVALID 哨兵。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return _INVALID


def _parse_row(item: dict[str, Any], row_idx: int, errors: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """单行校验 + 数字强转；失败记 errors 并返回 None（跳过该行）。

    query 必填非空；任一数字字段坏值 → 整行跳过（与规格「skip + error」一致）。
    """
    query = str(item.get("query") or "").strip()
    if not query:
        errors.append({"row": row_idx, "error": "query 必填"})
        return None
    params: dict[str, Any] = {"query": query}
    for field in _INT_FIELDS:
        coerced = _coerce_int(item.get(field))
        if coerced is _INVALID:
            errors.append({"row": row_idx, "error": f"{field} 不是有效整数: {item.get(field)!r}"})
            return None
        params[field] = coerced
    for field in _FLOAT_FIELDS:
        coerced = _coerce_float(item.get(field))
        if coerced is _INVALID:
            errors.append({"row": row_idx, "error": f"{field} 不是有效数字: {item.get(field)!r}"})
            return None
        params[field] = coerced
    params["contributed_by_token_id"] = ADMIN_TOKEN_ID
    params["source"] = ADMIN_SOURCE
    return params


def _upsert_rows(params_list: list[dict[str, Any]]) -> tuple[int, int]:
    """逐行 upsert（单事务）；RETURNING xmax=0 → 新插入，否则更新。返回 (imported, updated)。"""
    imported = 0
    updated = 0
    with get_engine().begin() as conn:
        for params in params_list:
            inserted = conn.execute(_UPSERT_SQL, params).scalar()
            if inserted:
                imported += 1
            else:
                updated += 1
    return imported, updated


def list_queries(limit: int = 50, offset: int = 0, search: str = "") -> dict[str, Any]:
    """浏览蓝海关键词库：可选 query ILIKE 搜索，固定按 created_at 倒序（白名单排序）。

    返回 {total, items}；limit 收敛到 [1, 200]。
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if search:
        where.append("query ILIKE :search")
        params["search"] = f"%{search}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_SELECT_COLS} FROM blue_ocean_queries {where_sql} "
            f"ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
        ), params).fetchall()
        total = conn.execute(text(
            f"SELECT COUNT(*) FROM blue_ocean_queries {where_sql}"
        ), {"search": params.get("search")}).scalar()

    items = [{
        "id": int(r[0]),
        "query": str(r[1]),
        "count": int(r[2]),
        "ca": float(r[3]) if r[3] is not None else None,
        "avg_ca_rub": float(r[4]) if r[4] is not None else None,
        "avg_count_items": float(r[5]) if r[5] is not None else None,
        "items_views": float(r[6]) if r[6] is not None else None,
        "uniq_queries_wca": int(r[7]) if r[7] is not None else None,
        "uniq_sellers": float(r[8]) if r[8] is not None else None,
        "source": str(r[9]),
        "created_at": r[10].isoformat() if r[10] is not None else None,
    } for r in rows]
    return {"total": int(total or 0), "items": items}


def import_queries(items: list[dict]) -> dict[str, Any]:
    """批量导入（JSON items）：逐行校验，坏行跳过记 errors，有效行 upsert。

    返回 {imported, updated, errors:[{row, error}]}。
    """
    errors: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        row = _parse_row(item, idx, errors)
        if row is not None:
            valid.append(row)
    imported, updated = _upsert_rows(valid) if valid else (0, 0)
    return {"imported": imported, "updated": updated, "errors": errors}


def import_queries_csv(csv_text: str) -> dict[str, Any]:
    """CSV 导入：utf-8-sig 剥 BOM，DictReader 按列名映射到同一 upsert 路径。

    列名: query/count/ca/avg_ca_rub/avg_count_items/items_views/uniq_queries_wca/uniq_sellers。
    空行静默跳过（不记错误）；errors 的 row 为物理行号（表头=1，首数据行=2）。
    """
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    errors: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for row in reader:
        row_idx = reader.line_num
        filled = {k: v for k, v in row.items() if v is not None}
        if not filled or not any(str(v).strip() for v in filled.values()):
            continue  # 空行（文件尾换行等）静默跳过
        parsed = _parse_row(filled, row_idx, errors)
        if parsed is not None:
            valid.append(parsed)
    imported, updated = _upsert_rows(valid) if valid else (0, 0)
    return {"imported": imported, "updated": updated, "errors": errors}


def delete_query(query_id: int) -> bool:
    """按 id 删除关键词行；删除成功返回 True，不存在返回 False。"""
    with get_engine().begin() as conn:
        result = conn.execute(
            text("DELETE FROM blue_ocean_queries WHERE id = :id"),
            {"id": int(query_id)},
        )
    return result.rowcount > 0
