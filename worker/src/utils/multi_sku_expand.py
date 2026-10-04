"""多 SKU 合卡展开纯函数层（feat/multi-sku-worker-v1，PLAN-multi-sku-v1 §V2 worker 腿）。

背景：3D 耗材等「颜色即 SKU」品类需要「每色一 SKU 合一张卡」（Ozon 站内竞品
形态：同 dc/tp + **同 9048** + 多 items[]，每 item 一个 offer_id + 颜色差异）。
此前管线恒单 SKU 单卡（F 线 8 单实证）：9048 是**防**并卡设计（确定性派生），
与合卡机制相反路；variant_primary_loop 有链路无数据。

契约面（skill V1 采集腿写入，本模块只消费）::

    draft.multi_sku = True
    draft.variants = [
        {"sku_id": "1083073125898_1", "color": "белый", "color_dict_id": 61571,
         "price_delta_cny": 0, "ref_image": "<该色 1688 图 URL>"}, …]   # ≤15

不变量（AGENTS「不变量速查表」对应条目，测试锁定）：
- **9048 全 items 同值**（合卡键）——值用主 item_id（跟卖 UPDATE 同值先例），
  绝不是防并卡 ``f"{item_id}~{hash}"`` 派生；本模块对展开产物强制覆写兜底。
- **颜色匹配唯一入口 attr_value_matcher**（match_dict_value + unique_or_none）：
  精确 > 包含唯一；多候选/无命中 → **variant 剔除并如实 log，绝不盲补首值、
  绝不用 FALLBACK_COLORS 编造**。变体 ``color_dict_id``（skill 采集腿权威）
  在场时最高优先（需在字典缓存中验存在，防幻觉 id）。
- **价格只做加减不重算公式**（compute_price 唯一入口语义）：per-variant 价
  = 主定价 ± price_delta_cny（CNY→店币经 fx_rate 换算），old_price 过
  ``enforce_old_price_rule`` 唯一规则。
- **值数闸天然合规**：每 item 属性恒 1 值（cap_attribute_values 出口闸照过）。
- **缺省零变化**：``draft.multi_sku`` 缺失/variants 空 → 本模块不被调用
  （prepare 既有单 SKU 路径逐字不变，防并卡 9048 派生照旧）。
- **9048-only 降级合卡**（feat/multi-sku-9048-only）：类目 schema 确无颜色
  字典属性且颜色字典展开全剔除（color_* 族）→ 不再整批回退单 SKU，改走
  ``expand_items_9048_only``（竞品 4929923490 实测形态：同 9048 多 items、
  无颜色属性、标题色尾缀区分变体）；无法俄语化的色名剔除（宁缺毋滥）、
  保留 <2 变体放弃降级。触发判定 ``should_degrade_to_9048_only``（schema
  判据 + 全 color_* 剔除双条件，瞬时字典缓存缺失不误降级）。

分层纪律（对齐 attr_value_matcher）：本文件 = 纯函数层，零网络/零 LLM/零 IO，
全部可离线单测。不 import graphs/api（W3a 依赖方向）。
"""
from __future__ import annotations

import copy
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from utils.attr_value_matcher import (  # type: ignore
    clean_dict_value,
    match_dict_value,
    normalize_text,
    unique_or_none,
)
from utils.attr_defaults import COLOR_ZH_TO_RU  # type: ignore
from utils.pricing_estimate import enforce_old_price_rule  # type: ignore

logger = logging.getLogger(__name__)

# Ozon 卡变体上限实测 20+（竞品 PLA 卡同卡 20+ 色），保守 15（PLAN 风险表：
# 超限取前 15 并如实打印剔除清单）。
MAX_VARIANTS = 15

# 颜色属性 id 集合（不同类目用不同 id；prepare 既有 COLOR_ATTR_IDS 同源口径）
COLOR_ATTR_IDS = (10096, 10097, 10098, 10099)

# 9048-only 降级合卡（feat/multi-sku-9048-only，双 gate 铁证）最小变体数：
# 3D 耗材类目（17028935/*、200001721/*）schema 无 Цвет 颜色字典属性 → 颜色字典
# 路径 7/7 color_no_match 全剔除回退单 SKU；而竞品 4929923490 多变体形态实测
# 是 9048 合并（同 9048 多 items，非颜色字典维度）。降级至少保 2 变体才值得
# 合卡，否则回退单 SKU 现状（宁缺毋滥）。
MIN_MERGE_VARIANTS_9048_ONLY = 2

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


# ────────────────────────── 颜色属性定位 ──────────────────────────


def resolve_color_attr_id(
    base_attributes: List[Dict[str, Any]],
    attributes_schema: List[Dict[str, Any]],
) -> int:
    """定位颜色属性 id。

    信任序：base item 既有属性命中已知颜色 id（主链已填色 = 最直接证据）
    > schema 属性名含 цвет/颜色 > 缺省 10096（与 prepare 既有默认一致）。
    """
    for attr in base_attributes or []:
        if not isinstance(attr, dict):
            continue
        try:
            aid = int(attr.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if aid in COLOR_ATTR_IDS:
            return aid
    for schema_attr in attributes_schema or []:
        if not isinstance(schema_attr, dict):
            continue
        name = str(schema_attr.get("name") or "").lower()
        if "цвет" in name or "颜色" in name:
            try:
                aid = int(schema_attr.get("id") or 0)
                if aid > 0:
                    return aid
            except (TypeError, ValueError):
                continue
    return 10096


def schema_has_color_dict_attr(attributes_schema: List[Dict[str, Any]]) -> bool:
    """类目 schema 是否**确有**颜色字典属性（9048-only 降级触发判据之一）。

    判据与 resolve_color_attr_id 同一命名启发（属性名含 цвет/颜色），
    另要求是字典属性（dictionary_id>0；键缺失无法证伪 → 保守视为有，
    误降级=上无颜色区分的 items 比「不降级维持单 SKU」风险高）。
    3D 耗材类目（17028935/*）schema 无此类属性 → False（双 gate 实证）。
    """
    for schema_attr in attributes_schema or []:
        if not isinstance(schema_attr, dict):
            continue
        name = str(schema_attr.get("name") or "").lower()
        if "цвет" not in name and "颜色" not in name:
            continue
        if "dictionary_id" not in schema_attr:
            return True
        try:
            if int(schema_attr.get("dictionary_id") or 0) > 0:
                return True
        except (TypeError, ValueError):
            return True
    return False


# ────────────────────────── 颜色匹配（attr_value_matcher 唯一入口） ──────────────────────────


def _zh_to_ru_bridge(color: str) -> str:
    """中文色名 → 俄语色名（attr_defaults.COLOR_ZH_TO_RU 复合色优先，非编造——
    只做语言桥接，命中与否仍以字典缓存匹配为准）。"""
    c = str(color or "").strip()
    for zh, ru in COLOR_ZH_TO_RU:
        if zh in c:
            return ru
    return ""


def _variant_color_text(variant: Dict[str, Any]) -> str:
    """variant 颜色文本（attributes["颜色"] 权威 > color 字段，match/降级共用）。"""
    attrs = variant.get("attributes") if isinstance(variant.get("attributes"), dict) else {}
    return str(attrs.get("颜色", variant.get("color", "")) or "").strip()


def derive_ru_color_suffix(color: str) -> str:
    """9048-only 降级模式的变体标题色尾缀（该类目无颜色属性，标题尾缀是
    唯一变体区分手段）。语言桥 = attr_value_matcher 同源 COLOR_ZH_TO_RU。

    宁缺毋滥：西里尔原文直接用（skill 采集腿可产 RU 色名）；中文过 ZH→RU
    桥；桥不中（生僻色）/拉丁/其他文字 → ""（调用方必须剔除该 variant，
    绝不保留中文尾缀、绝不音译编造俄语色名）。"""
    c = str(color or "").strip()
    if not c:
        return ""
    if _CYRILLIC_RE.search(c):
        return c
    return _zh_to_ru_bridge(c)


def _exact_hits(color: str, candidates: List[dict]) -> List[dict]:
    c = normalize_text(color)
    return [v for v in candidates if normalize_text(str(v.get("value") or "")) == c]


def match_variant_color(
    variant: Dict[str, Any],
    color_attr_id: int,
    dictionary_values: Dict[str, List[Dict[str, Any]]],
) -> Tuple[int, str, str]:
    """解析 variant 颜色 → (dictionary_value_id, 展示文本, 匹配层)。

    匹配层（信任序，逐级降级、全程可审计）：
    - ``skill_dict_id``：variant.color_dict_id 在场且能在字典缓存中验到（防幻觉 id）
    - ``exact``：attr_value_matcher.match_dict_value 精确命中（唯一）
    - ``bridge_exact``：中文色名经 ZH→RU 语言桥接后精确命中
    - ``unique``：包含匹配唯一存活（match_dict_value + unique_or_none）

    返回 dict_id=0 表示无匹配（reason 经第三位传达：missing/no_match/ambiguous），
    调用方必须剔除该 variant——绝不盲补首值、绝不 FALLBACK_COLORS 编造。
    """
    cached = dictionary_values.get(str(color_attr_id)) or []
    color_cn = _variant_color_text(variant)
    color_dict_id = int(variant.get("color_dict_id") or 0)

    # 0) skill 权威 dict_id（验存在，防幻觉；缓存空时无法验证 → 不采信）
    if color_dict_id > 0 and cached:
        for cv in cached:
            try:
                if int(cv.get("id") or 0) == color_dict_id:
                    return color_dict_id, clean_dict_value(color_dict_id, str(cv.get("value") or "")) or _zh_to_ru_bridge(color_cn), "skill_dict_id"
            except (TypeError, ValueError):
                continue

    if not color_cn:
        return 0, "", "missing"

    # 1) attr_value_matcher 唯一入口：精确 → 包含
    if color_cn:
        candidates = match_dict_value(color_attr_id, color_cn, cached)
        exact = _exact_hits(color_cn, candidates) if candidates else []
        pool = exact or candidates
        res = unique_or_none(color_attr_id, "Цвет", pool)
        if res.status == "matched":
            return res.dictionary_value_id, res.value or _zh_to_ru_bridge(color_cn), ("exact" if exact else "unique")
        if res.status == "llm_eligible":
            # 多候选不盲采（v0.13「套娃」纪律）→ variant 剔除
            return 0, "", "ambiguous"

        # 2) 中文色名 ZH→RU 桥接后再走同一匹配入口（字典缓存可能是 RU 形态）
        ru = _zh_to_ru_bridge(color_cn)
        if ru:
            candidates = match_dict_value(color_attr_id, ru, cached)
            exact = _exact_hits(ru, candidates) if candidates else []
            pool = exact or candidates
            res = unique_or_none(color_attr_id, "Цвет", pool)
            if res.status == "matched":
                return res.dictionary_value_id, res.value or ru, ("bridge_exact" if exact else "bridge_unique")

    return 0, "", "no_match"


# ────────────────────────── 价格（主定价 ± delta，不重算公式） ──────────────────────────


def variant_list_prices(
    main_price: Any,
    main_old_price: Any,
    price_delta_cny: Any,
    fx_rate: float,
) -> Tuple[str, str]:
    """per-variant (price, old_price) 字符串。

    price = 主定价 + delta（CNY→店币经 fx_rate；CNY 店 fx_rate=1.0）——只加减，
    绝不重跑 compute_price 公式（唯一入口纪律）。old_price 同步平移后过
    ``enforce_old_price_rule`` 唯一规则（≥price×1.2 且差价 <400 时 ≥20）。
    主定价无效（≤0/畸形）→ ("", "")，调用方维持 base item 原价语义。
    """
    try:
        p_main = int(float(main_price))
    except (TypeError, ValueError):
        return "", ""
    if p_main <= 0:
        return "", ""
    try:
        o_main = int(float(main_old_price))
    except (TypeError, ValueError):
        o_main = 0
    try:
        delta = float(price_delta_cny or 0)
        if not math.isfinite(delta):
            delta = 0.0
    except (TypeError, ValueError):
        delta = 0.0
    rate = float(fx_rate) if fx_rate and float(fx_rate) > 0 else 1.0
    delta_conv = delta * rate
    p_v = max(1, round(p_main + delta_conv))
    o_v = enforce_old_price_rule(p_v, (o_main + delta_conv) if o_main > 0 else None)
    if not o_v:
        o_v = enforce_old_price_rule(p_v) or str(p_v)
    return str(p_v), str(o_v)


# ────────────────────────── 展开 ──────────────────────────


@dataclass
class MultiSkuExpansion:
    """展开产物：items + 剔除清单 + 审计 marks。

    items 为空 = 全部 variant 被剔除（颜色全无匹配等）→ 调用方必须回退
    单 SKU 路径并如实 log，绝不上传空 items。
    """

    items: List[Dict[str, Any]] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    marks: Dict[str, Any] = field(default_factory=dict)


def _variant_offer_id(variant: Dict[str, Any], idx: int, base_offer_id: str) -> str:
    """variant offer_id：sku_id 权威；缺失回落确定性 f"{base}_{idx}"（重试幂等）。"""
    sku = str(variant.get("sku_id") or "").strip()
    if sku:
        return sku
    return f"{base_offer_id}_{idx}" if base_offer_id else f"msku_{idx}"


def expand_multi_sku_items(
    base_item: Dict[str, Any],
    variants: List[Dict[str, Any]],
    *,
    color_attr_id: int,
    dictionary_values: Dict[str, List[Dict[str, Any]]],
    shared_images: List[str],
    variant_primary_images: Optional[List[str]] = None,
    main_price: Any = 0,
    main_old_price: Any = 0,
    fx_rate: float = 1.0,
    merge_9048: str = "",
    base_offer_id: str = "",
    max_variants: int = MAX_VARIANTS,
) -> MultiSkuExpansion:
    """把单 item 载荷展开为多 SKU 变体 items（纯函数）。

    每 variant 一个 item：offer_id=variant sku_id、9048 全 items 同值
    （merge_9048 强制覆写兜底）、颜色属性填该 variant 字典值、
    price=主定价±delta、其余属性/营销图共享 base item 值；每 variant 主图
    取 variant_primary_images[i]（该色生图，缺/败回落 base 主图）。

    剔除（dropped，如实报告不静默）：颜色缺失/无匹配/多候选歧义/重复字典值
    （Ozon 同卡变体颜色必须唯一）/offer 重复/超上限（截断保前 N）。
    """
    expansion = MultiSkuExpansion()
    variants = [v for v in (variants or []) if isinstance(v, dict)]
    base_primary = str((base_item or {}).get("primary_image") or "").strip()

    # 超上限截断（保前 N，剔除清单如实记录）
    for j, v in enumerate(variants[max_variants:], start=max_variants):
        expansion.dropped.append({
            "index": j, "sku_id": str(v.get("sku_id") or ""),
            "color": str(v.get("color") or ""), "reason": "limit_exceeded",
        })
    variants = variants[:max_variants]

    used_color_ids: set = set()
    used_offers: set = set()

    for i, variant in enumerate(variants):
        offer_id = _variant_offer_id(variant, i, base_offer_id)
        if offer_id in used_offers:
            expansion.dropped.append({
                "index": i, "sku_id": offer_id,
                "color": str(variant.get("color") or ""), "reason": "offer_duplicate",
            })
            continue

        dict_id, value_text, layer = match_variant_color(variant, color_attr_id, dictionary_values)
        if dict_id <= 0:
            expansion.dropped.append({
                "index": i, "sku_id": offer_id,
                "color": str(variant.get("color") or ""), "reason": f"color_{layer or 'no_match'}",
            })
            continue
        if dict_id in used_color_ids:
            expansion.dropped.append({
                "index": i, "sku_id": offer_id,
                "color": str(variant.get("color") or ""), "reason": "color_duplicate",
            })
            continue

        price_v, old_v = variant_list_prices(main_price, main_old_price, variant.get("price_delta_cny"), fx_rate)

        item = copy.deepcopy(base_item)
        item["offer_id"] = offer_id
        if price_v:
            item["price"] = price_v
            item["old_price"] = old_v

        # 颜色属性：替换/插入该 variant 字典值（每 item 恒 1 值 → 值数闸天然合规）
        color_values: List[Dict[str, Any]] = [
            {"dictionary_value_id": dict_id, "value": clean_dict_value(dict_id, value_text)}
        ]
        color_attr: Optional[Dict[str, Any]] = None
        for attr in item.get("attributes") or []:
            if isinstance(attr, dict):
                try:
                    if int(attr.get("id") or 0) == color_attr_id:
                        color_attr = attr
                        break
                except (TypeError, ValueError):
                    continue
        if color_attr is not None:
            color_attr["values"] = color_values
        else:
            (item.setdefault("attributes", [])).append({
                "complex_id": 0, "id": color_attr_id, "values": color_values,
            })

        # 9048 合卡键强制同值（base 已设主 item_id；此处兜底覆写防上游漂移）
        if merge_9048:
            _set_9048(item, merge_9048)

        # 每 variant 主图：该色生图优先（缺/败回落 base 主图——AI 图，过 payload 图源闸）
        _set_variant_gallery(item, i, variant_primary_images, base_primary, shared_images)

        used_color_ids.add(dict_id)
        used_offers.add(offer_id)
        expansion.items.append(item)
        logger.info(
            "[multi_sku_expand] variant[%d] offer=%s color=%s → dict_id=%s(%s) price=%s/%s",
            i, offer_id, str(variant.get("color") or "")[:24], dict_id, layer, price_v, old_v,
        )

    expansion.marks = {
        "mode": "color_dict",
        "requested": len(variants),
        "kept": len(expansion.items),
        "dropped": len(expansion.dropped),
        "color_attr_id": color_attr_id,
        "merge_9048": merge_9048,
    }
    for d in expansion.dropped:
        # 剔除必须如实 log（纪律：不静默降级）
        logger.warning(
            "[multi_sku_expand] variant 剔除: idx=%s sku=%s color=%s reason=%s",
            d.get("index"), d.get("sku_id"), str(d.get("color") or "")[:24], d.get("reason"),
        )
    logger.info(
        "[multi_sku_expand] 完成: 请求 %d → 保留 %d，剔除 %d（9048=%s）",
        len(variants), len(expansion.items), len(expansion.dropped), merge_9048[:40],
    )
    return expansion


def _set_variant_gallery(
    item: Dict[str, Any],
    idx: int,
    variant_primary_images: Optional[List[str]],
    base_primary: str,
    shared_images: List[str],
) -> None:
    """每 variant 主图：该色生图优先（缺/败回落 base 主图——AI 图，过 payload
    图源闸）；共享营销图全变体复用。颜色字典/9048-only 两模式共用。"""
    primary = ""
    if variant_primary_images and idx < len(variant_primary_images):
        primary = str(variant_primary_images[idx] or "").strip()
    if not primary:
        primary = base_primary
    item["primary_image"] = primary
    gallery = [primary] if primary else []
    for img in shared_images or []:
        s = str(img or "").strip()
        if s and s != primary and s not in gallery:
            gallery.append(s)
    item["images"] = gallery[:15]  # Ozon 单 item 图上限（与既有变体分支同口径）


# ────────────────────────── 9048-only 降级合卡 ──────────────────────────


def should_degrade_to_9048_only(
    expansion: MultiSkuExpansion,
    attributes_schema: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    """颜色字典展开全剔除 → 是否降级 9048-only 合卡（纯函数判定）。

    触发条件（双 gate 铁证 1，三条件同时成立）：
    1. 展开产物 items 全空（全部 variant 被剔除）；
    2. 剔除原因全是颜色匹配族（``color_*``：no_match/missing/ambiguous）——
       结构性剔除（offer 重复/超限）不触发，回退单 SKU 现状；
    3. 类目 schema **确无**颜色字典属性（schema_has_color_dict_attr=False）——
       schema 有颜色字典而全剔除多半是字典缓存缺失（瞬时故障），此时降级会
       上出无颜色区分的 items，宁可回退单 SKU 等重试。

    返回 (是否降级, 原因码)；原因码进 log 留痕。
    """
    if expansion is None or expansion.items:
        return False, ""
    drops = expansion.dropped or []
    if not drops:
        return False, ""
    if not all(str(d.get("reason") or "").startswith("color_") for d in drops):
        return False, "structural_drop_present"
    if schema_has_color_dict_attr(attributes_schema):
        # schema 有颜色字典却全无匹配 = 字典缓存缺失类瞬时故障 → 不降级（回退单 SKU）
        return False, "schema_has_color_dict_attr"
    return True, "no_color_dict_schema_all_color_dropped"


def expand_items_9048_only(
    base_item: Dict[str, Any],
    variants: List[Dict[str, Any]],
    *,
    shared_images: List[str],
    variant_primary_images: Optional[List[str]] = None,
    main_price: Any = 0,
    main_old_price: Any = 0,
    fx_rate: float = 1.0,
    merge_9048: str = "",
    base_offer_id: str = "",
    max_variants: int = MAX_VARIANTS,
) -> MultiSkuExpansion:
    """9048-only 降级合卡展开（纯函数）：类目无颜色字典属性时的合卡形态，
    对齐竞品 4929923490 实测结构（同 9048 多 items，非颜色字典维度）。

    与颜色字典路径（expand_multi_sku_items）差异：
    - **不写颜色属性**（该类目没有；防御性剥离 base 误带的 COLOR_ATTR_IDS 属性）；
    - 变体区分 = 标题色尾缀：``f"{主标题}, {俄语色名}"``（derive_ru_color_suffix；
      俄语化走 COLOR_ZH_TO_RU 语言桥，西里尔原文直用）；
    - **无法确定俄语色名的 variant 剔除**（宁缺毋滥：绝不中文尾缀、绝不音译编造）；
    - 保留变体 < MIN_MERGE_VARIANTS_9048_ONLY → 返回空 items（调用方回退单 SKU）；
    - 其余同颜色字典路径：offer_id=variant sku_id 唯一、9048 全 items 同值、
      price=主定价±delta、每 variant 主图一张。

    marks.mode="merge_9048_only" 留痕；全流程剔除清单如实 log 不静默。
    """
    expansion = MultiSkuExpansion()
    variants = [v for v in (variants or []) if isinstance(v, dict)]
    base = base_item or {}
    base_primary = str(base.get("primary_image") or "").strip()
    base_name = str(base.get("name") or "").strip()

    # 超上限截断（保前 N，剔除清单如实记录；与颜色字典路径同口径）
    for j, v in enumerate(variants[max_variants:], start=max_variants):
        expansion.dropped.append({
            "index": j, "sku_id": str(v.get("sku_id") or ""),
            "color": str(v.get("color") or ""), "reason": "limit_exceeded",
        })
    variants = variants[:max_variants]

    used_offers: set = set()
    for i, variant in enumerate(variants):
        offer_id = _variant_offer_id(variant, i, base_offer_id)
        if offer_id in used_offers:
            expansion.dropped.append({
                "index": i, "sku_id": offer_id,
                "color": str(variant.get("color") or ""), "reason": "offer_duplicate",
            })
            continue

        color_text = _variant_color_text(variant)
        suffix = derive_ru_color_suffix(color_text)
        if not suffix:
            reason = "color_missing" if not color_text else "color_untranslatable"
            expansion.dropped.append({
                "index": i, "sku_id": offer_id,
                "color": color_text, "reason": reason,
            })
            continue

        price_v, old_v = variant_list_prices(main_price, main_old_price, variant.get("price_delta_cny"), fx_rate)

        item = copy.deepcopy(base)
        item["offer_id"] = offer_id
        if price_v:
            item["price"] = price_v
            item["old_price"] = old_v
        item["name"] = f"{base_name}, {suffix}" if base_name else suffix

        # 该类目无颜色字典属性 → 防御性剥离 base 误带的颜色属性（正常不在场=零操作）
        for attr_id in COLOR_ATTR_IDS:
            before = len(item.get("attributes") or [])
            item["attributes"] = [
                a for a in (item.get("attributes") or [])
                if not (isinstance(a, dict) and _safe_attr_id(a) == attr_id)
            ]
            if len(item.get("attributes") or []) != before:
                logger.warning(
                    "[multi_sku_9048_only] 剥离 base 误带颜色属性 id=%s（该类目 schema 无颜色字典属性）",
                    attr_id)

        # 9048 合卡键强制同值（合卡是本模式唯一目的，兜底覆写防上游漂移）
        if merge_9048:
            _set_9048(item, merge_9048)

        # 每 variant 主图：该色生图优先（缺/败回落 base 主图）
        _set_variant_gallery(item, i, variant_primary_images, base_primary, shared_images)

        used_offers.add(offer_id)
        expansion.items.append(item)
        logger.info(
            "[multi_sku_9048_only] variant[%d] offer=%s color=%s → 尾缀=%s price=%s/%s",
            i, offer_id, color_text[:24], suffix[:24], price_v, old_v,
        )

    # 至少保 2 变体才值得降级合卡，否则回退单 SKU 现状（宁缺毋滥）
    if 0 < len(expansion.items) < MIN_MERGE_VARIANTS_9048_ONLY:
        kept = expansion.items
        for it in kept:
            expansion.dropped.append({
                "index": -1, "sku_id": str(it.get("offer_id") or ""),
                "color": "", "reason": "below_min_merge_variants",
            })
        expansion.items = []
        logger.warning(
            "[multi_sku_9048_only] 可译变体仅 %d 个（<%d）→ 放弃降级，回退单 SKU",
            len(kept), MIN_MERGE_VARIANTS_9048_ONLY,
        )

    expansion.marks = {
        "mode": "merge_9048_only",
        "requested": len(variants),
        "kept": len(expansion.items),
        "dropped": len(expansion.dropped),
        "merge_9048": merge_9048,
    }
    for d in expansion.dropped:
        # 剔除必须如实 log（纪律：不静默降级）
        logger.warning(
            "[multi_sku_9048_only] variant 剔除: idx=%s sku=%s color=%s reason=%s",
            d.get("index"), d.get("sku_id"), str(d.get("color") or "")[:24], d.get("reason"),
        )
    logger.info(
        "[multi_sku_9048_only] 完成: 请求 %d → 保留 %d，剔除 %d（9048=%s）",
        len(variants), len(expansion.items), len(expansion.dropped), merge_9048[:40],
    )
    return expansion


def _safe_attr_id(attr: Dict[str, Any]) -> int:
    try:
        return int(attr.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _set_9048(item: Dict[str, Any], value: str) -> None:
    """item 9048 强制同值（合卡键）。"""
    for attr in item.get("attributes") or []:
        if isinstance(attr, dict):
            try:
                if int(attr.get("id") or 0) == 9048:
                    attr["values"] = [{"dictionary_value_id": 0, "value": value}]
                    return
            except (TypeError, ValueError):
                continue
    (item.setdefault("attributes", [])).append({
        "complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": value}],
    })
