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


def validate_draft_sanity(draft: dict | None) -> str | None:
    """入队前校验：返回错误信息；通过返回 None。"""
    if not isinstance(draft, dict):
        return None
    out = check_weight_suspect(draft.get("weight"), draft.get("dimensions"))
    return out["reason"] if out["suspect"] else None
