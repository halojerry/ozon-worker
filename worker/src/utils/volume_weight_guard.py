"""体积-重量密度兜底（v0.73 Issue4）— weight = max(weight, 0.40 g/cm³ × 体积) 唯一入口。

 Issue4 根因：迷你相机 56g / 83×62×29mm（密度 0.375 g/cm³）被 Ozon ML 以
 ML_INCORRECT_VOLUME_WEIGHT 拒单，且原值重发再拒（无自愈）。

 生产数据校准结论（已定案，本地 listing 留存 199 行 weight+dims 统计，2026-09-09）：
 - 大量行密度紧贴 0.398-0.402 g/cm³ —— 源自 skill 侧 cloud_probe
   `_validate_and_fix_product_data` 的**尺寸估算分支**（尺寸全缺失时按
   400 kg/m³ 反推 2:1.5:1 长方体，rounding 产生 ±0.002 漂移），该簇全部 approved；
 - 出事相机 0.375 恰在该簇之下被拒；
 - 同批 approved 里存在密度低至 0.015 g/cm³ 的大件轻抛货（占 32%）
   → **绝对密度硬拒会误杀**。

 因此本 guard 只做**兜底提升，永不拒绝、永不下调**：
   目标 = max(ceil(体积cm³ × MIN_DENSITY_G_CC), OZON_MIN_WEIGHT_G(10g 契约硬下限))
   weight < 目标 → 新重量 = min(目标, max(weight×3, 10))   # cap 原值×3 防极端体积
 否则原样返回。cap×3 语义说明：密度 <0.133 g/cm³ 的轻抛货即使被 ML 拒也最多抬到
 原值 3 倍（不到 floor），防大件体积把重量抬到离谱；10g 下限提升不受 cap 限制
 （契约最低重量不属于「极端抬升」）。

 为何既有机制放过了 56g 真实值（改本模块前先读）：
 - skill 侧 0.40 填充（cloud_probe）只作用于「尺寸全缺失」——方向是已知重量→估
   尺寸；相机 1688 页面有真实尺寸，不走该分支；
 - worker normalizer（weight_dimension_normalizer）核心原则「真实值只标疑绝不改写」，
   且其标疑下限 DENSITY_MIN_KG_M3=1.293 kg/m³（=0.0013 g/cm³）远低于 0.375，
   连标疑都不触发；
 - retry 体积重反推闸 MIN_PHYSICAL_DENSITY_KG_M3=50 kg/m³（=0.05 g/cm³）也够不着。

 选型说明：既有 0.40 语义在 skill 侧（重量→估尺寸），与新 guard（尺寸→兜底重量）
 方向互补、不重复，且跨仓库边界（skill 是编译交付的客户本地组件）——故 worker 侧
 独立本模块为唯一入口，skill 侧保持不动。

 接线点（仅两处，禁止内联复制公式）：
 1. prepare_ozon_upload_node._resolve_weight_dimensions —— normalizer/尺寸 clamp
    之后对最终 payload 重量兜底（marks["weight_adjusted_for_volume"] 留痕）；
 2. validation_retry_loop.repair_dimensions_node —— ML_INCORRECT_VOLUME_WEIGHT
    拒后按 payload 当前 weight/dims 反推重发（repair_marks["weight_floor_applied"]）。
"""
from __future__ import annotations

import math
from typing import Any, Optional, Tuple

# ✅ v0.73: 与 weight_dimension_normalizer 同源（OZON_MIN_WEIGHT_G 注释见彼处）
from utils.weight_dimension_normalizer import OZON_MIN_WEIGHT_G

# 生产 199 行留存校准：0.398-0.402 簇（skill 400 kg/m³ 估算尺寸产物）全通过、
# 0.375 被拒。禁止调用方各自改这个数——改阈值必须同步复查两处接线测试。
MIN_DENSITY_G_CC = 0.40


def compute_density_g_cc(weight_g: Any, dims_mm: Any) -> Optional[float]:
    """当前密度（g/cm³）；weight<=0 或 dims 缺失/非法返回 None（marks 留痕用）。"""
    try:
        w = float(weight_g)
    except (TypeError, ValueError):
        return None
    if w <= 0:
        return None
    vol = _volume_cm3(dims_mm)
    if vol is None or vol <= 0:
        return None
    return round(w / vol, 3)


def ensure_volume_weight_floor(weight_g: Any, dims_mm: Any) -> Tuple[int, bool]:
    """按最小密度 0.40 g/cm³ 兜底提升重量（只上调、cap 原值×3、永不拒）。

    返回 (最终重量 int, 是否调整 bool)。规则：
    - volume_cm3 = l×w×h / 1000（mm → cm³）；floor = ceil(volume_cm3 × MIN_DENSITY_G_CC)
    - 目标 = max(floor, OZON_MIN_WEIGHT_G)（10g 为 Ozon 契约硬下限）
    - weight_g < 目标 → (min(目标, max(weight_g×3, OZON_MIN_WEIGHT_G)), True)
    - 否则 (weight_g, False)；永不下调
    - weight_g<=0 / dims 缺失或非法 / 任一维<=0 → 原样返回（不猜）
    """
    try:
        w = int(float(weight_g))
    except (TypeError, ValueError):
        return weight_g, False  # type: ignore[return-value]
    if w <= 0:
        return weight_g, False  # type: ignore[return-value]

    vol = _volume_cm3(dims_mm)
    if vol is None:
        return w, False

    floor = math.ceil(vol * MIN_DENSITY_G_CC)
    target = max(floor, OZON_MIN_WEIGHT_G)
    if w >= target:
        return w, False
    raised = min(target, max(w * 3, OZON_MIN_WEIGHT_G))
    return int(raised), True


def _volume_cm3(dims_mm: Any) -> Optional[float]:
    """mm 三维 → cm³；dims 非法（非 dict / 缺维 / 任一维<=0）返回 None。"""
    dims = dims_mm if isinstance(dims_mm, dict) else {}
    try:
        ln = float(dims.get("length") or 0)
        w = float(dims.get("width") or 0)
        h = float(dims.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if ln <= 0 or w <= 0 or h <= 0:
        return None
    return ln * w * h / 1000.0
