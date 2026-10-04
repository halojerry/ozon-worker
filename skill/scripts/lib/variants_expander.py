# -*- coding: utf-8 -*-
"""多 SKU 合卡 V1 — variants 展开（PLAN-multi-sku-v1 V1 skill 腿）。

职责边界（cloud_probe 冻结增长——展开逻辑全在本模块，1688 采集腿只留薄调用点）：

- **输入**：cloud_probe ``build_graph_envelope`` §5 产出的折叠前 variants
  列表（形状 ``{sku_id, name, color, model, size, image, price,
  original_price, attributes, variant_type}``， bait/定制 SKU 已过滤）；
- **输出**：draft.variants 合卡形状（与 worker V2 的契约面，键逐字）::

      {"sku_id": str, "color": str, "color_dict_id": None,
       "price_delta_cny": float, "ref_image": str}

  ``draft.multi_sku: True`` 由调用方置位（本模块不碰信封结构）；
- **颜色判定**：沿用现有 SKU 键解析产物 ``attributes["颜色"]``（非空才认，
  本模块零再解析、零 LLM）；颜色名中文原样透传——**俄语翻译在 worker V2**，
  skill 不本地翻译（零 LLM 红线）；
- **上限**：``MAX_MULTI_SKU_VARIANTS = 15``，超限取列表前 15（1688 原始
  顺序），剔除清单经 logger.warning 打印；
- **价格 delta**：相对主 SKU（=折叠代表档）的 1688 价差（CNY，负=减价），
  主 SKU 恒 0；国内运费不进 delta（主定价 purchase_cost 已含运费，worker V2
  对 delta 只做加减不再走定价公式）；
- **color_dict_id**：恒 None——Ozon 颜色字典在 worker 侧（attr_value_matcher
  链），skill 无字典可查，可空键占位保持形状稳定。

V1 范围：仅 1688 采集腿（build_graph_envelope / cli graph --variants）；
跨平台/跟卖/discover 腿维持现状（缺省关 = 单 SKU 零变化）。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Ozon 卡变体上限实测 20+，保守 15（PLAN-multi-sku-v1 §1）
MAX_MULTI_SKU_VARIANTS = 15

# 与 worker V2 的契约面（draft.variants[i] 键集合，逐字）
_VARIANT_KEYS = ("sku_id", "color", "color_dict_id", "price_delta_cny", "ref_image")


def _color_of(variant: dict) -> str:
    """取该 SKU 的颜色维度值；无颜色键/空串 → ""（视为无颜色维度）。"""
    attrs = variant.get("attributes")
    if not isinstance(attrs, dict):
        return ""
    return str(attrs.get("颜色") or "").strip()


def _resolve_base_price(variants: list[dict], base_sku_id: str) -> float:
    """解析主 SKU 基准价（折叠代表档的折叠前 1688 价，不含运费）。

    信任序：sku_id 精确命中 > 全列表最低正价 > 0.0（全缺失时 delta 退化为
    原价，调用方信封仍成立，worker V2 按主 SKU 定价兜底）。
    """
    if base_sku_id:
        for v in variants:
            if str(v.get("sku_id") or "") == base_sku_id:
                try:
                    p = float(v.get("price") or 0)
                except (TypeError, ValueError):
                    p = 0.0
                if p > 0:
                    return p
    for v in variants:
        try:
            p = float(v.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if p > 0:
            return p
    return 0.0


def expand_multi_sku_variants(
    variants: list[dict],
    *,
    base_sku_id: str = "",
    max_variants: int = MAX_MULTI_SKU_VARIANTS,
) -> dict[str, Any]:
    """1688 折叠前 variants → draft.variants 合卡形状（≤15，颜色维度）。

    Returns:
        ``{"variants": [合卡形状...], "dropped": [超限剔除清单...],
          "skipped_no_color": int}``
        - ``variants`` 空 = 该商品无颜色维度 SKU（调用方**不置**
          ``draft.multi_sku``，单 SKU 现状零变化）；
        - ``dropped`` 元素 ``{sku_id, color, price}``（超限剔除，仅供打印）。

    同色多 SKU（颜色×尺寸组合）V1 按 1 SKU : 1 variant 直映射不合并——
    每 entry 带自身 sku_id/delta，V3 gate 如暴露同色冲突再议。
    """
    src = [v for v in (variants or []) if isinstance(v, dict)]
    eligible = [v for v in src if _color_of(v)]
    skipped_no_color = len(src) - len(eligible)
    if not eligible:
        if src and skipped_no_color:
            logger.info(
                "多SKU展开: %d 个 SKU 均无颜色维度 → 不产 draft.variants（单SKU）",
                skipped_no_color,
            )
        return {"variants": [], "dropped": [], "skipped_no_color": skipped_no_color}

    base_price = _resolve_base_price(src, base_sku_id)

    kept = eligible[: max(1, int(max_variants))]
    overflow = eligible[len(kept):]

    out: list[dict] = []
    for v in kept:
        try:
            price = float(v.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        delta = round(price - base_price, 2) if price > 0 and base_price > 0 else 0.0
        out.append({
            "sku_id": str(v.get("sku_id") or ""),
            "color": _color_of(v),
            "color_dict_id": None,
            "price_delta_cny": delta,
            "ref_image": str(v.get("image") or ""),
        })

    dropped = [
        {
            "sku_id": str(v.get("sku_id") or ""),
            "color": _color_of(v),
            "price": v.get("price"),
        }
        for v in overflow
    ]
    if dropped:
        logger.warning(
            "多SKU展开: %d 色超上限 %d，取前 %d，剔除: %s",
            len(eligible), len(kept), len(kept),
            ", ".join(f"{d['sku_id']}({d['color']})" for d in dropped),
        )
    logger.info(
        "多SKU展开: %d 色进 draft.variants（主SKU=%s 基准价=%.2f"
        " delta区间 %+.2f~%+.2f; 无颜色跳过 %d）",
        len(out), base_sku_id or "?", base_price,
        min((v["price_delta_cny"] for v in out), default=0.0),
        max((v["price_delta_cny"] for v in out), default=0.0),
        skipped_no_color,
    )
    return {"variants": out, "dropped": dropped, "skipped_no_color": skipped_no_color}


def multi_sku_price_span(variants: list[dict]) -> tuple[float, float] | None:
    """draft.variants 的采购 delta 区间 ``(min_delta, max_delta)``；空 → None。"""
    deltas: list[float] = []
    for v in variants or []:
        if not isinstance(v, dict):
            continue
        try:
            deltas.append(float(v.get("price_delta_cny") or 0))
        except (TypeError, ValueError):
            continue
    if not deltas:
        return None
    return (min(deltas), max(deltas))


def print_multi_sku_estimate(variants: list[dict], main_price: float | None) -> int:
    """CLI 预估展示（``graph --variants``）：逐行打印每 variant delta 与售价近似。

    - 售价近似 = 主 SKU 预估售价 + 该 variant 采购 delta（worker V2 定价口径
      「主定价 ± delta」的展示侧同构；预估非终价免责由主预估行承担）；
    - ``main_price=None``（worker 不可达无预估）→ 只打数量 + delta 区间一行，
      不打售价（绝不本地补公式——v0.83 降级纪律同源）；
    - 返回变体数（0 = 非多 SKU 信封，调用方零输出）。cli.py 冻结增长，
      展示逻辑集中于此，调用方只留薄调用点。
    """
    rows = [v for v in (variants or []) if isinstance(v, dict)]
    n = len(rows)
    if n == 0:
        return 0
    span = multi_sku_price_span(rows) or (0.0, 0.0)
    if main_price is None:
        print(f"🎨 多SKU: {n} 变体 | Δ采购 {span[0]:+.2f}~{span[1]:+.2f}"
              f"（无预估，变体售价未估）", flush=True)
        return n
    print(f"🎨 多SKU: {n} 变体 | Δ采购 {span[0]:+.2f}~{span[1]:+.2f}"
          f" → 变体售价区间 ≈{main_price + span[0]:.2f}~{main_price + span[1]:.2f}",
          flush=True)
    for v in rows:
        try:
            d = float(v.get("price_delta_cny") or 0)
        except (TypeError, ValueError):
            d = 0.0
        print(f"   · {v.get('sku_id') or '?'} {v.get('color') or '?'}"
              f" Δ{d:+.2f} → ≈{main_price + d:.2f}", flush=True)
    return n
