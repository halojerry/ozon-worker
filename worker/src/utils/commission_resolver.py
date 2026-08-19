"""佣金解析共享模块 — Ozon 类目佣金（FBS/FBO 价格分段）唯一解析入口（任务 1.2）。

- `parse_prices_commissions`：/v5/product/info/prices 响应解析的唯一入口（修复「result.commissions
  路径错」——只认 `items[0].commissions.sales_percent_rfbs` 结构，/100 返回比例）。
- `resolve_commission_rate`：优先级链 explicit > 缓存表(band 选段) > extensions segments > 0.10，
  产出 (rate, source) 供定价节点消费。
- `get_category_commission` / `upsert_category_commission`：category_commission 缓存表读写
  （全局共享，无 tenant_id，对齐 category_mapping W11）；session 可注入便于测试。

⚠️ 定价公式在 `pricing_estimate.compute_price`（本模块不改），本模块只产 commission_rate。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from storage.database.shared.model import CategoryCommission

# FBS 是 worker 主履约通道；FBO 段仍入库，解析时按需换 prefix
DEFAULT_PREFIX = "fbs"

_SEGMENT_KEYS = (
    "fbs_leq_1500",
    "fbs_leq_5000",
    "fbs_gt_5000",
    "fbo_leq_1500",
    "fbo_leq_5000",
    "fbo_gt_5000",
)

FALLBACK_RATE = 0.10


def pick_price_band(price_rub: Optional[float]) -> str:
    """按 RUB 售价选价格段：≤1500 → leq_1500；1501-5000 → leq_5000；>5000 → gt_5000。

    价格 ≤0 或 None → leq_1500（最保守分段）。
    """
    if not price_rub or price_rub <= 0:
        return "leq_1500"
    if price_rub <= 1500:
        return "leq_1500"
    if price_rub <= 5000:
        return "leq_5000"
    return "gt_5000"


def select_segment(row: dict, prefix: str, band: str) -> Optional[float]:
    """从 dict 取 `{prefix}_{band}` 键（如 prefix="fbs", band="leq_1500" → "fbs_leq_1500"）。

    prefix 为空 → 直接取 band 键（extensions segments 内层为裸 band 键）。
    值 None/空/非数字 → None，否则返回 float。
    """
    key = f"{prefix}_{band}" if prefix else band
    raw = row.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_prices_commissions(prices_resp: Optional[dict]) -> Optional[float]:
    """解析 /v5/product/info/prices 响应 → 佣金比例（0-1）。

    读 `items[0].commissions.sales_percent_rfbs`（缺失回退 `sales_percent_fbp`），/100 返回。
    items 空 / commissions 缺失 / 字段缺失或非数字 / 响应非 dict → None。
    """
    if not prices_resp or not isinstance(prices_resp, dict):
        return None
    items = prices_resp.get("items")
    if not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    commissions = first.get("commissions") or {}
    pct = commissions.get("sales_percent_rfbs")
    if pct is None:
        pct = commissions.get("sales_percent_fbp")
    if pct is None:
        return None
    try:
        return float(pct) / 100.0
    except (TypeError, ValueError):
        return None


def resolve_commission_rate(
    description_category_id: Optional[int],
    price_rub: Optional[float],
    explicit_commission: Optional[float],
    extensions_commission_segments: Optional[dict] = None,
    get_category_commission_fn: Optional[Callable[[int], Optional[dict]]] = None,
) -> tuple[float, str]:
    """按优先级链解析佣金率：explicit > 缓存表(band 选段) > extensions segments > 0.10。

    Args:
        description_category_id: Ozon 类目 ID（仅缓存路径使用）。
        price_rub: RUB 售价，决定价格段。
        explicit_commission: 显式佣金率（如 0.12，>0 即优先，已是比例不用 /100）。
        extensions_commission_segments: `{"fbs": {"leq_1500": 8.0, ...}, "fbo": {...}}`
            —— 内层值为百分比，段值百分比 /100 后返回。
        get_category_commission_fn: 缓存查询注入点（默认 None 时不查缓存表）。

    Returns:
        (rate, source)；source ∈ "explicit" / "cache:{band}" / "segments:{band}" / "fallback"。
    """
    band = pick_price_band(price_rub)
    if explicit_commission and explicit_commission > 0:
        return float(explicit_commission), "explicit"
    if get_category_commission_fn is not None:
        row = get_category_commission_fn(description_category_id)
        if row:
            pct = select_segment(row, DEFAULT_PREFIX, band)
            if pct is not None:
                return pct / 100.0, f"cache:{band}"
    if extensions_commission_segments:
        fbs_segments = extensions_commission_segments.get(DEFAULT_PREFIX)
        if fbs_segments:
            pct = select_segment(fbs_segments, "", band)
            if pct is not None:
                return pct / 100.0, f"segments:{band}"
    return FALLBACK_RATE, "fallback"


def get_category_commission(
    description_category_id: int, session: Any = None
) -> Optional[dict]:
    """PG 查 category_commission 表 → dict（6 段值 + source）；无记录 → None。

    session 可注入（测试传 mock，不连真实 PG）；默认从 storage.database.db 取真实 session
    （惰性导入，避免模块加载即依赖 PG）。
    """
    own_session = session is None
    if own_session:
        from storage.database.db import get_session

        session = get_session()
    try:
        row = (
            session.execute(
                select(CategoryCommission).where(
                    CategoryCommission.description_category_id == description_category_id
                )
            )
            .scalars()
            .first()
        )
    finally:
        if own_session:
            session.close()
    if row is None:
        return None
    result = {key: getattr(row, key) for key in _SEGMENT_KEYS}
    result["source"] = row.source
    return result


def upsert_category_commission(
    description_category_id: int,
    source: str = "what_to_sell",
    session: Any = None,
    **segments: Optional[float],
) -> None:
    """PG upsert category_commission：ON CONFLICT (description_category_id) DO UPDATE。

    segments 形如 `fbs_leq_1500=8.0`（百分比）；source / updated_at 随 upsert 刷新。
    session 可注入（测试传 mock）；默认取真实 session（惰性导入）。
    """
    own_session = session is None
    if own_session:
        from storage.database.db import get_session

        session = get_session()
    try:
        stmt = insert(CategoryCommission).values(
            description_category_id=description_category_id,
            source=source,
            **segments,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[CategoryCommission.description_category_id],
            set_={
                **segments,
                "source": source,
                "updated_at": func.now(),
            },
        )
        session.execute(stmt)
        session.commit()
    finally:
        if own_session:
            session.close()
