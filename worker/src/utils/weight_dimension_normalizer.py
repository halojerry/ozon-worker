"""重量/尺寸归一化公共模块（v0.37 A2/B2 修复）— 唯一单位/密度裁决点。

背景：prepare/pricing/validate/retry 四处各有一套重量尺寸启发式
（<10g×1000、密度÷1000、cm/mm 交叉判定），阈值互不相同 → 真实轻物
（3g）被误伤成 3000g → 物流费爆炸。v0.37 收敛到本模块单一实现。

核心原则（v0.21/v0.26/v0.34 已验证的保护法）：
- 数据**缺失**（weight<=0 / dim<=0）→ 允许兜底/估算
- 数据**已有**（非零真实值）→ 默认信任，密度/单位异常仅打标，绝不改写
- 唯一例外：明确单位级证据（重量为字符串带小数点判 kg）才转换

marks 语义（供调用方写入 state/payload/审计）：
- weight_source: draft(原始) / competitor(竞品) / estimated(兜底估算)
- weight_estimated: bool 重量非原始抓取值
- dimensions_suspected: bool 尺寸密度异常但保留原值
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

# Ozon 密度校验范围（kg/m³），来自 Ozon ML 经验值
DENSITY_MIN_KG_M3 = 1.293
DENSITY_MAX_KG_M3 = 13546

# 缺失兜底默认值（与历史一致，仅在缺失时生效）
DEFAULT_WEIGHT_G = 100
DEFAULT_DIMS_MM = (300, 200, 50)

# 轻物疑点阈值：<10g 且任一维 >50mm（历史启发式触发条件，v0.37 起仅标记）
LIGHT_WEIGHT_G = 10
LIGHT_DIM_MM = 50


def normalize_weight_dimensions(
    weight_raw: Any,
    dimensions_obj: Any,
    competitor: Dict[str, Any] | None = None,
) -> Tuple[int, Dict[str, int], Dict[str, Any]]:
    """归一化重量/尺寸，返回 (weight_g, dims_mm, marks)。

    只对缺失兜底，对已有值仅标记。marks 含：
      weight_source / weight_estimated / dimensions_suspected / reasons
    """
    marks: Dict[str, Any] = {
        "weight_source": "draft",
        "weight_estimated": False,
        "dimensions_suspected": False,
        "reasons": [],
    }
    dims = dimensions_obj if isinstance(dimensions_obj, dict) else {}
    length_raw = dims.get("length") or dims.get("depth") or 0
    width_raw = dims.get("width") or 0
    height_raw = dims.get("height") or 0
    competitor = competitor or {}

    # ── 重量：单位判定 + 缺失兜底 ──
    weight_g = _parse_weight_g(weight_raw, marks)

    # 缺失 → 竞品兜底 → 默认值
    if weight_g <= 0:
        comp_w = _safe_float(competitor.get("competitor_weight_g"))
        if comp_w > 0:
            weight_g = int(comp_w)
            marks["weight_source"] = "competitor"
            marks["weight_estimated"] = True
            marks["reasons"].append("weight_missing_used_competitor")
        else:
            weight_g = DEFAULT_WEIGHT_G
            marks["weight_source"] = "default"
            marks["weight_estimated"] = True
            marks["reasons"].append("weight_missing_used_default")

    # ⚠️ v0.37 A2 修复：轻物（<10g + 尺寸>50mm）仅标记，绝不 ×1000。
    # 旧启发式假设"kg 误写 g"，但真实轻物（3g 薄膜/5g 垫片）误伤率极高。
    l, w, h = _safe_float(length_raw), _safe_float(width_raw), _safe_float(height_raw)
    if 0 < weight_g < LIGHT_WEIGHT_G and max(l, w, h) > LIGHT_DIM_MM:
        marks["reasons"].append(
            f"light_weight_suspected({weight_g}g < {LIGHT_WEIGHT_G}g but dim>{LIGHT_DIM_MM}mm)"
        )

    # ── 尺寸：缺失兜底 + 密度标疑（不改写）──
    dims_mm: Dict[str, int] = {
        "length": int(l),
        "width": int(w),
        "height": int(h),
    }
    if all(v <= 0 for v in dims_mm.values()):
        comp_dims = competitor.get("competitor_dimensions_mm") or {}
        if isinstance(comp_dims, dict) and any(
            _safe_float(comp_dims.get(k)) > 0 for k in ("length", "width", "height")
        ):
            dims_mm = {
                "length": int(_safe_float(comp_dims.get("length"))),
                "width": int(_safe_float(comp_dims.get("width"))),
                "height": int(_safe_float(comp_dims.get("height"))),
            }
            marks["reasons"].append("dims_missing_used_competitor")
        else:
            dims_mm = {
                "length": DEFAULT_DIMS_MM[0],
                "width": DEFAULT_DIMS_MM[1],
                "height": DEFAULT_DIMS_MM[2],
            }
            marks["reasons"].append("dims_missing_used_default")
    else:
        # 逐维补缺失（不覆盖已有非零维）：竞品数据优先，其次默认值
        comp_dims = competitor.get("competitor_dimensions_mm") or {}
        if not isinstance(comp_dims, dict):
            comp_dims = {}
        for k, default in (("length", 100), ("width", 100), ("height", 50)):
            if dims_mm[k] <= 0:
                cv = _safe_float(comp_dims.get(k))
                if cv > 0:
                    dims_mm[k] = int(cv)
                    marks["reasons"].append(f"dim_{k}_missing_used_competitor")
                else:
                    dims_mm[k] = default
                    marks["reasons"].append(f"dim_{k}_missing_used_default")

    # 密度标疑（不改写）：Ozon 要求密度在 [1.293, 13546] kg/m³
    if weight_g > 0 and all(v > 0 for v in dims_mm.values()):
        volume_m3 = (dims_mm["length"] * dims_mm["width"] * dims_mm["height"]) / 1e9
        if volume_m3 > 0:
            density = (weight_g / 1000.0) / volume_m3
            if density > DENSITY_MAX_KG_M3:
                marks["dimensions_suspected"] = True
                marks["reasons"].append(
                    f"density_too_high({density:.1f} kg/m³>{DENSITY_MAX_KG_M3})"
                )
            elif 0 < density < DENSITY_MIN_KG_M3:
                marks["dimensions_suspected"] = True
                marks["reasons"].append(
                    f"density_too_low({density:.2f} kg/m³<{DENSITY_MIN_KG_M3})"
                )

    return weight_g, dims_mm, marks


def _parse_weight_g(weight_raw: Any, marks: Dict[str, Any]) -> int:
    """解析重量 → 克。字符串带小数点判 kg→g（明确单位级证据才转换）。"""
    if weight_raw is None or weight_raw == "":
        return 0
    try:
        if isinstance(weight_raw, str) and "." in str(weight_raw):
            weight_g = int(float(weight_raw) * 1000)  # kg → g
            marks["reasons"].append(f"weight_str_kg_parsed({weight_raw}kg→{weight_g}g)")
            return weight_g
        weight_g = int(float(weight_raw))
        return max(0, weight_g)
    except (TypeError, ValueError):
        return 0


def _safe_float(val: Any) -> float:
    try:
        return float(val) if val else 0.0
    except (TypeError, ValueError):
        return 0.0
