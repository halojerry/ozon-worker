"""C2: 物流费率管理服务 — logistics_rates 表 CRUD + CSV 导入（upsert）。

只读消费方 logistics_quote.py 每次实时查表（无缓存），本服务任何更新立即生效。
表结构（model.py LogisticsRate）：无唯一约束 → 导入 upsert 在代码层按
(scoring_group, service_level, tpl_provider, weight_min, weight_max) 匹配。
"""
import csv
import io
import logging
from typing import Any, Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# list_rates 固定排序（定价匹配按评分组/服务等级/重量区间线性扫描，顺序稳定即可）
_ORDER_BY = "scoring_group, service_level, weight_min, id"

_COLUMNS = (
    "id, scoring_group, service_level, tpl_provider, delivery_method, base_cost, "
    "per_gram_rate, weight_min, weight_max, sum_limit_cm, longest_limit_cm, "
    "charge_type, vol_weight_divisor, created_at"
)

# CSV 必填列（vol_weight_divisor/delivery_method 可选）
_REQUIRED_COLUMNS = [
    "scoring_group", "service_level", "tpl_provider", "weight_min", "weight_max",
    "base_cost", "per_gram_rate", "sum_limit_cm", "longest_limit_cm", "charge_type",
]

# upsert 时 UPDATE 的字段（键字段不变）
_UPDATE_COLUMNS = [
    "base_cost", "per_gram_rate", "sum_limit_cm", "longest_limit_cm",
    "charge_type", "vol_weight_divisor", "delivery_method",
]


def _row_to_dict(row) -> dict[str, Any]:
    """SQLAlchemy Row → dict（None → None，不做空串替换，保持原始类型）。"""
    return {
        "id": int(row[0]),
        "scoring_group": str(row[1]),
        "service_level": str(row[2]),
        "tpl_provider": str(row[3]),
        "delivery_method": str(row[4]) if row[4] is not None else None,
        "base_cost": float(row[5]),
        "per_gram_rate": float(row[6]),
        "weight_min": int(row[7]),
        "weight_max": int(row[8]),
        "sum_limit_cm": int(row[9]),
        "longest_limit_cm": int(row[10]),
        "charge_type": str(row[11]),
        "vol_weight_divisor": int(row[12] or 0),
        "created_at": str(row[13]) if row[13] is not None else None,
    }


def _fetch_by_id(conn, rate_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(text(
        f"SELECT {_COLUMNS} FROM logistics_rates WHERE id=:rid"), {"rid": rate_id}
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_rates(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """费率列表：total + items（分页，固定排序，白名单 LIMIT/OFFSET 绑定参数）。"""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_COLUMNS} FROM logistics_rates "
            f"ORDER BY {_ORDER_BY} LIMIT :limit OFFSET :offset"
        ), {"limit": limit, "offset": offset}).fetchall()
        total = int(conn.execute(text(
            "SELECT COUNT(*) FROM logistics_rates")).scalar() or 0)
    return {"total": total, "items": [_row_to_dict(r) for r in rows]}


def update_rate(rate_id: int, data: dict) -> Optional[dict[str, Any]]:
    """更新单条费率：校验通过 → UPDATE → 返回更新后行；id 不存在 → None。

    校验失败抛 ValueError（路由层转 400）：
    - 必填 scoring_group/service_level/tpl_provider 非空
    - weight_min <= weight_max
    - vol_weight_divisor >= 0
    - base_cost / per_gram_rate >= 0
    """
    scoring_group = str(data.get("scoring_group") or "").strip()
    service_level = str(data.get("service_level") or "").strip()
    tpl_provider = str(data.get("tpl_provider") or "").strip()
    if not scoring_group or not service_level or not tpl_provider:
        raise ValueError("scoring_group/service_level/tpl_provider 不能为空")

    weight_min = int(data.get("weight_min") or 0)
    weight_max = int(data.get("weight_max") or 0)
    if weight_min > weight_max:
        raise ValueError("weight_min 不能大于 weight_max")

    vol_weight_divisor = int(data.get("vol_weight_divisor") or 0)
    if vol_weight_divisor < 0:
        raise ValueError("vol_weight_divisor 不能为负数")

    base_cost = float(data.get("base_cost") or 0)
    per_gram_rate = float(data.get("per_gram_rate") or 0)
    if base_cost < 0 or per_gram_rate < 0:
        raise ValueError("base_cost / per_gram_rate 不能为负数")

    delivery_method = data.get("delivery_method")
    delivery_method = str(delivery_method) if delivery_method not in (None, "") else None

    with get_engine().begin() as conn:
        result = conn.execute(text(
            "UPDATE logistics_rates SET scoring_group=:sg, service_level=:sl, "
            "tpl_provider=:tp, delivery_method=:dm, base_cost=:bc, per_gram_rate=:pgr, "
            "weight_min=:wmin, weight_max=:wmax, sum_limit_cm=:sum, longest_limit_cm=:long, "
            "charge_type=:ct, vol_weight_divisor=:vd WHERE id=:rid"
        ), {
            "sg": scoring_group, "sl": service_level, "tp": tpl_provider, "dm": delivery_method,
            "bc": base_cost, "pgr": per_gram_rate, "wmin": weight_min, "wmax": weight_max,
            "sum": int(data.get("sum_limit_cm") or 0), "long": int(data.get("longest_limit_cm") or 0),
            "ct": str(data.get("charge_type") or ""), "vd": vol_weight_divisor, "rid": rate_id,
        })
        if result.rowcount == 0:
            return None
        return _fetch_by_id(conn, rate_id)


def _parse_int(row_no: int, field: str, value: Any, errors: list[dict]) -> Optional[int]:
    """解析整数字段：失败 → 记 error，返回 None。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        errors.append({"row": row_no, "error": f"{field} 不是有效整数: {value!r}"})
        return None


def _parse_float(row_no: int, field: str, value: Any, errors: list[dict]) -> Optional[float]:
    """解析浮点字段：失败 → 记 error，返回 None。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        errors.append({"row": row_no, "error": f"{field} 不是有效数字: {value!r}"})
        return None


def import_rates_csv(csv_text: str) -> dict[str, Any]:
    """CSV 导入费率：按键 (scoring_group, service_level, tpl_provider, weight_min, weight_max)
    匹配已有行 → UPDATE，否则 INSERT。坏行跳过并记录 {row, error}，不影响其他行。

    返回 {imported: 新插入数, updated: 更新数, errors: [{row, error}]}。
    """
    imported = 0
    updated = 0
    errors: list[dict] = []

    # utf-8-sig 等价：剥掉 BOM（否则 \ufeff 粘在第一个列名上导致必填列判空）
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    with get_engine().begin() as conn:
        for idx, raw in enumerate(rows):
            row_no = idx + 2  # 第 1 行是表头

            def field(name: str) -> str:
                return str(raw.get(name) or "").strip()

            # 必填列缺失 → 跳过
            missing = [c for c in _REQUIRED_COLUMNS if field(c) == ""]
            if missing:
                errors.append({"row": row_no, "error": f"缺少必填列: {', '.join(missing)}"})
                continue

            weight_min = _parse_int(row_no, "weight_min", field("weight_min"), errors)
            weight_max = _parse_int(row_no, "weight_max", field("weight_max"), errors)
            sum_limit_cm = _parse_int(row_no, "sum_limit_cm", field("sum_limit_cm"), errors)
            longest_limit_cm = _parse_int(row_no, "longest_limit_cm", field("longest_limit_cm"), errors)
            base_cost = _parse_float(row_no, "base_cost", field("base_cost"), errors)
            per_gram_rate = _parse_float(row_no, "per_gram_rate", field("per_gram_rate"), errors)
            if any(v is None for v in (weight_min, weight_max, sum_limit_cm,
                                       longest_limit_cm, base_cost, per_gram_rate)):
                continue  # 数字解析失败已记 error

            # 数值越界校验（与 update_rate 一致）
            if weight_min > weight_max:
                errors.append({"row": row_no, "error": "weight_min 不能大于 weight_max"})
                continue
            vol_raw = field("vol_weight_divisor")
            vol_weight_divisor = _parse_int(row_no, "vol_weight_divisor", vol_raw, errors) if vol_raw else 0
            if vol_weight_divisor is None or vol_weight_divisor < 0:
                if vol_weight_divisor is not None:
                    errors.append({"row": row_no, "error": "vol_weight_divisor 不能为负数"})
                continue
            if base_cost < 0 or per_gram_rate < 0:
                errors.append({"row": row_no, "error": "base_cost / per_gram_rate 不能为负数"})
                continue

            # 按键匹配已有行 → UPDATE；否则 INSERT
            existing = conn.execute(text(
                "SELECT id FROM logistics_rates WHERE scoring_group=:sg AND service_level=:sl "
                "AND tpl_provider=:tp AND weight_min=:wmin AND weight_max=:wmax"
            ), {
                "sg": field("scoring_group"), "sl": field("service_level"),
                "tp": field("tpl_provider"), "wmin": weight_min, "wmax": weight_max,
            }).fetchone()

            delivery_method = field("delivery_method") or None
            if existing:
                conn.execute(text(
                    "UPDATE logistics_rates SET base_cost=:bc, per_gram_rate=:pgr, "
                    "sum_limit_cm=:sum, longest_limit_cm=:long, charge_type=:ct, "
                    "vol_weight_divisor=:vd, delivery_method=:dm WHERE id=:rid"
                ), {
                    "bc": base_cost, "pgr": per_gram_rate, "sum": sum_limit_cm,
                    "long": longest_limit_cm, "ct": field("charge_type"),
                    "vd": vol_weight_divisor, "dm": delivery_method, "rid": int(existing[0]),
                })
                updated += 1
            else:
                conn.execute(text(
                    "INSERT INTO logistics_rates (scoring_group, service_level, tpl_provider, "
                    "delivery_method, base_cost, per_gram_rate, weight_min, weight_max, "
                    "sum_limit_cm, longest_limit_cm, charge_type, vol_weight_divisor) "
                    "VALUES (:sg, :sl, :tp, :dm, :bc, :pgr, :wmin, :wmax, :sum, :long, :ct, :vd)"
                ), {
                    "sg": field("scoring_group"), "sl": field("service_level"),
                    "tp": field("tpl_provider"), "dm": delivery_method, "bc": base_cost,
                    "pgr": per_gram_rate, "wmin": weight_min, "wmax": weight_max,
                    "sum": sum_limit_cm, "long": longest_limit_cm, "ct": field("charge_type"),
                    "vd": vol_weight_divisor,
                })
                imported += 1

    return {"imported": imported, "updated": updated, "errors": errors}
