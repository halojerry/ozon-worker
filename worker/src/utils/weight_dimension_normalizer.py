"""重量/尺寸归一化公共模块（v0.37 A2/B2 修复）— 唯一单位/密度裁决点。

背景：prepare/pricing/validate/retry 四处各有一套重量尺寸启发式
（<10g×1000、密度÷1000、cm/mm 交叉判定），阈值互不相同 → 真实轻物
（3g）被误伤成 3000g → 物流费爆炸。v0.37 收敛到本模块单一实现。

核心原则（v0.21/v0.26/v0.34 已验证的保护法）：
- 数据**缺失**（weight<=0 / dim<=0）→ 允许兜底/估算
- 数据**已有**（非零真实值）→ 默认信任，密度/单位异常仅打标，绝不改写
- 唯一例外①：明确单位级证据（重量为字符串带小数点判 kg）才转换
- 唯一例外②（v0.69 契约硬边界）：Ozon 类目对长/宽/高的毫米级上下限
  （OZON_DIM_BOUNDS_MM，唯一事实源，validate 预检第二道同源 import）是平台
  物理事实——同 v0.68.1 OZON_MIN_WEIGHT_G 先例，越界值原样上传必被
  INCORRECT_DIMENSION 拒（A 系列 330×430×100 宽 430>400 实证）→ 取边界值
  clamp 并在 marks["dimensions_clamped"] 逐维留痕 from/to。密度/单位异常
  依旧只标疑不改写（轻物保护不变）。

marks 语义（供调用方写入 state/payload/审计）：
- weight_source: draft(原始) / competitor(竞品) / estimated(兜底估算)
- weight_estimated: bool 重量非原始抓取值
- dimensions_suspected: bool 尺寸密度异常但保留原值
- dimensions_clamped: dict 逐维 {dim: {from, to}}（无 clamp 时空 dict）
"""
from __future__ import annotations

import re
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

# ✅ v0.68.1: Ozon API 硬下限 10g（INCORRECT_DIMENSION "weight is out of range
# (min: 10, max: 5000)" 实证）——(0,10) 区间按契约必拒，视同缺失走兜底
OZON_MIN_WEIGHT_G = 10

# ✅ v0.69 Wave3: Ozon 契约尺寸硬边界（毫米，唯一事实源）——ozon_validate_node
# 预检第二道 import 同源对照，禁止两处各自维护。店铺健康扫描尺寸重量类错误 17 例
# 实证（330×430×100 宽 430>400 被 INCORRECT_DIMENSION 拒，retry 原样重跑耗尽）。
OZON_DIM_BOUNDS_MM = {"length": (42, 400), "width": (25, 400), "height": (5, 200)}


def normalize_weight_dimensions(
    weight_raw: Any,
    dimensions_obj: Any,
    competitor: Dict[str, Any] | None = None,
) -> Tuple[int, Dict[str, int], Dict[str, Any]]:
    """归一化重量/尺寸，返回 (weight_g, dims_mm, marks)。

    缺失兜底 + Ozon 契约硬边界 clamp（v0.69，例外②）；密度/单位异常仅标记。
    marks 含：weight_source / weight_estimated / dimensions_suspected /
    dimensions_clamped / reasons
    """
    marks: Dict[str, Any] = {
        "weight_source": "draft",
        "weight_estimated": False,
        "dimensions_suspected": False,
        "dimensions_clamped": {},
        "reasons": [],
    }
    dims = dimensions_obj if isinstance(dimensions_obj, dict) else {}
    length_raw = dims.get("length") or dims.get("depth") or 0
    width_raw = dims.get("width") or 0
    height_raw = dims.get("height") or 0
    competitor = competitor or {}

    # ── 重量：单位判定 + 缺失兜底 ──
    weight_g = _parse_weight_g(weight_raw, marks)

    # ✅ v0.68.1: 低于 Ozon 硬下限（(0,10)g）视同缺失——skill 抓取层无单位裸数字
    # （kg 语义按克产出 1g，A3 拒单实证）原样上传必被 INCORRECT_DIMENSION 拒。
    # 只兜底不改写成 ×1000（v0.37 轻物保护不变）。
    if 0 < weight_g < OZON_MIN_WEIGHT_G:
        marks["reasons"].append(
            f"weight_below_ozon_min({weight_g}g < {OZON_MIN_WEIGHT_G}g)_treated_as_missing"
        )
        weight_g = 0

    # 缺失 → 竞品兜底 → 默认值
    if weight_g <= 0:
        comp_w = _safe_float(competitor.get("competitor_weight_g"))
        if comp_w >= OZON_MIN_WEIGHT_G:
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

    # ✅ v0.69 Wave3: Ozon 契约硬边界 clamp（在缺失兜底/竞品回填之后，覆盖所有
    # 来源：真实值/竞品回填/默认值——默认 300/200/50 本就在界内，走同一路径只为
    # 防御未来阈值变动）。低于下限取下限、高于上限取上限，marks 逐维留痕 from/to。
    # 只动非零维：零维语义是「缺失」，已由上方兜底分支补齐，不参与 clamp。
    for _dim_key, (_lo, _hi) in OZON_DIM_BOUNDS_MM.items():
        _v = dims_mm[_dim_key]
        if _v <= 0:
            continue
        if _v < _lo:
            marks["dimensions_clamped"][_dim_key] = {"from": _v, "to": _lo}
            dims_mm[_dim_key] = _lo
        elif _v > _hi:
            marks["dimensions_clamped"][_dim_key] = {"from": _v, "to": _hi}
            dims_mm[_dim_key] = _hi
    for _dim_key, _ch in marks["dimensions_clamped"].items():
        marks["reasons"].append(f"dim_{_dim_key}_clamped({_ch['from']}→{_ch['to']})")

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


# ── v0.81 上架质量止血：箱级毛重 reconcile（唯一入口）────────────────────────
# 根因（实锤）：skill 信封 draft.weight 常是 1688 包装表第一行 = 箱级毛重
# （30支香 final_weight_g=962g 而卡属性「商品重量=50g」；吸顶灯 9150g；风扇 3000g）
# → worker pricing/prepare 原样信任 → 运费/卡价虚高数倍。本函数从 1688 属性
# （draft.attributes，键为中文属性名）提取单件重候选，仅在「信封重 ≥ 3×候选 且
# 候选 ≥ Ozon 硬下限」时采信候选——证据不足原样返回零 marks（宁缺毋滥，
# 真实重货如 962g 整箱香薰蜡烛不被误杀需 3× 门槛）。
# 契约：不得 raise；输入畸形（None/非 dict/乱码值/非数值 weight_g）一律原样返回。

# reconcile 触发比值：信封重 ≥ 3×单件候选 才判「箱级毛重混入」
RECONCILE_LOT_RATIO = 3.0

# 单件重候选键优先序：单件语义在前，箱/包级语义殿后（箱级键与信封重同源，
# 会被比值闸自然挡住，仅当它是唯一证据时才可能命中）
_WEIGHT_CANDIDATE_KEYS: Tuple[str, ...] = (
    "净重", "单件重量", "商品重量", "weight", "重量", "含包装重量",
)

_WEIGHT_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 单位换算表（长单位优先匹配，防止「毫克」被「克」截胡）
_WEIGHT_UNIT_FACTORS: Dict[str, float] = {
    "千克": 1000.0, "公斤": 1000.0, "毫克": 0.001,
    "kg": 1000.0, "кг": 1000.0, "мг": 0.001,
    "克": 1.0, "г": 1.0, "g": 1.0,
    "斤": 500.0,
    "吨": 1_000_000.0, "т": 1_000_000.0, "t": 1_000_000.0,
    "盎司": 28.3495, "oz": 28.3495,
    "磅": 453.592, "lbs": 453.592, "lb": 453.592,
}
_WEIGHT_UNIT_ORDER: Tuple[str, ...] = tuple(
    sorted(_WEIGHT_UNIT_FACTORS, key=len, reverse=True)
)


def _fmt_g(v: float) -> str:
    """重量留痕格式：整数不带小数点（962 而非 962.0），非整数保留 1 位。"""
    fv = float(v)
    return str(int(fv)) if fv.is_integer() else f"{fv:.1f}"


def _parse_attr_weight_g(raw: Any) -> float:
    """解析 1688 属性重量值 → 克。「50克」「0.05kg」「50」「0.05」均可；
    无单位时带小数点判 kg（与 _parse_weight_g 同纪律）、整数判 g。解析失败返 0。"""
    try:
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        if raw is None or isinstance(raw, bool):
            return 0.0
        if isinstance(raw, (int, float)):
            v = float(raw)
            return v if v > 0 else 0.0  # 裸数字 = 克
        s = str(raw).strip()
        if not s:
            return 0.0
        m = _WEIGHT_NUM_RE.search(s)
        if not m:
            return 0.0
        num = float(m.group())
        after = s[m.end():].lstrip().lower()
        for unit in _WEIGHT_UNIT_ORDER:
            if after.startswith(unit.lower()):
                return num * _WEIGHT_UNIT_FACTORS[unit]
        # 无单位：小数点 = kg 证据（skill 抓取层 A3 先例），整数 = 克
        if "." in m.group():
            return num * 1000.0
        return num
    except Exception:
        return 0.0


def _extract_single_item_weight_g(draft_attrs: Any) -> float:
    """从 draft.attributes 提取单件重候选（克）。按候选键优先序 + 其余含
    「重量/weight」键兜底扫描，首个可解析正值返回；无候选返回 0。"""
    if not isinstance(draft_attrs, dict):
        return 0.0
    ordered: list = []
    lower_index: Dict[str, Any] = {}
    for k in draft_attrs:
        try:
            lower_index[str(k).strip().casefold()] = k
        except Exception:
            continue
    for pk in _WEIGHT_CANDIDATE_KEYS:
        k = lower_index.get(pk.casefold())
        if k is not None and k not in ordered:
            ordered.append(k)
    for k in draft_attrs:
        if k in ordered:
            continue
        ks = str(k).strip().casefold()
        if "重量" in ks or "weight" in ks:
            ordered.append(k)
    for k in ordered:
        v = _parse_attr_weight_g(draft_attrs.get(k))
        if v > 0:
            return v
    return 0.0


def reconcile_weight_with_attrs(weight_g: Any, draft_attrs: Any) -> Tuple[Any, list]:
    """箱级毛重 reconcile（v0.81 止血唯一入口）：信封重 vs 1688 单件重属性交叉裁决。

    判定：候选 ≥ 10g（OZON_MIN_WEIGHT_G）且 weight_g ≥ 3×候选 → 采信候选，
    marks 追加 ``weight_lot_suspected_reconciled:{原值}->{候选}``；
    无候选 / 不满足比值 → 原样返回零 marks。

    契约：纯函数不 raise——None/非 dict/乱码值/非数值 weight_g 一律 (原值, [])。
    调用方（pricing_node / prepare._resolve_weight_dimensions）必须在
    normalize_weight_dimensions 之后、ensure_volume_weight_floor 之前接线。
    """
    try:
        if not isinstance(draft_attrs, dict) or not draft_attrs:
            return weight_g, []
        try:
            w_val = float(weight_g)
        except (TypeError, ValueError):
            return weight_g, []
        if w_val <= 0:
            return weight_g, []
        candidate = _extract_single_item_weight_g(draft_attrs)
        if candidate < OZON_MIN_WEIGHT_G:
            return weight_g, []
        if w_val < RECONCILE_LOT_RATIO * candidate:
            return weight_g, []
        marks = [
            f"weight_lot_suspected_reconciled:{_fmt_g(w_val)}->{_fmt_g(candidate)}"
        ]
        return candidate, marks
    except Exception:
        return weight_g, []
