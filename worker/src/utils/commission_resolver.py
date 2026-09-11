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

import time
from datetime import datetime
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

# ── BL-24 一期：佣金缓存 180d 新鲜度闸（docs/audit/2026-09-11-repo-gov/design-b2b-cache-ttl-governance.md §2 选项 3）──
# Ozon 佣金是「类目×发货模式×价格段」三维矩阵且平台侧会调整，一年前的行照常
# 参与定价 = 系统性价格偏差（隐性资资损，无人报警）。超龄 → 视同未命中降级
# segments/fallback；0.10 fallback 是保守方向（宁可利润算保守不可虚高）。
# 180d 是拍的保守值（Ozon 佣金无公开变更频率数据，取半年）。
COMMISSION_STALE_AFTER_DAYS = 180

# 超龄降级时无 segments 兜底的 source 标记（与既有 source 枚举风格一致，排查链 grep 兼容）
STALE_FALLBACK_SOURCE = "fallback:stale"


def _updated_at_epoch(value: Any) -> Optional[float]:
    """updated_at 值归一为 epoch 秒（datetime/数值/None 兼容）。

    None / 无法识别 → None（调用方保守视同超龄——历史行 updated_at 为空的
    降信方向见设计 §3 Phase 1.4）。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (OverflowError, OSError, ValueError):
            return None
    return None


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
    ✅ v0.67 wave 修复：值 <=0 同样 → None（Ozon 响应缺 commissions 块时常给 0，
    0% 不是真实佣金——回填侧会据此 upsert 0 污染缓存表，A6 实证）。
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
        pct_f = float(pct)
    except (TypeError, ValueError):
        return None
    if pct_f <= 0:
        return None
    return pct_f / 100.0


def resolve_commission_rate_detail(
    description_category_id: Optional[int],
    price_rub: Optional[float],
    explicit_commission: Optional[float],
    extensions_commission_segments: Optional[dict] = None,
    get_category_commission_fn: Optional[Callable[[int], Optional[dict]]] = None,
    *,
    stale_after_days: int = COMMISSION_STALE_AFTER_DAYS,
    now_fn: Optional[Callable[[], float]] = None,
) -> dict:
    """按优先级链解析佣金率（带新鲜度详情）：explicit > 缓存表(band 选段) > extensions segments > 0.10。

    BL-24 一期核心：缓存行带 ``updated_at`` 新鲜度闸——行龄 > ``stale_after_days``
    （默认 180d）**不再直接采信**，降级走 extensions segments / fallback，
    ``stale=True`` 标记降级；``updated_at`` 缺失的历史行同样保守视同超龄
    （真实生产行由 server_default 保证非空，此分支仅命中 mock/异常行）。
    segments 命中时 source 仍如实标 ``segments:{band}``（source 描述数值真实
    来源，stale 标志描述「缓存行因超龄被弃用」）；segments 也未命中时
    source=``fallback:stale``（设计验收探针锁定）。

    Args:
        description_category_id: Ozon 类目 ID（仅缓存路径使用）。
        price_rub: RUB 售价，决定价格段。
        explicit_commission: 显式佣金率（如 0.12，>0 即优先，已是比例不用 /100）。
        extensions_commission_segments: `{"fbs": {"leq_1500": 8.0, ...}, "fbo": {...}}`
            —— 内层值为百分比，段值百分比 /100 后返回。
        get_category_commission_fn: 缓存查询注入点（默认 None 时不查缓存表）。
        stale_after_days: 缓存行新鲜度阈值（天），keyword-only。
        now_fn: 时间注入点（默认 time.time），keyword-only，测试用。

    Returns:
        dict: ``{"rate": float, "source": str, "stale": bool}``；
        source ∈ "explicit" / "cache:{band}" / "segments:{band}" / "fallback" /
        "fallback:stale"。
    """
    band = pick_price_band(price_rub)
    if explicit_commission and explicit_commission > 0:
        return {"rate": float(explicit_commission), "source": "explicit", "stale": False}
    stale_hit = False  # 缓存行存在但因超龄/缺 updated_at 被弃用（区别于污染行）
    if get_category_commission_fn is not None:
        row = get_category_commission_fn(description_category_id)
        if row:
            pct = select_segment(row, DEFAULT_PREFIX, band)
            # ✅ v0.67 wave 修复：段值 <=0（历史污染行/响应缺块写出的 0）视同未命中，
            # 继续走 segments/fallback——0% 佣金采信会让定价利润虚高（A6 实证）。
            # 污染行不标 stale（stale 专指超龄降信，非段值缺失）。
            if pct is not None and pct > 0:
                ts = _updated_at_epoch(row.get("updated_at"))
                now = time.time() if now_fn is None else now_fn()
                age_seconds = None if ts is None else now - ts
                if age_seconds is not None and age_seconds <= stale_after_days * 86400:
                    return {"rate": pct / 100.0, "source": f"cache:{band}", "stale": False}
                stale_hit = True
    if extensions_commission_segments:
        fbs_segments = extensions_commission_segments.get(DEFAULT_PREFIX)
        if fbs_segments:
            pct = select_segment(fbs_segments, "", band)
            if pct is not None:
                return {"rate": pct / 100.0, "source": f"segments:{band}", "stale": stale_hit}
    if stale_hit:
        return {"rate": FALLBACK_RATE, "source": STALE_FALLBACK_SOURCE, "stale": True}
    return {"rate": FALLBACK_RATE, "source": "fallback", "stale": False}


def resolve_commission_rate(
    description_category_id: Optional[int],
    price_rub: Optional[float],
    explicit_commission: Optional[float],
    extensions_commission_segments: Optional[dict] = None,
    get_category_commission_fn: Optional[Callable[[int], Optional[dict]]] = None,
) -> tuple[float, str]:
    """按优先级链解析佣金率：explicit > 缓存表(band 选段) > extensions segments > 0.10。

    ⚠️ BL-24 一期行为变化（有意变更，PR 说明必提）：本函数已变为
    :func:`resolve_commission_rate_detail` 的薄壳——缓存行 ``updated_at``
    距今 >180 天（或缺失）时**不再直接采信**，降级走 extensions/0.10 并
    落 source=``fallback:stale``。新鲜行（≤180d）行为与旧版逐字一致；
    需要区分降级的调用方请改用 detail 版。三处调用方（pricing_node /
    estimate_service / 本模块 lookup）自动生效。

    Args:
        description_category_id: Ozon 类目 ID（仅缓存路径使用）。
        price_rub: RUB 售价，决定价格段。
        explicit_commission: 显式佣金率（如 0.12，>0 即优先，已是比例不用 /100）。
        extensions_commission_segments: `{"fbs": {"leq_1500": 8.0, ...}, "fbo": {...}}`
            —— 内层值为百分比，段值百分比 /100 后返回。
        get_category_commission_fn: 缓存查询注入点（默认 None 时不查缓存表）。

    Returns:
        (rate, source)；source ∈ "explicit" / "cache:{band}" / "segments:{band}" /
        "fallback" / "fallback:stale"。
    """
    detail = resolve_commission_rate_detail(
        description_category_id,
        price_rub,
        explicit_commission,
        extensions_commission_segments=extensions_commission_segments,
        get_category_commission_fn=get_category_commission_fn,
    )
    return detail["rate"], detail["source"]


def get_category_commission(
    description_category_id: int, session: Any = None
) -> Optional[dict]:
    """PG 查 category_commission 表 → dict（6 段值 + source + updated_at）；无记录 → None。

    BL-24 一期：返回 dict 增加 ``updated_at``（epoch 秒，timezone-aware
    datetime 归一）——供 ``resolve_commission_rate_detail`` 的 180d 新鲜度闸
    消费；lookup 端点响应是白名单键组装，新增键对其零影响。
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
    result["updated_at"] = _updated_at_epoch(getattr(row, "updated_at", None))
    return result


def upsert_category_commission(
    description_category_id: int,
    source: str = "what_to_sell",
    session: Any = None,
    **segments: Optional[float],
) -> None:
    """PG upsert category_commission：ON CONFLICT (description_category_id) DO UPDATE。

    segments 形如 `fbs_leq_1500=8.0`（百分比）；source / updated_at 随 upsert 刷新。
    BL-24 Phase 3-7（随用续期）：DO UPDATE SET 显式带 ``updated_at=func.now()``
    （建表起既有）——被 180d 新鲜度闸判超龄的行随 what_to_sell 分段 upsert 自然
    续期，闸即解除；fresh INSERT 分支由列 server_default=func.now() 兜底
    （model.py）。语句级 + 真实 PG 行为双测锁定（test_commission_stale_v075.py）。
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
