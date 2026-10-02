"""v0.83.2: declined / validation-fail 卡处置决策（纯函数，唯一事实源）。

背景（2026-10-02 生产 4718259 店实锤，67 张问题卡）：
- v0.82 card_audit 域拍板 B 不变量「declined 只报告」（理由：修复=归档+重上，
  破坏性，人工确认）。半年人工未至：declined 卡零处置累积（41→67 张实证），
  店铺后台被死卡淹没 = 用户新困惑源。
- 拍板升级（fix/card-audit-declined-v1，用户 2026-10-02 批准）：declined 卡
  **分级终态处置**——可修拒因走既有唯一构造器自动修（content_enrich 家族，
  A6 防洗卡全量回显），救不活的自动归档（可逆、留痕、kill-switch 可关）。
  declined 卡本就不可售：留着只产生噪音，归档不销毁数据（unarchive 可回）。

分级规则（declined_disposition，纯函数）：
- 标题残壳（无 ≥4 字西里尔词，utils.title_sanitizer.has_cyrillic_word 同源）
  → archive（连标题都残的卡，重传无从谈起）。
- 拒因含 HOPELESS 族（资质/类目合法性/媒体级——重传也过不了）→ archive。
- 拒因 ⊆ FIXABLE 族且标题健康 → auto_repair。
- 无错误码（人工审核拒绝无机器码）/ 可修与未知混码 → report（保守人工）。

红线：
- 本模块只做「决策 + 回显补丁」；写出口仍是 card_audit → content_enrich
  家族构造器（全量回显）与 /v1/product/archive，不在别处新增写路径。
- patch_echo_for_declines 只修「Ozon 点名的问题」（错误码带 attribute_id 的
  定向清洗 + 重量密度/维度兜底 + 空值剔除），绝不改写卡面其余内容——
  与 A6 防洗卡同一纪律。
- 码表变更（新拒因码归档/可修）只改本模块常量，决策函数零散落。
"""
from __future__ import annotations

import copy
from typing import Any, Optional

# ── 拒因码分级（2026-10-02 4718259 店 67 卡实盘直方图校准）──

# 可修族：修复手段全部已存在——数值清洗（attr_numeric_sanitize，RU 逗号/取整/
# bounds 夹取）、重量密度兜底（volume_weight_guard）、维度 clamp
#（weight_dimension_normalizer.OZON_DIM_BOUNDS_MM）、4191/描述重建
#（content_enrich 构造器 allow_annotation_replace）。
FIXABLE_DECLINE_CODES: frozenset[str] = frozenset({
    "VALUE_MUST_BE_DECIMAL",
    "VALUE_MUST_BE_INTEGER",
    "VALUE_MIN_LIMIT",
    "VALUE_MAX_LIMIT",
    "warning_attribute_values_out_of_range",
    "ML_INCORRECT_VOLUME_WEIGHT",
    "INCORRECT_DIMENSION",
    "DESCRIPTION_DECLINE",          # 4191/描述族——v0.83 撰写链重建覆盖
    "error_attribute_values_empty",
})

# 不可修族：资质/品类合法性/媒体级问题——载荷修不出来，重传必再拒。
HOPELESS_DECLINE_CODES: frozenset[str] = frozenset({
    "BR_ASSORTMENT",
    "BR_hazard_class1",
    "image_not_upload",
})

# 噪音码：Ozon 平台侧擦值（9782 erased 族）——不是拒因，判分级时剔除。
IGNORED_DECLINE_CODES: frozenset[str] = frozenset({"erased_attribute_value"})

# 数值族 → sanitize 的 attr_type 映射（错误码本身声明了期望类型）
_NUMERIC_TYPE_BY_CODE = {
    "VALUE_MUST_BE_INTEGER": "Integer",
    "VALUE_MUST_BE_DECIMAL": "Decimal",
    "VALUE_MUST_BE_INTEGER_LIMIT": "Integer",
}


def decline_error_codes(info: dict) -> list[str]:
    """卡 info（/v3/product/info/list item）→ 去噪后的拒因码列表。"""
    errs = info.get("errors") if isinstance(info, dict) else None
    if not isinstance(errs, list):
        return []
    out: list[str] = []
    for e in errs:
        if not isinstance(e, dict):
            continue
        code = str(e.get("code") or "").strip()
        if code and code not in IGNORED_DECLINE_CODES and code not in out:
            out.append(code)
    return out


def declined_disposition(info: dict) -> dict:
    """(纯函数) declined/validation-fail 卡 → 处置决策。

    返回 {"action", "codes", "title_healthy", "reason"}：
    - action ∈ {"auto_repair", "archive", "report"}
    - title_healthy：标题含 ≥4 字西里尔词（残壳标题 False）
    - reason：决策依据（进 finding detail，人工可溯）
    """
    codes = decline_error_codes(info)
    title = str(info.get("name") or "")
    from utils.title_sanitizer import has_cyrillic_word

    title_healthy = has_cyrillic_word(title)
    hopeless = [c for c in codes if c in HOPELESS_DECLINE_CODES]
    unknown = [c for c in codes if c not in FIXABLE_DECLINE_CODES
               and c not in HOPELESS_DECLINE_CODES]

    if not title_healthy and title != "":
        return {"action": "archive", "codes": codes, "title_healthy": False,
                "reason": "title_shell"}
    if hopeless:
        return {"action": "archive", "codes": codes, "title_healthy": title_healthy,
                "reason": f"hopeless_codes:{','.join(sorted(hopeless))}"}
    if not codes:
        return {"action": "report", "codes": [], "title_healthy": title_healthy,
                "reason": "no_error_codes"}
    if unknown:
        return {"action": "report", "codes": codes, "title_healthy": title_healthy,
                "reason": f"unknown_codes:{','.join(sorted(unknown))}"}
    return {"action": "auto_repair", "codes": codes, "title_healthy": True,
            "reason": "fixable_codes"}


def _to_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def patch_echo_for_declines(stored_item: Any, info: dict
                            ) -> tuple[Optional[dict], list[str]]:
    """按拒因码定向修补 v4 回显（deep copy），返回 (patched, changes)。

    只动「Ozon 点名的问题」，绝不改写其余卡面内容：
    - 数值族：按错误 attribute_id 定向过 sanitize_numeric_attr_value
      （RU 逗号小数/取整/bounds 夹取；剥完无数字 → 剔除该值）
    - error_attribute_values_empty：剔除空串值
    - ML_INCORRECT_VOLUME_WEIGHT：ensure_volume_weight_floor 密度兜底（只上调）
    - INCORRECT_DIMENSION：三维 clamp 到 OZON_DIM_BOUNDS_MM
    - DESCRIPTION_DECLINE：不在本函数修——由构造器 allow_annotation_replace
      重建 4191（调用方传开关），此码只透传。
    """
    if not isinstance(stored_item, dict):
        return None, []
    patched = copy.deepcopy(stored_item)
    changes: list[str] = []
    code_map: dict[str, dict] = {}
    for e in (info.get("errors") or []):
        if isinstance(e, dict) and e.get("code"):
            code_map[str(e["code"])] = e

    attrs = [a for a in (patched.get("attributes") or []) if isinstance(a, dict)]
    attr_index = {_to_int(a.get("id")): a for a in attrs}

    # ① 数值族定向清洗
    from utils.attr_numeric_sanitize import sanitize_numeric_attr_value

    for code, err in code_map.items():
        if code not in ("VALUE_MUST_BE_DECIMAL", "VALUE_MUST_BE_INTEGER",
                        "VALUE_MIN_LIMIT", "VALUE_MAX_LIMIT",
                        "warning_attribute_values_out_of_range"):
            continue
        aid = _to_int(err.get("attribute_id"))
        attr = attr_index.get(aid)
        if not aid or not attr:
            continue
        attr_type = _NUMERIC_TYPE_BY_CODE.get(code, "Decimal")
        new_values: list[Any] = []
        changed = False
        for v in (attr.get("values") or []):
            raw = v.get("value") if isinstance(v, dict) else v
            cleaned, _reason = sanitize_numeric_attr_value(aid, raw, attr_type)
            if cleaned is None:
                changed = True  # 剥完无数字 → 剔除该值
                continue
            if str(cleaned) != str(raw if raw is not None else ""):
                changed = True
            new_values.append({"value": cleaned} if isinstance(v, dict) else cleaned)
        if changed:
            attr["values"] = new_values
            changes.append(f"numeric:{aid}")

    # ② 空值剔除
    if "error_attribute_values_empty" in code_map:
        for attr in attrs:
            vals = attr.get("values") or []
            kept = [v for v in vals
                    if (v.get("value") if isinstance(v, dict) else v) not in ("", None)]
            if kept != vals:
                attr["values"] = kept
                changes.append(f"strip_empty:{attr.get('id')}")

    # ③ 重量密度兜底（只上调，cap ×3，永不下调——guard 契约）
    if "ML_INCORRECT_VOLUME_WEIGHT" in code_map:
        from utils.volume_weight_guard import ensure_volume_weight_floor

        dims = {"length": _to_int(patched.get("depth")),
                "width": _to_int(patched.get("width")),
                "height": _to_int(patched.get("height"))}
        weight, adjusted = ensure_volume_weight_floor(
            _to_int(patched.get("weight")), dims)
        if adjusted:
            patched["weight"] = weight
            changes.append(f"weight_floor:{weight}")

    # ④ 维度 clamp（Ozon 契约硬边界）
    if "INCORRECT_DIMENSION" in code_map:
        from utils.weight_dimension_normalizer import OZON_DIM_BOUNDS_MM

        for key, field in (("length", "depth"), ("width", "width"), ("height", "height")):
            lo, hi = OZON_DIM_BOUNDS_MM.get(key, (0, 0))
            v = _to_int(patched.get(field))
            if v > 0 and (v < lo or v > hi):
                patched[field] = min(max(v, lo), hi)
                changes.append(f"dim_clamp:{field}({v}→{patched[field]})")

    return patched, changes
