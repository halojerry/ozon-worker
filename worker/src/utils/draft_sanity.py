"""信封合理性防线（v0.21 P2）— 防止脏尺寸/脏重量再次打爆定价。

根因（2026-08-04 实证）：skill 侧尺寸单位误判 + density 兜底把重量放大到
30kg~364kg，worker 此前只校验"非负"，放大后的重量直接进物流费 → 价格爆炸
（2134/25290/5837 CNY 三个错误案例）。
"""
from __future__ import annotations

from typing import Any

MAX_WEIGHT_G = 50000   # 50kg 物理上限（跑步机/沙发等大件仍远低于此）
MAX_DIM_MM = 5000      # 单边 5m 物理上限


def check_weight_suspect(weight_g: Any, dimensions: dict | None) -> dict:
    """检查重量/尺寸是否超出物理合理范围。

    返回 {"suspect": bool, "reason": str}。仅拦截明显脏数据，不拦截正常大件。
    """
    try:
        weight = float(weight_g or 0)
    except (TypeError, ValueError):
        weight = 0.0
    reasons: list[str] = []
    if weight > MAX_WEIGHT_G:
        reasons.append(f"weight={weight:g}g > {MAX_WEIGHT_G}g")
    for key in ("length", "width", "height"):
        dim_val = (dimensions or {}).get(key, 0)
        if isinstance(dim_val, (int, float)) and dim_val > MAX_DIM_MM:
            reasons.append(f"dimensions.{key}={dim_val:g}mm > {MAX_DIM_MM}mm")
    return {"suspect": bool(reasons), "reason": "; ".join(reasons)}


def validate_draft_sanity(draft: dict | None, extensions: dict | None = None) -> str | None:
    """入队前校验：返回错误信息；通过返回 None。

    C2 (sentry-attribute-fixes): 支持竞品兜底放行——1688 缺重量/尺寸但信封
    extensions 提供了竞品 what_to_sell 数据（competitor_weight_g /
    competitor_dimensions_mm）时放行，由 prepare 节点用竞品数据兜底。
    """
    if not isinstance(draft, dict):
        return None
    # v0.28.5 D1: 重量缺失/为零 → 定价无意义(此前仅拦超大, 0 重量直接进物流费)
    try:
        weight = float(draft.get("weight") or 0)
    except (TypeError, ValueError):
        weight = 0.0
    # C2: 竞品重量放行（None-guard：extensions 为 None 时绝不能 .get() 崩溃）
    has_competitor_weight = bool(extensions and extensions.get("competitor_weight_g", 0) > 0)
    if weight <= 0 and not has_competitor_weight:
        return "weight 缺失或为 0(无法定价)"
    # v0.28.5 D1: 尺寸缺失/含 0 → 无效(此前仅拦负数, 0 尺寸会让密度兜底乱估)
    dims = draft.get("dimensions") or {}
    # C2: 竞品尺寸兜底——draft 某维无效时用 competitor_dimensions_mm 对应维替换后再校验
    competitor_dims = (extensions or {}).get("competitor_dimensions_mm") or {}
    if not isinstance(competitor_dims, dict):
        competitor_dims = {}
    dim_vals = []
    for k in ("length", "width", "height"):
        v = dims.get(k)
        if isinstance(v, (int, float)) and v > 0:
            dim_vals.append(v)
            continue
        cv = competitor_dims.get(k, 0)
        if isinstance(cv, (int, float)) and cv > 0:
            dim_vals.append(cv)  # 竞品对应维为正数 → 放行
        else:
            dim_vals.append(v)   # 双方均无效 → 维持原拒绝逻辑
    if not all(isinstance(v, (int, float)) and v > 0 for v in dim_vals):
        return "dimensions 缺失或含 0(尺寸无效)"
    out = check_weight_suspect(draft.get("weight"), draft.get("dimensions"))
    return out["reason"] if out["suspect"] else None
