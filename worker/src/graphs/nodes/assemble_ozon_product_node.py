"""
统一商品组装节点 — 替代 4 节点管线

将 category_lookup + attributes_fetch + attributes_llm + attributes_learning
合并为单一 Python 函数，消除跨节点状态传递 bug。

流程:
  1. PG 缓存查询 → pg_trgm 搜索 top-15 候选类目
  2. LLM 类目匹配 → 从候选中选出 description_category_id + type_id
  3. PG 缓存查询 → 获取属性 schema + 字典值
  4. LLM 完整组装 → 输出完整 /v3/product/import items JSON
  5. 解析校验 → 写入 state 兼容下游节点

替代节点:
  - category_lookup_node
  - attributes_fetch_node
  - attributes_llm_node
  - attributes_learning_node
"""

import os
import json
import time
import logging
import re
import requests
from pathlib import Path
from typing import Any, Optional
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from storage.database.db import get_session

from graphs.state import GlobalState
from utils.mxou_llm import call_mxou_chat_api
from utils.progress_logger import ProgressLogger
from utils.ozon_category_query import get_category_query, OzonCategoryQuery
from utils.http_session import session
from utils.attribute_utils import HAZARD_DICT_ATTR_IDS, is_customs_attr, pick_dict_fallback_value  # ⚠️ v0.16 海关不填 / v0.21 兜底规则
from utils.attribute_utils import match_attr_name_synonym  # v0.32 属性名词汇分歧同义词匹配
from utils.attr_synonyms import load_attr_synonyms  # v0.32 共享同义词加载器（单一事实源）

# ── v0.21: 外置同义词映射（1688 词 → Ozon ZH 类目词），解决字面匹配同义词不通 ──
_SYNONYMS_CACHE: dict | None = None
_SYNONYMS_PATH = Path(__file__).resolve().parents[3] / "config" / "category_synonyms.json"


def _to_int(v) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def apply_competitor_fallback(weight_g, depth_mm, width_mm, height_mm, extensions):
    """竞品重量/尺寸兜底（v0.22）：1688 数据缺失时用 Ozon 竞品值。

    Skill follow 链路把竞品 what_to_sell 重量（4497）与尺寸（9454/9455/9456）
    透传到 extensions.competitor_weight_g / competitor_dimensions_mm。
    v0.22 P3: 入参任意类型安全转换（None/字符串不崩溃）；任一维度缺失即整体兜底。
    """
    ext = extensions or {}
    weight_g = _to_int(weight_g)
    depth_mm = _to_int(depth_mm)
    width_mm = _to_int(width_mm)
    height_mm = _to_int(height_mm)
    if weight_g <= 0 and ext.get("competitor_weight_g"):
        cw = _to_int(ext["competitor_weight_g"])
        if cw > 0:
            logger.info("✅ 1688 重量缺失，用竞品重量 %dg 兜底", cw)
            weight_g = cw
    # 任一维度缺失即用竞品整套尺寸（不再要求三边全 0）
    if depth_mm <= 0 or width_mm <= 0 or height_mm <= 0:
        cd = ext.get("competitor_dimensions_mm") or {}
        if isinstance(cd, dict):
            n_d = _to_int(cd.get("length") or cd.get("depth"))
            n_w = _to_int(cd.get("width"))
            n_h = _to_int(cd.get("height"))
            if n_d > 0 and n_w > 0 and n_h > 0:
                logger.info("✅ 1688 尺寸缺失，用竞品尺寸 %d×%d×%dmm 兜底", n_d, n_w, n_h)
                return weight_g, n_d, n_w, n_h
    return weight_g, depth_mm, width_mm, height_mm


def _load_category_synonyms() -> dict:
    global _SYNONYMS_CACHE
    if _SYNONYMS_CACHE is None:
        try:
            _SYNONYMS_CACHE = json.loads(_SYNONYMS_PATH.read_text(encoding="utf-8")) or {}
        except Exception as _e:
            logger.warning(f"category_synonyms.json 加载失败（回退空表）: {_e}")
            _SYNONYMS_CACHE = {}
    return _SYNONYMS_CACHE


def _apply_leaf_bonus(candidates: list, leaf_name: str, synonyms: dict) -> list:
    """v0.21: 末级类目词（含同义词）命中节点名 → 加分，打破字面匹配 tie。

    例：后视镜 → 摩托车后视镜(+0.5) 胜过 单车裤；震动棒 → 振动器(同义词+0.5) 胜过 适应性器具餐具。
    精确同名 +0.6，包含 +0.3——避免「振动器」误伤「振动器配件」。
    """
    if not leaf_name or not candidates:
        return candidates
    bonus_terms = {leaf_name}
    for s in (synonyms or {}).get(leaf_name, []) or []:
        if isinstance(s, str) and len(s.strip()) >= 2:
            bonus_terms.add(s.strip())
    for c in candidates:
        name = c.get("node_name") or ""
        if any(t == name for t in bonus_terms if len(t) >= 2):
            c["_score"] = float(c.get("_score", c.get("similarity", 0))) + 0.6
        elif any(name.endswith(t) for t in bonus_terms if len(t) >= 2):
            c["_score"] = float(c.get("_score", c.get("similarity", 0))) + 0.5
        elif any(t in name for t in bonus_terms if len(t) >= 2):
            c["_score"] = float(c.get("_score", c.get("similarity", 0))) + 0.3
    candidates.sort(key=lambda x: -(float(x.get("_score", x.get("similarity", 0)))))
    return candidates


def _merge_candidates(a: list, b: list) -> list:
    """按 (dc, tp) 去重合并候选，保留高分；用于 source_keywords + 标题关键词双路搜索。"""
    merged: dict[tuple, dict] = {}
    for c in list(a) + list(b):
        key = (c.get("description_category_id"), c.get("type_id"))
        if not all(key):
            continue
        if key not in merged:
            merged[key] = c
            continue
        # v0.34: 同 dc/tp 保留 similarity 更高的版本——否则源搜索 sim=0.80 的正确类目
        # 被全标题搜索 sim=0.455 的同一类目覆盖, 导致 _acceptable_match 误拒 (竹知了实证)
        sim_cur = merged[key].get("similarity")
        sim_new = c.get("similarity")
        if sim_new is not None and (sim_cur is None or float(sim_new) > float(sim_cur)):
            merged[key] = c
    return sorted(merged.values(), key=lambda x: -(float(x.get("_score", x.get("similarity", 0)))))


def _l0_consistent(l0_hit: dict | None, candidates: list, top_n: int = 5) -> bool:
    """v0.21: L0 学习缓存命中的类目必须与 L1 高分候选一致，否则视为污染映射。"""
    if not l0_hit:
        return False
    l0_dc = int(l0_hit.get("description_category_id", 0) or 0)
    l0_tp = int(l0_hit.get("type_id", 0) or 0)
    if not l0_dc or not l0_tp:
        return False
    return any(
        int(c.get("description_category_id", 0) or 0) == l0_dc
        and int(c.get("type_id", 0) or 0) == l0_tp
        for c in (candidates or [])[:top_n]
    )


# ── v0.31.x: 类目匹配最低接受门槛（三路搜索 similarity 语义不同，不能共用数值门槛）──
# - jieba (ZH_HANS): similarity = 匹配token数 / 总token数 → 0.5
# - pg_trgm (RU): similarity = func.similarity 0-1 实数 → 0.3
# - ILIKE fallback: similarity = 匹配词数 / 总词数 → 0.5
MIN_SIM_BY_MATCHER = {"jieba": 0.5, "pg_trgm": 0.3, "ili": 0.5}


def _acceptable_match(best: dict) -> bool:
    """类目候选是否达到最低接受门槛（纯函数，可单测）。

    从候选推断 matcher：
    - 显式 `matcher` 字段优先
    - 否则 `matched_tokens` 非空 → jieba
    - 否则默认按 pg_trgm 标尺（最宽松，避免误拒无标记候选）
    无 similarity 的候选不阻断（兼容无分候选）。

    注意：L0/Skill 命中由调用方放行（不设门槛），本函数只判普通候选。
    """
    if not isinstance(best, dict) or not best:
        return True
    matcher = best.get("matcher")
    if not matcher and best.get("matched_tokens"):
        matcher = "jieba"
    if not matcher:
        matcher = "pg_trgm"
    threshold = MIN_SIM_BY_MATCHER.get(matcher, MIN_SIM_BY_MATCHER["pg_trgm"])
    sim = best.get("similarity")
    if sim is None:
        return True  # 无分候选不阻断
    try:
        return float(sim) >= threshold
    except (TypeError, ValueError):
        return True


def _confidence_from_sim(sim) -> float:
    """候选 similarity → match_confidence（0~1 clamp），无分默认 0.5。"""
    if sim is None:
        return 0.5
    try:
        return max(0.0, min(1.0, float(sim)))
    except (TypeError, ValueError):
        return 0.5


logger = logging.getLogger(__name__)

# ==================== 常量 ====================

# 品牌属性ID列表（按优先级）
BRAND_ATTRIBUTE_IDS = [85, 31, 5076]  # 31=服装和鞋类品牌(帽子等服装类目必填, wave4 风扇帽实证缺 31 被拒)

# "无品牌" 字典值
NO_BRAND_DICT_ID = 126745801
NO_BRAND_VALUE = "Нет бренда"

# 原产国（中国）
COUNTRY_ATTR_ID = 4389
CHINA_DICT_ID = 90296
CHINA_VALUE = "Китай"

# Ozon 强制属性
FORCE_ATTR_9048 = 9048   # 变体绑定名
FORCE_ATTR_8229 = 8229   # 类型名称
FORCE_ATTR_4191 = 4191   # 完整描述
FORCE_ATTR_4180 = 4180   # 短描述/关键字
FORCE_ATTR_4958 = 4958   # 适用对象（部分类目）
FORCE_ATTR_8962 = 8962   # 件数（部分类目）
FORCE_ATTR_23171 = 23171 # hashtag 标签（部分类目）

# 分类名属性（8229 的替代）
TYPE_NAME_ATTR_IDS = [8229]

# 集合属性（values 数组可包含多个元素）
COLLECTION_ATTR_IDS = {9048, 23171}


def _build_hardcoded_attributes(_description_category_id: int) -> list[dict[str, Any]]:
    """type_id 无效时的最小属性集，避免 Ozon 校验空属性直接报错。"""
    attrs: list[dict[str, Any]] = []
    # 品牌 → Нет бренда（所有可能的 brand attribute ID）
    for brand_id in BRAND_ATTRIBUTE_IDS:
        attrs.append({
            "id": brand_id,
            "complex_id": 0,
            "values": [{"dictionary_value_id": NO_BRAND_DICT_ID, "value": NO_BRAND_VALUE}],
        })
    # 原产国 → Китай
    attrs.append({
        "id": COUNTRY_ATTR_ID,
        "complex_id": 0,
        "values": [{"dictionary_value_id": CHINA_DICT_ID, "value": CHINA_VALUE}],
    })
    return attrs


def _assemble_follow_sell(
    state: GlobalState,
    draft: dict[str, Any],
    title: str,
    images: list[str],
    pricing_info: dict[str, Any],
    progress: Any,
) -> dict[str, Any]:
    """跟卖模式组装：跳过 LLM 类目匹配和属性填写，直接构建 payload。"""
    from utils.ozon_category_query import get_category_query

    query = get_category_query()
    # ✅ v0.20 A: 空类目安全转换（跟卖 UPDATE 省略类目时）
    description_category_id = int(state.description_category_id) \
        if str(state.description_category_id or "").isdigit() else 0
    type_id = int(state.type_id) \
        if str(getattr(state, "type_id", "") or "").isdigit() else 0
    ozon_client_id = str(state.ozon_client_id or "")
    ozon_api_key = state.ozon_api_key or ""

    logger.info(f"🔄 跟卖组装: cat={description_category_id}/{type_id}, title={title[:60]}")

    # ✅ v0.20 A: type_id=0 时——
    #   UPDATE（product_id 已存在）→ 省略类目，Ozon 保留原卡片类目（不阻断）
    #   CREATE（无 product_id）→ 阻断（无类目无法建卡）
    if type_id <= 0:
        if not getattr(state, "product_id", None):
            logger.error(f"❌ 跟卖 type_id 无效({type_id})，无法获取属性 schema（CREATE 需要类目）")
            return {
                "error_message": f"跟卖 type_id 无效: {type_id}，请检查类目解析",
                "description_category_id": str(description_category_id),
                "type_id": str(type_id),
                "final_attributes": _build_hardcoded_attributes(description_category_id),
            }
        logger.warning(f"⚠️ 跟卖 UPDATE 无有效类目(type_id={type_id})，"
                       "省略类目字段，Ozon 保留原卡片类目")
    else:
        # Step 2: 获取属性 Schema（仅用于验证，不实际填写）
        progress.log_node_action(f"跟卖 Step 2: 获取属性 Schema — cat={description_category_id}")
        attr_schema = query.get_attribute_schema(description_category_id, type_id)
        if attr_schema and isinstance(attr_schema, dict) and attr_schema.get("result"):
            attr_list: list[dict[str, Any]] = attr_schema["result"]
        else:
            attr_list = _fetch_attribute_schema_from_ozon(
                ozon_client_id, ozon_api_key, description_category_id, type_id
            )
        logger.info(f"   属性 Schema: {len(attr_list)} 个属性")

        # 获取俄语类目路径
        try:
            from storage.database.db import get_engine
            from sqlalchemy import text
            engine = get_engine()
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT full_path FROM category_tree_nodes "
                    "WHERE description_category_id=:cid AND type_id=:tid AND language='RU' LIMIT 1"
                ), {"cid": description_category_id, "tid": type_id}).fetchone()
                if row:
                    logger.info(f"   🇷🇺 俄语类目: {row[0]}")
        except Exception:
            pass

    # 定价信息
    # ⚠️ v0.14 P1-4: 不再 "1000" 兜底 —— 定价失败由 graph 层 [PRICING_FAILED] 路由阻断
    price_rub = str(pricing_info.get("price", ""))
    old_price_rub = str(pricing_info.get("old_price", ""))

    # ⚠️ v0.14 P0-2: 消费 follow_sell_import 输出的 final_attributes（Ozon 格式 id+values）
    # 旧代码硬编码 {"id": 126745801, ...} —— 把品牌字典值 ID(126745801) 当属性 ID，Ozon 拒绝；
    # 且不消费 follow 输出导致跟卖属性全丢。现在展开为：
    #   - attrs_for_payload（Ozon 格式）→ items[0]["attributes"]
    #   - final_attrs_flat（prepare 兼容 attribute_id 格式）→ final_attributes
    follow_attrs_raw: list = list(getattr(state, 'final_attributes', None) or [])
    attrs_for_payload: list[dict[str, Any]] = []
    final_attrs_flat: list[dict[str, Any]] = []
    for _fa in follow_attrs_raw:
        if not isinstance(_fa, dict):
            continue
        _aid = _fa.get("id") or _fa.get("attribute_id")
        if _aid is None:
            continue
        _vals = _fa.get("values") or []
        if not _vals or not isinstance(_vals, list) or not isinstance(_vals[0], dict):
            continue
        _v0 = _vals[0]
        _dv_id = int(_v0.get("dictionary_value_id", 0) or 0)
        _v_txt = str(_v0.get("value", ""))
        attrs_for_payload.append({
            "complex_id": 0,
            "id": int(_aid),
            "values": [{"dictionary_value_id": _dv_id, "value": _v_txt}],
        })
        final_attrs_flat.append({
            "attribute_id": int(_aid),
            "value": _v_txt,
            "dictionary_value_id": _dv_id,
        })
    if not attrs_for_payload:
        # 兜底：无 follow 属性时用最小硬编码集（品牌=Нет бренда + 原产国=Китай）
        attrs_for_payload = _build_hardcoded_attributes(description_category_id)
        final_attrs_flat = [
            {"attribute_id": int(a["id"]),
             "value": str(a["values"][0]["value"]),
             "dictionary_value_id": int(a["values"][0]["dictionary_value_id"])}
            for a in attrs_for_payload
        ]
        logger.warning("⚠️ 跟卖无 follow 属性输出，使用最小硬编码属性集")
    logger.info(f"✅ 跟卖消费 follow 属性: {len(attrs_for_payload)} 个")

    # 构建 payload items
    sku_id = draft.get("sku_id", draft.get("item_id", ""))
    # ✅ v0.25 FIX: 统一裸竞品 ID（无 follow_ 前缀），与 follow_sell_import/prepare 一致，
    # 避免 api 复制模式与后续 UPDATE 的 offer_id 不一致导致双卡
    offer_id = str(draft.get("ozon_product_id", sku_id))
    weight_g = draft.get("weight", 100)
    dims = draft.get("dimensions", {}) or {}
    # ✅ v0.22: 1688 数据缺失时用竞品重量/尺寸兜底（follow 链路 skill 透传）
    _ext_fb = (getattr(state, "envelope", None) or {}).get("extensions", {}) or {}
    if _ext_fb.get("competitor_weight_g") or _ext_fb.get("competitor_dimensions_mm"):
        weight_g, _dl, _dw, _dh = apply_competitor_fallback(
            weight_g, dims.get("length", dims.get("depth", 0)),
            dims.get("width", 0), dims.get("height", 0), _ext_fb,
        )
        dims = {"length": _dl, "width": _dw, "height": _dh}
    depth = int(dims.get("length", dims.get("depth", 100)))
    width = int(dims.get("width", 100))
    height = int(dims.get("height", 100))

    items = [{
        "offer_id": offer_id,
        "name": title,
        "description": draft.get("description", title),
        "category_id": description_category_id,
        "price": price_rub,
        "old_price": old_price_rub,
        "vat": "0",
        "currency_code": getattr(state, 'currency_code', None) or "RUB",
        "images": images[:10] if images else [],
        "attributes": attrs_for_payload,
        "depth": max(1, depth // 10),  # mm → cm
        "width": max(1, width // 10),
        "height": max(1, height // 10),
        "weight": max(1, weight_g),
        "dimension_unit": "cm",
        "weight_unit": "g",
    }]

    logger.info(f"✅ 跟卖组装完成: offer_id={offer_id}, images={len(images)}, price={price_rub}")

    return {
        "ozon_payload": {"items": items},
        "final_attributes": final_attrs_flat,
        "description_category_id": str(description_category_id),
        "type_id": str(type_id),
    }


def _resolve_skill_category(draft_ozon_cat: dict) -> dict | None:
    """v0.27 方案B: 校验 Skill 传入的直采类目(Seller 空间)是否在树中有效。

    直采信封若带 draft.ozon_category(search_categories / poll_category=True 解析),
    dc+tp 组合在 category_tree_nodes 存在即返回 l0_hit 结构(供 assemble 跳过 pg_trgm);
    品牌页 ID / 错配 / 文本值 → None(退回 pg_trgm 猜)。

    Returns:
        {"description_category_id", "type_id", "full_path", ...} 或 None
    """
    if not draft_ozon_cat or not draft_ozon_cat.get("description_category_id"):
        return None
    _dc_s = str(draft_ozon_cat["description_category_id"])
    _tp_s = str(draft_ozon_cat.get("type_id", _dc_s))
    if not (_dc_s.isdigit() and _tp_s.isdigit()):
        return None
    try:
        from sqlalchemy import text as _sql_t0
        with get_session() as _s0:
            _row0 = _s0.execute(_sql_t0(
                "SELECT full_path FROM category_tree_nodes "
                "WHERE description_category_id=:dc AND type_id=:tp AND language='ZH_HANS' LIMIT 1"
            ), {"dc": int(_dc_s), "tp": int(_tp_s)}).fetchone()
        if not _row0:
            logger.warning(f"⚠️ 直采 Skill 类目 {_dc_s}/{_tp_s} 树中不存在, 不走用, 退回 pg_trgm")
            return None
        return {
            "description_category_id": int(_dc_s),
            "type_id": int(_tp_s),
            "full_path": _row0[0] or "",
            "node_name": "",
            "similarity": 1.0,
            "confidence": 0.95,
            "reason": "skill_search_categories",
        }
    except Exception as _e0:
        logger.warning(f"直采 Skill 类目校验异常(退回 pg_trgm): {_e0}")
        return None


def assemble_ozon_product_node(
    state: GlobalState,
    config: RunnableConfig,
    runtime: Runtime,
) -> dict[str, Any]:
    """
    统一商品组装节点。

    输入: GlobalState（含 draft, token, ozon_client_id, ozon_api_key, pricing_info）
    输出: dict（被 LangGraph 合并到 GlobalState）
    """
    progress = ProgressLogger()
    progress.log_node_start("assemble_ozon_product", "统一商品组装")
    
    # ✅ 自修复：如果是重试（类目匹配回退），递增计数器
    retry_count = getattr(state, 'assembly_retry_count', 0)
    if retry_count > 0:
        logger.info(f"   🔄 组装重试 (第{retry_count}次)")
    progress.log_node_action("Step 1: 类目匹配...")

    draft: dict[str, Any] = state.draft or {}
    token: str = state.token or ""
    ozon_client_id: str = str(state.ozon_client_id or "")
    ozon_api_key: str = state.ozon_api_key or ""
    currency_code: str = state.currency_code or "RUB"

    title: str = draft.get("title", "")
    description: str = draft.get("description", "")
    images: list[str] = draft.get("images", []) or []
    weight_grams: int = draft.get("weight", 100)
    dimensions: dict[str, int] = draft.get("dimensions", {}) or {}
    # ✅ v0.22: 1688 数据缺失时用竞品重量/尺寸兜底（follow 链路 skill 透传）
    _ext_fb = (getattr(state, "envelope", None) or {}).get("extensions", {}) or {}
    if _ext_fb.get("competitor_weight_g") or _ext_fb.get("competitor_dimensions_mm"):
        weight_grams, _dl, _dw, _dh = apply_competitor_fallback(
            weight_grams, dimensions.get("length", dimensions.get("depth", 0)),
            dimensions.get("width", 0), dimensions.get("height", 0), _ext_fb,
        )
        dimensions = {"length": _dl, "width": _dw, "height": _dh}
    purchase_cost: float = float(draft.get("purchase_cost", 0) or 0)
    sku_id: str = draft.get("sku_id", "")
    attributes_1688: dict[str, Any] = draft.get("attributes", {}) or {}
    variants: list[dict[str, Any]] = draft.get("variants", []) or []

    # ✅ v0.21: 尺寸为 skill 估算值（1688 未提供）→ 显式告警，Ozon 可能拒绝 INCORRECT_DIMENSION
    if draft.get("dimensions_estimated"):
        logger.warning(
            f"⚠️ 商品 {draft.get('item_id', '')} 尺寸为估算值（1688 页面未提供），"
            f"Ozon 可能报 INCORRECT_DIMENSION/ML_INCORRECT_VOLUME_WEIGHT"
        )

    # 定价信息（来自 pricing_node）
    pricing_info: dict[str, Any] = state.pricing_info if hasattr(state, 'pricing_info') else {}
    # ⚠️ v0.14 P1-4: 定价失败不兜底 "1000" —— 由 graph 层在 pricing 后路由阻断（[PRICING_FAILED]）
    price_rub: str = str(pricing_info.get("price", ""))
    old_price_rub: str = str(pricing_info.get("old_price", ""))

    if not title:
        logger.error("产品标题为空，无法进行类目匹配")
        return {"error_message": "产品标题为空，无法进行类目匹配",
                "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1}

    # 🆕 跟卖模式：类目已由前序节点设置（或 Skill 从 Ozon 页面提取），直接跳到属性组装
    extensions = state.envelope.get("extensions", {}) if state.envelope else {}
    # ✅ 优先用 draft.ozon_category（Skill 端从 Ozon 竞品页面提取的类目名/ID）
    draft_ozon_cat = draft.get("ozon_category", {}) if draft else {}
    if extensions.get("follow_sell"):
        if state.description_category_id:
            return _assemble_follow_sell(state, draft, title, images, pricing_info, progress)
        elif draft_ozon_cat.get("description_category_id"):
            dc_val = str(draft_ozon_cat["description_category_id"])
            tp_val = str(draft_ozon_cat.get("type_id", dc_val))
            # ✅ 若是纯数字 → 直接用；若是文本 → 搜 PG 类目树找到真实 ID
            if dc_val.isdigit() and tp_val.isdigit():
                # ✅ v0.20 A: 数字 ID 必须通过类目树校验才采用——品牌页 ID（甩脂机
                # Luxhommè/101029485）会被 Ozon 以"类型不属于该类目"整包拒绝，
                # 导致图也不落卡。校验失败则保持空（prepare 省略类目，UPDATE 由
                # Ozon 保留原卡片类目）。
                q = get_category_query()
                node = q.get_node_by_description_category_id(int(dc_val))
                if node:
                    state.description_category_id = dc_val
                    state.type_id = tp_val
                    logger.info(f"✅ 跟卖类目(来自 Skill 数字ID, 已校验): dc={dc_val} type={tp_val}")
                    return _assemble_follow_sell(state, draft, title, images, pricing_info, progress)
                else:
                    logger.warning(f"⚠️ Skill 数字类目 {dc_val}/{tp_val} 未通过类目树校验"
                                   "（可能是品牌页），不采用，走 1688/省略类目")
            else:
                # 文本类目名 → pg_trgm 搜索（使用模块级导入的 get_category_query）
                q = get_category_query()
                candidates = q.search_nodes(dc_val, top_k=5, node_type="type", language="RU")
                if candidates:
                    best = candidates[0]
                    state.description_category_id = str(best["description_category_id"])
                    state.type_id = str(best["type_id"])
                    logger.info(f"✅ 跟卖类目(来自 Skill 文本→pg_trgm): '{dc_val}' → dc={state.description_category_id} type={state.type_id} ({best['full_path']})")
                    return _assemble_follow_sell(state, draft, title, images, pricing_info, progress)
                else:
                    logger.warning(f"⚠️ Skill 类目文本 '{dc_val}' 在 PG 树中未找到，省略类目走 UPDATE")
            # ✅ v0.20 A: 类目不可用（品牌页/未找到）→ 置空并直接走跟卖组装，
            # 绝不掉进 1688 类目匹配（会匹配出无效 dc/type 对，整包被 Ozon 拒）
            state.description_category_id = ""
            state.type_id = ""
            return _assemble_follow_sell(state, draft, title, images, pricing_info, progress)

    # 初始化查询助手
    query = get_category_query()

    # =====================================================
    # Step 0.5 (v0.27): Skill 类目优先 — 直采信封若带 Seller 空间类目
    # (skill search_categories / poll_category=True 解析), dc+tp 组合在树中
    # 存在即采用, 跳过 pg_trgm 猜。与跟卖分支共用同一校验逻辑。
    # =====================================================
    _skill_l0_hit = None
    if (not extensions.get("follow_sell")
            and draft_ozon_cat.get("description_category_id")
            and not getattr(state, "description_category_id", None)):
        _skill_l0_hit = _resolve_skill_category(draft_ozon_cat)
        if _skill_l0_hit:
            logger.info(f"✅ 直采类目(来自 Skill search_categories, 已校验): "
                        f"dc={_skill_l0_hit['description_category_id']} "
                        f"type={_skill_l0_hit['type_id']} ({(_skill_l0_hit['full_path'] or '')[:70]})")

    # =====================================================
    # Step 1: 类目匹配
    # =====================================================
    logger.info(f"🔍 Step 1: 类目匹配 — 产品: {title[:60]}")

    # 1a. 提取搜索关键词（jieba 分词 + 1688 类目面包屑）
    keywords = _extract_keywords(title, description, attributes_1688)
    
    # 追加 1688 类目面包屑作为搜索词（与 Ozon ZH_HANS 类目名直接匹配）
    # ✅ v0.21: 优先用 source.source_category_path（skill 新信封为完整路径），兼容旧 draft.source_category
    _src = getattr(state, "source", None) or {}
    source_category = str(_src.get("source_category_path") or "").strip() or draft.get("source_category", "")
    # ⚠️ v0.34: leaf_name 默认空串 — 信封无 source_category 时防 UnboundLocalError（v0.21 引入的 pre-existing bug）
    leaf_name = ""
    source_keywords = ""
    if source_category:
        # ✅ 分割所有分隔符：> 、 / → 空格
        import re as _re
        cleaned = _re.sub(r'[>、/→]', ' ', source_category)
        cat_terms = [t.strip() for t in cleaned.split() if len(t.strip()) >= 2]
        if cat_terms:
            # 提取最具体的末级类目词（最后 1 个，如"其他益智玩具"；末级词整体辨识度最高）
            # ⚠️ v0.34: 原取最后 2 级(cat_terms[-2:])——"科教玩具 其他益智玩具"分词后
            # 「玩具」token 稀释末级词信号, sim 0.5→0.333 错配到甜品套装。只留末级词整体。
            specific_terms = cat_terms[-1:]
            # 泛化词黑名单（在多类目中出现，稀释信号）
            _GENERIC_WORDS = {"运动", "休闲", "传统", "家用", "日用", "通用", "其他", "配件", "附件"}
            specific_terms = [t for t in specific_terms if t not in _GENERIC_WORDS]
            if not specific_terms:
                specific_terms = [t for t in cat_terms if t not in _GENERIC_WORDS][-1:]
            leaf_name = cat_terms[-1] if cat_terms else ""  # v4: L0学习缓存key
            
            # 中文同义词映射（1688 用语 → Ozon ZH_HANS 用语）
            _CN_SYNONYMS = {
                "喷壶": "喷雾瓶 喷雾器 浇花壶",
                "洒水壶": "浇花壶 喷雾器",
                "浇花壶": "喷雾瓶 喷雾器",
                "加仑盆": "花盆 塑料花盆",
                # 宠物：1688 用"猫狗/猫猫"，Ozon 用"宠物"
                "猫猫玩具": "宠物玩具",
                "猫狗玩具": "宠物玩具",
                "逗猫棒": "宠物玩具",
                "猫玩具": "宠物玩具",
                "猫猫食具": "宠物碗 宠物餐具",
                "猫狗食具": "宠物碗 宠物餐具",
                # 宠物狗用品
                "牵引绳": "宠物牵绳 宠物牵引绳",
                "狗绳": "宠物牵绳 宠物牵引绳",
                "遛狗绳": "宠物牵绳 宠物牵引绳",
                "狗链": "宠物牵绳 宠物牵引绳",
                "胸背带": "宠物胸背带 宠物背心",
                "狗胸背带": "宠物胸背带",
                "训犬": "宠物训练用品",
                "狗碗": "宠物碗 宠物餐具",
                "猫碗": "宠物碗 宠物餐具",
                "狗窝": "宠物床 宠物窝",
                "猫窝": "宠物床 宠物窝",
                "狗衣服": "宠物服装",
                "猫衣服": "宠物服装",
                "宠物用品": "宠物用品 宠物配件",
                "猫狗用品": "宠物用品",
                # 园艺
                "园艺工具": "园艺工具 花园工具",
                "园林资材": "园艺工具 花园",
                # 手套：过滤掉"防护"（匹配消防/建筑），保留"园艺"
                "通用手套": "园艺手套",
                "手部防护": "园艺手套",
            }
            expanded_terms = list(specific_terms)
            for term in specific_terms:
                if term in _CN_SYNONYMS:
                    for syn in _CN_SYNONYMS[term].split():
                        if syn not in expanded_terms:
                            expanded_terms.append(syn)
            # ✅ v0.21: 外置同义词表合并（覆盖 inline 表缺失的词）
            for _ext_term, _ext_syns in _load_category_synonyms().items():
                if _ext_term in specific_terms:
                    for _syn in (_ext_syns if isinstance(_ext_syns, list) else [str(_ext_syns)]):
                        if isinstance(_syn, str) and _syn not in expanded_terms:
                            expanded_terms.append(_syn)
            # ✅ jieba 分词末级类目，提取最有辨识度的词（如"戏水玩具"→["戏水","玩具"]）
            try:
                import jieba as _jieba
                for term in specific_terms:
                    jieba_words = [w for w in _jieba.cut(term) if len(w) >= 2]
                    for w in jieba_words:
                        if w not in expanded_terms:
                            expanded_terms.append(w)
            except Exception:
                pass
            source_keywords = " ".join(expanded_terms)
            keywords = source_keywords + " " + keywords
        logger.info(f"   关键词（含1688类目）: {keywords}")
    else:
        logger.info(f"   关键词: {keywords}")

    # 1b. 搜索策略：source_keywords 优先（高精度），不够再扩大
    MIN_CANDIDATES = 1  # 有 source_category 时，1 个精确结果 > 30 个噪声结果
    
    # ✅ v0.21: source_keywords 与标题关键词双路搜索合并——
    # 单一用 source_keywords（如"其他电动车配件/野营折叠桌椅"）时，泛化词会把搜索带偏
    src_candidates = query.search_nodes(source_keywords, top_k=15, node_type="type") if source_keywords else []
    kw_candidates = query.search_nodes(keywords, top_k=30, node_type="type")
    candidates = _merge_candidates(src_candidates, kw_candidates)
    if candidates:
        logger.info(f"   ✅ 双路搜索合并：{len(candidates)} 个候选（src={len(src_candidates)} + kw={len(kw_candidates)}）")
    # ✅ v0.27: Skill 类目优先 — 校验通过的 Skill 类目置于候选首位, 后续 best=candidates[0] 命中
    if _skill_l0_hit:
        candidates.insert(0, _skill_l0_hit)
    
    if not candidates:
        # 回退：不过滤 node_type
        if source_keywords:
            candidates = query.search_nodes(source_keywords, top_k=15, node_type=None)
            if not candidates or len(candidates) < MIN_CANDIDATES:
                candidates = query.search_nodes(keywords, top_k=30, node_type=None)
        else:
            candidates = query.search_nodes(keywords, top_k=30, node_type=None)

    if not candidates:
        # 缓存为空，调用 Ozon API 获取类目树
        logger.warning("类目缓存为空，调用 Ozon API 获取类目树...")
        tree_data = _fetch_category_tree_from_ozon(ozon_client_id, ozon_api_key)
        if tree_data:
            # 缓存并同步
            from utils.local_db_manager import LocalDBManager
            local_db = LocalDBManager()
            local_db.set_category_cache(ozon_client_id, tree_data)
            local_db.sync_category_tree_nodes(tree_data)
            # 重试搜索（优先 source_keywords）
            if source_keywords:
                candidates = query.search_nodes(source_keywords, top_k=15, node_type="type")
                if not candidates or len(candidates) < MIN_CANDIDATES:
                    candidates = query.search_nodes(keywords, top_k=30, node_type="type")
            else:
                candidates = query.search_nodes(keywords, top_k=30, node_type="type")
            if not candidates:
                candidates = query.search_nodes(keywords, top_k=30, node_type=None)

    if not candidates:
        logger.error("❌ 类目搜索无结果（Ozon API 也无数据）")
        return {"error_message": "类目匹配失败：无候选类目",
                "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1}

    logger.info(f"   pg_trgm 返回 {len(candidates)} 个候选")

    # ✅ v4: L2 指纹重排
    candidates = _apply_fingerprint_rerank(query, candidates, source_keywords, keywords)

    # ✅ v0.21: 末级类目词（含同义词）加权必须放在 L2 之后——
    # 否则指纹重排会把刚加的权重覆盖掉（折叠椅→户外折叠椅配件 被 多功能折叠工具 顶掉）
    candidates = _apply_leaf_bonus(candidates, leaf_name, _load_category_synonyms())

    # ✅ v5: 初始化匹配质量标记
    match_layer = "L1"       # pg_trgm / jieba LIKE
    match_confidence = 0.5   # 默认中等置信度

    # ✅ v4: L0 学习缓存查找（在候选选择前，命中则跳过 overlap 验证）
    l0_hit = _match_category_layered(
        query, source_category, source_keywords, keywords, candidates, leaf_name,
        source_category_id=draft.get("source_category_id"),
    ) if source_category else None
    # ✅ v0.27: Skill 类目优先(直采) — 校验通过则直接采用, 不走学习表/一致性检查
    if not l0_hit and _skill_l0_hit:
        l0_hit = _skill_l0_hit
        match_layer = "Skill"
        match_confidence = 0.95
        logger.info(f"   ✅ Skill类目覆盖: [{l0_hit['description_category_id']}/{l0_hit['type_id']}]")

    # ✅ v0.21: L0 映射必须与 L1 高分候选一致，否则忽略（防旧脏数据固化错类目）
    if l0_hit and not _l0_consistent(l0_hit, candidates):
        _cand_desc = ", ".join(
            f"{c.get('description_category_id')}/{c.get('type_id')}" for c in candidates[:5]
        )
        logger.warning(
            f"⚠️ L0映射与L1 top5候选不一致，忽略L0（防固化）: "
            f"[{l0_hit.get('description_category_id')}/{l0_hit.get('type_id')}] vs "
            f"[{_cand_desc}]"
        )
        l0_hit = None
        # v0.34: 丢弃 Skill/L0 后必须重置 match_layer——否则 stale "Skill" 会跳过
        # L848 的接受门槛（sim=0.2 低分候选被采用但 confidence 低 → route 阻断，
        # 行为矛盾：正确类目被采用却被 0.2 置信度卡死）
        if match_layer in ("L0", "Skill"):
            match_layer = "L1"

    # 1c. 直接使用 pg_trgm 最高相似度候选（不用 LLM）
    # pg_trgm 搜索已按 sim DESC 排序，candidates[0] 即最佳匹配
    # LLM 匹配不可靠（如玩具→鞋类），关键词匹配更准确
    best = candidates[0]
    # ✅ v0.31.x: 低分候选（sim 低于接受门槛）不直接采用——记日志后走既有
    # overlap 验证 → LLM fallback 链（最终采纳点在 L779 前再判定阻断）
    if not l0_hit and not _acceptable_match(best):
        _matcher = best.get("matcher", "pg_trgm")
        logger.warning(
            f"   ⚠️ 类目匹配 sim={best.get('similarity', 0):.3f} 低于接受门槛"
            f"({MIN_SIM_BY_MATCHER.get(_matcher, MIN_SIM_BY_MATCHER['pg_trgm'])})，"
            f"转 LLM fallback: {best['full_path'][:60]}"
        )
    # ✅ v0.31.x: match_confidence 挂钩真实 sim（L0/Skill 覆盖为 0.95）
    match_confidence = _confidence_from_sim(best.get("similarity"))
    category_result = {
        "description_category_id": best["description_category_id"],
        "type_id": best["type_id"],
        "category_path": best["full_path"],
        "confidence": "high" if best.get("similarity", 0) > 0.3 else "medium",
        "reason": f"pg_trgm 最高相似度 ({best.get('similarity', 0):.3f}): {best['node_name']}",
        "similarity": best.get("similarity", 0),
        "matcher": best.get("matcher", "pg_trgm"),
    }
    logger.info(f"   ✅ 类目匹配 (pg_trgm): [{best['description_category_id']}/{best['type_id']}] {best['full_path']} (sim={best.get('similarity', 0):.3f})")

    # ✅ v4: L0命中 → 跳过 overlap 验证，直接使用学习缓存结果
    if l0_hit:
        category_result = l0_hit
        match_layer = "L0"
        match_confidence = 0.95
        logger.info(f"   ✅ L0覆盖: [{category_result['description_category_id']}/{category_result['type_id']}]")

    # ✅ 关键词重叠验证（L0未命中时执行）
    # 例如 "烟灰缸" 被 pg_trgm 误匹配到 "珠宝秤" → 无重叠词 → 丢弃该候选
    # ✅ v0.8.0 修复: 改用子串匹配代替 set intersection，解决中文分词问题
    # ✅ v5 修复: 过滤泛化词（"用品"、"工具"等），避免父类目名称造成假匹配
    _GENERIC_OVERLAP = {"用品", "工具", "配件", "附件", "设备", "材料", "系列", "套装", "商品", "产品",
                       "运动", "休闲", "传统", "家用", "日用", "通用", "其他", "跨境", "新款", "爆款"}
    if not l0_hit:
        # .split() 对中文无空格文本无效（"蓝牙" vs "蓝牙耳机" → 无交集），子串匹配解决
        _source_words = [w.strip().lower() for w in (source_keywords or keywords).split() if len(w.strip()) >= 2]
        for _c in candidates:
            _cand_path = _c.get("full_path", "").lower()
            _overlap = set()
            for sw in _source_words:
                if sw in _cand_path:
                    _overlap.add(sw)
            # v5: 过滤泛化词，只有非泛化词重叠才算真正匹配
            _specific_overlap = _overlap - _GENERIC_OVERLAP
            if _specific_overlap:
                category_result = {
                    "description_category_id": _c["description_category_id"],
                    "type_id": _c["type_id"],
                    "category_path": _c["full_path"],
                    "confidence": "high",
                    "reason": f"pg_trgm+overlap (sim={_c.get('similarity', 0):.3f}, words={_specific_overlap})",
                    "similarity": _c.get("similarity", 0),
                    "matcher": _c.get("matcher", "pg_trgm"),
                }
                match_confidence = _confidence_from_sim(_c.get("similarity"))
                logger.info(f"   ✅ 子串验证通过: 候选 '{_c['full_path'][:60]}' 包含源词 {_specific_overlap}")
                break
        else:
            # 无候选有重叠 → LLM fallback：让 LLM 从 top-5 候选中选择最佳匹配
            logger.warning(f"   ⚠️ 所有 {len(candidates)} 个候选与源词无子串重叠，触发 LLM fallback")
            best_by_llm = _llm_rank_categories(candidates[:5], source_keywords or keywords, draft, state)
            # v0.34: LLM 认为候选都不合适 → 用建议搜索词二次搜索
            if best_by_llm and best_by_llm.get("_llm_suggest"):
                _sugg = str(best_by_llm.get("suggest_keywords", "") or "").strip()
                logger.info(f"   🔍 LLM 建议搜索词: {_sugg}，二次搜索类目")
                if _sugg:
                    try:
                        query = get_category_query()
                        re_cands = query.search_nodes(_sugg, top_k=10, node_type="type")
                        if re_cands:
                            candidates = _merge_candidates(re_cands, candidates)
                            logger.info(f"   ✅ 建议词二次搜索: {len(re_cands)} 候选并入")
                            # ⚠️ v0.34 review fix: 合并后必须重跑 LLM 排名——
                            # 否则 best_by_llm 仍是 suggest 标记 dict(full_path 为空) →
                            # 下方重叠检查恒失败 → 硬阻断, 二次搜索白做 (与 L1237 路径对齐)
                            best_by_llm = _llm_rank_categories(candidates[:10], source_keywords or keywords, draft, state)
                    except Exception as _se:
                        logger.warning(f"   ⚠️ 建议词二次搜索失败: {_se}")
            if best_by_llm:
                # ✅ v0.21: LLM 结果也必须与源词有具体重叠，否则不硬猜（阻断，需人工确认类目）
                _llm_path = str(best_by_llm.get("full_path", "")).lower()
                _llm_overlap = {sw for sw in _source_words if sw in _llm_path} - _GENERIC_OVERLAP
                # ⚠️ v0.34 review fix: 重跑后若 LLM 仍返回 suggest 标记(候选并入但全不满意)——
                # 从合并后 candidates 取 top1(已含二次搜索高分候选)回退, 不再硬阻断白做二次搜索
                if not _llm_overlap and best_by_llm.get("_llm_suggest") and candidates:
                    _fb = candidates[0]
                    _fb_path = str(_fb.get("full_path", "")).lower()
                    _fb_overlap = {sw for sw in _source_words if sw in _fb_path} - _GENERIC_OVERLAP
                    if _fb_overlap:
                        best_by_llm = _fb
                        _llm_overlap = _fb_overlap
                        _llm_path = _fb_path
                        logger.info(f"   🔍 suggest 回退: 采用二次搜索 top1 '{_fb.get('full_path', '')[:60]}'")
                if _llm_overlap:
                    category_result = {
                        "description_category_id": best_by_llm["description_category_id"],
                        "type_id": best_by_llm["type_id"],
                        "category_path": best_by_llm["full_path"],
                        "confidence": "medium",
                        "reason": f"LLM_fallback+overlap (words={_llm_overlap})",
                        "similarity": best_by_llm.get("similarity", 0),
                        "matcher": best_by_llm.get("matcher", "pg_trgm"),
                    }
                    match_confidence = _confidence_from_sim(best_by_llm.get("similarity"))
                    logger.info(f"   ✅ LLM fallback 选中（有重叠）: {best_by_llm['full_path'][:80]} {_llm_overlap}")
                else:
                    logger.error(
                        f"   🛑 LLM fallback 结果与源词无具体重叠，阻断上架避免错误类目: "
                        f"{best_by_llm.get('full_path', '')[:80]}"
                    )
                    match_confidence = 0.0
                    return {"error_message": "类目匹配失败：LLM fallback 无可靠结果（需人工确认类目），阻断上架",
                            "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1,
                            "match_confidence": 0.0}
            else:
                # ✅ v5: LLM 也失败 → 阻断上架，不硬用低质量候选
                match_confidence = 0.0
                logger.error(f"   🛑 LLM fallback 也失败，无可靠类目匹配，阻断上架")
                return {"error_message": "类目匹配失败：jieba搜索+LLM均无可靠结果，阻断上架避免错误类目",
                        "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1,
                        "match_confidence": 0.0}

    # ✅ v0.31.x: 最终采纳点门槛 — 非 L0/Skill 且 sim 低于接受门槛 → 走既有阻断分支。
    # 防低分错配（如 sim=0.200 的『儿童多功能学习挂图』）经 direct/overlap/LLM 任一
    # 路径被采用；L0/Skill 直采不设门槛（来源可信）。
    if match_layer not in ("L0", "Skill") and not _acceptable_match(category_result):
        logger.error(
            f"   🛑 类目匹配最终结果 sim={category_result.get('similarity', 0):.3f} "
            f"低于接受门槛，阻断上架避免错误类目: "
            f"{str(category_result.get('category_path', ''))[:80]}"
        )
        match_confidence = 0.0
        return {"error_message": "类目匹配失败：类目相似度低于接受门槛（需人工确认类目），阻断上架",
                "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1,
                "match_confidence": 0.0}

    # ✅ v4: 审计日志 — 记录本次匹配详情到 category_match_log
    _log_match_attempt(state, title, source_category, keywords, category_result, match_layer, match_confidence, candidates)

    description_category_id: int = int(category_result["description_category_id"])
    type_id: int = int(category_result["type_id"])
    category_path: str = category_result.get("category_path", "")
    
    # ✅ 修正 type_id <= 0：尝试从 candidates 中找到有效的 type_id
    if type_id <= 0:
        for c in candidates:
            tid = int(c.get("type_id", 0) or 0)
            if tid > 0:
                type_id = tid
                description_category_id = int(c.get("description_category_id", description_category_id))
                category_path = c.get("full_path", category_path)
                logger.warning(f"   ⚠️ type_id 为 0，从候选修正: [{description_category_id}/{type_id}] {category_path}")
                break
        if type_id <= 0:
            logger.error("   ❌ 类目匹配失败：所有候选的 type_id 都无效")
            return {"error_message": "类目匹配失败：type_id 无效",
                    "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1}
    
    # ✅ 修正 LLM 输出：LLM 有时把 type_id 填到 description_category_id
    # 从 candidates 中查找正确的 description_category_id
    if description_category_id == type_id or description_category_id <= 0:
        for c in candidates:
            if int(c.get("type_id", 0)) == type_id and int(c.get("description_category_id", 0)) > 0:
                description_category_id = int(c["description_category_id"])
                logger.info(f"   🔧 修正 description_category_id: {type_id} → {description_category_id}")
                break
    
    logger.info(f"   ✅ 类目匹配: [{description_category_id}/{type_id}] {category_path}")

    # ✅ Step 1.5: 查俄语类目名（同一 ID，RU 语言，直接SQL）
    ru_category_path: str = ""
    if description_category_id and type_id:
        try:
            from sqlalchemy import text as sql_text
            with get_session() as s:
                row = s.execute(sql_text(
                    "SELECT full_path FROM category_tree_nodes "
                    "WHERE description_category_id=:cid AND type_id=:tid AND language='RU' LIMIT 1"
                ), {"cid": description_category_id, "tid": type_id}).fetchone()
                if row:
                    ru_category_path = row[0]
                    logger.info(f"   🇷🇺 俄语类目: {ru_category_path}")
        except Exception:
            pass

    # =====================================================
    # Step 1d: 验证类目对（防止无效 category_id/type_id 导致后续 400）
    # =====================================================
    tried_category_ids: set = {(description_category_id, type_id)}
    MAX_CATEGORY_RETRIES = 5

    # =====================================================
    # Step 2: 获取属性 Schema（PG 缓存优先，Ozon API 回退）
    # =====================================================
    progress.log_node_action(f"Step 2: 获取属性 Schema — category={description_category_id}, type={type_id}")

    attr_schema = query.get_attribute_schema(description_category_id, type_id)
    if attr_schema:
        # ✅ 兼容两种缓存格式: {"result": [...]} (dict) 和 [...] (list)
        if isinstance(attr_schema, dict) and attr_schema.get("result"):
            attr_list: list[dict[str, Any]] = attr_schema["result"]
            logger.info(f"   ✅ PG 缓存命中 (dict): {len(attr_list)} 个属性")
        elif isinstance(attr_schema, list):
            attr_list: list[dict[str, Any]] = attr_schema
            logger.info(f"   ✅ PG 缓存命中 (list): {len(attr_list)} 个属性")
        else:
            logger.warning(f"   ⚠️ PG 缓存格式未知: {type(attr_schema)}, 回退到 Ozon API")
            attr_list = []
    else:
        attr_list = []

    if not attr_list:
        # Ozon API 回退（带候选类目自动回退）
        logger.info("   PG 缓存未命中，调用 Ozon API...")
        attr_list = _fetch_attribute_schema_from_ozon(
            ozon_client_id, ozon_api_key,
            description_category_id, type_id
        )
        
        # ✅ 自修复：API 400 时自动尝试候选类目
        retry_idx = 0
        while not attr_list and retry_idx < MAX_CATEGORY_RETRIES:
            # 从候选列表中找下一个未尝试的类目对
            fallback_found = False
            for c in candidates:
                cid = int(c.get("description_category_id", 0))
                tid = int(c.get("type_id", 0))
                if cid > 0 and tid > 0 and (cid, tid) not in tried_category_ids:
                    tried_category_ids.add((cid, tid))
                    logger.warning(
                        f"   🔄 类目对 [{description_category_id}/{type_id}] 无效，"
                        f"回退尝试候选 [{cid}/{tid}] {c.get('full_path', '')}"
                    )
                    description_category_id = cid
                    type_id = tid
                    category_path = c.get("full_path", "")
                    attr_list = _fetch_attribute_schema_from_ozon(
                        ozon_client_id, ozon_api_key, cid, tid
                    )
                    if attr_list:
                        logger.info(f"   ✅ 回退类目对有效: [{cid}/{tid}] {category_path}")
                    fallback_found = True
                    retry_idx += 1
                    break
            if not fallback_found:
                break
        
        if not attr_list:
            logger.error(f"❌ 属性 Schema 获取失败（已尝试 {len(tried_category_ids)} 个类目对）")
            return {"error_message": f"属性 Schema 获取失败: 尝试了 {len(tried_category_ids)} 个类目对均无效",
                    "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1}
        logger.info(f"   ✅ Ozon API 返回: {len(attr_list)} 个属性")

    # 标记必填属性
    required_attrs = [a for a in attr_list if a.get("is_required", False)]
    logger.info(f"   其中 {len(required_attrs)} 个必填属性")

    # =====================================================
    # Step 3: 预加载字典值（PG 缓存优先，Ozon API 回退）
    # =====================================================
    logger.info("📖 Step 3: 预加载字典值")

    dict_lookup: dict[int, list[dict[str, Any]]] = {}
    for attr in attr_list:
        dict_id = attr.get("dictionary_id", 0)
        if dict_id and dict_id > 0:
            attr_id = int(attr.get("id", 0))
            values = query.get_dictionary_values(attr_id, description_category_id, type_id)
            if not values or (isinstance(values, list) and len(values) == 0):
                # PG 缓存未命中 → Ozon API 回退
                logger.info(f"   PG 缓存未命中 attr={attr_id}，调用 Ozon API...")
                values = _fetch_dict_values_from_ozon(
                    ozon_client_id, ozon_api_key,
                    description_category_id, type_id, attr_id,
                    language="ZH_HANS",
                )
                # 写入 PG 缓存（供后续使用）
                if values:
                    _cache_dict_values(attr_id, description_category_id, type_id, values, language="ZH_HANS")
            if values and isinstance(values, list) and len(values) > 0:
                dict_lookup[attr_id] = values
            elif isinstance(values, dict) and values.get("result"):
                dict_lookup[attr_id] = values["result"]

    dict_attr_count = sum(1 for a in attr_list if a.get("dictionary_id", 0) > 0)
    cached_dict_count = len(dict_lookup)
    logger.info(f"   字典属性: {dict_attr_count} 个, PG 缓存命中: {cached_dict_count} 个")

    # =====================================================
    # Step 4: 确定性组装 items（不调用 LLM）
    # =====================================================
    logger.info("🔧 Step 4: 确定性构建 /v3/product/import items JSON")

    items = _build_items_deterministically(
        draft=draft,
        description_category_id=description_category_id,
        type_id=type_id,
        attr_list=attr_list,
        dict_lookup=dict_lookup,
        images=images,
        ozon_client_id=ozon_client_id,
        ozon_api_key=ozon_api_key,
        weight_grams=weight_grams,
        dimensions=dimensions,
        price_rub=price_rub,
        old_price_rub=old_price_rub,
        currency_code=currency_code,
        token=token,
    )

    if not items:
        logger.error("❌ 确定性组装失败，返回空 items")
        return {
            "error_message": "确定性组装失败：未生成有效的 items",
            "description_category_id": str(description_category_id),
            "type_id": str(type_id),
            "attributes_schema": attr_list,
            "dictionary_values": {str(k): v for k, v in dict_lookup.items()},
            "final_attributes": [],
            "llm_attributes": [],
            "learned_attributes": {},
            "ozon_payloads": [],
        }

    logger.info(f"   ✅ 确定性生成 {len(items)} 个 item(s)")

    # =====================================================
    # Step 5: 解析 + 校验 + 补充
    # =====================================================
    logger.info("🔍 Step 5: 解析校验 LLM 输出")

    items = _validate_and_enrich_items(
        items=items,
        attr_list=attr_list,
        dict_lookup=dict_lookup,
        images=images,
        ozon_client_id=ozon_client_id,
        ozon_api_key=ozon_api_key,
        description_category_id=description_category_id,
        type_id=type_id,
        weight_grams=weight_grams,
        dimensions=dimensions,
        draft_title=draft.get("title", ""),
        supplier=draft.get("supplier", ""),
        ru_category_path=ru_category_path,
    )

    # =====================================================
    # Step 6: 提取 final_attributes（兼容下游节点）
    # =====================================================
    # 提取第一个 item 的属性作为 final_attributes（兼容 prepare_ozon_upload）
    final_attributes: list[dict[str, Any]] = []
    if items and items[0].get("attributes"):
        for attr in items[0]["attributes"]:
            for v in (attr.get("values") or []):
                final_attributes.append({
                    "attribute_id": attr["id"],
                    "value": v.get("value", ""),
                    "dictionary_value_id": v.get("dictionary_value_id", 0),
                    "source": "llm",
                })

    # 为兼容 learning_record_node，同时设置 llm_attributes
    llm_attributes = final_attributes

    # 提取 LLM 生成的俄语标题（供 prepare_ozon_upload 使用）
    llm_name = ""
    if items and items[0].get("name"):
        llm_name = str(items[0]["name"])[:500]

    # =====================================================
    # Step 6.5: 跨类目一致性校验
    # =====================================================
    # 对比 LLM 生成的俄语标题与分配的 Ozon 类目路径，检测明显不匹配
    # ✅ v0.8.0 修复 Bug#4: 用俄语类目路径与俄语标题做一致性检查
    # 之前用 ZH_HANS 类目路径比较俄语标题 → 语言不匹配 → 永远不过
    ru_check_path = ru_category_path  # Step 1.5 已查俄语路径
    if not ru_check_path:
        # 回退查询：用 dc+type 查 RU 表
        try:
            from sqlalchemy import text as _sql_text2
            with get_session() as _s2:
                _row2 = _s2.execute(_sql_text2(
                    "SELECT full_path FROM category_tree_nodes "
                    "WHERE description_category_id=:cid AND type_id=:tid AND language='RU' LIMIT 1"
                ), {"cid": description_category_id, "tid": type_id}).fetchone()
                if _row2:
                    ru_check_path = _row2[0]
        except Exception:
            pass
    
    category_consistent = _check_category_consistency(
        llm_name, ru_check_path or category_path, description_category_id, type_id
    )

    if not category_consistent:
        # 类目不匹配 → 尝试用俄语标题重新匹配类目
        logger.warning(f"⚠️ 类目不一致，尝试用俄语标题重新匹配...")
        recategorize_failed = True
        re_candidates = []  # v0.8.0: 初始化防止 UnboundLocalError
        try:
            query = get_category_query()
            # ✅ v0.8.0 修复 Bug#4: llm_name 是俄语标题，用 RU 搜索；失败时用 ZH_HANS 回退
            re_candidates = query.search_nodes(llm_name[:50], top_k=10, node_type="type", language="RU")
            if not re_candidates:
                # 回退：ZH_HANS 搜索（俄语标题也可能含中文相似词）
                re_candidates = query.search_nodes(llm_name[:50], top_k=10, node_type="type")
            if re_candidates:
                # 检查新候选中是否有更好的匹配
                for candidate in re_candidates[:3]:
                    re_cat_id = candidate.get("description_category_id", 0)
                    re_type_id = candidate.get("type_id", 0)
                    re_path = candidate.get("full_path", "")
                    if re_cat_id and re_type_id and (re_cat_id != description_category_id or re_type_id != type_id):
                        # ✅ v0.8.0 修复 Bug#4: 查新候选的俄语路径再验证
                        re_ru_path = ""
                        try:
                            from sqlalchemy import text as _sql_text3
                            with get_session() as _s3:
                                _row3 = _s3.execute(_sql_text3(
                                    "SELECT full_path FROM category_tree_nodes "
                                    "WHERE description_category_id=:cid AND type_id=:tid AND language='RU' LIMIT 1"
                                ), {"cid": re_cat_id, "tid": re_type_id}).fetchone()
                                if _row3:
                                    re_ru_path = _row3[0]
                        except Exception:
                            pass
                        # 用俄语路径验证一致性
                        re_consistent = _check_category_consistency(llm_name, re_ru_path or re_path, re_cat_id, re_type_id)
                        if re_consistent:
                            logger.info(f"✅ 重新匹配成功: {description_category_id}/{type_id} → {re_cat_id}/{re_type_id} ({re_path})")
                            # ✅ v0.9.0: 类目变更后完整重建属性 schema + items + final_attributes
                            rebuild_result = _rebuild_for_new_category(
                                new_dc=re_cat_id, new_type=re_type_id,
                                draft=draft, images=images,
                                ozon_client_id=ozon_client_id, ozon_api_key=ozon_api_key,
                                weight_grams=weight_grams, dimensions=dimensions,
                                price_rub=price_rub, old_price_rub=old_price_rub,
                                currency_code=currency_code, token=token,
                                ru_category_path=re_ru_path,
                            )
                            if rebuild_result:
                                description_category_id = re_cat_id
                                type_id = re_type_id
                                category_path = re_path
                                ru_category_path = re_ru_path
                                attr_list = rebuild_result["attr_list"]
                                dict_lookup = rebuild_result["dict_lookup"]
                                items = rebuild_result["items"]
                                final_attributes = rebuild_result["final_attributes"]
                                llm_attributes = final_attributes
                                llm_name = rebuild_result["llm_name"]
                                logger.info(f"   ✅ 类目变更+属性重建完成: {len(final_attributes)} 个属性")
                            else:
                                # 降级: 至少更新 attr_list
                                new_attr_schema = query.get_attribute_schema(re_cat_id, re_type_id)
                                if new_attr_schema:
                                    if isinstance(new_attr_schema, dict) and new_attr_schema.get("result"):
                                        attr_list = new_attr_schema["result"]
                                    elif isinstance(new_attr_schema, list):
                                        attr_list = new_attr_schema
                                    logger.info(f"   ⚠️ 仅更新 schema（重建失败）: {len(attr_list)} 个属性")
                                description_category_id = re_cat_id
                                type_id = re_type_id
                                category_path = re_path
                                ru_category_path = re_ru_path
                            recategorize_failed = False
                            break
        except Exception as _re_match_e:
            logger.warning(f"   ⚠️ 重新匹配异常: {_re_match_e}")

        if recategorize_failed:
            # ✅ v0.8.0 修复 Bug#3: re-match 失败 → LLM fallback，不再直接保留错误类目
            logger.warning(f"   ⚠️ pg_trgm 重新匹配失败，触发 LLM fallback")
            # 收集所有候选（来自初始 pg_trgm 搜索 + re-match 搜索）
            all_candidates = candidates[:10]  # 初始 pg_trgm 搜索的 candidates (在外部作用域)
            if re_candidates:
                # 合并去重
                seen = {(c.get("description_category_id"), c.get("type_id")) for c in all_candidates}
                for rc in re_candidates:
                    key = (rc.get("description_category_id"), rc.get("type_id"))
                    if key not in seen:
                        all_candidates.append(rc)
                        seen.add(key)
            # 提取产品中文关键词用于 LLM
            product_keywords = _extract_keywords(
                (draft or {}).get("title", ""),
                (draft or {}).get("description", ""),
                (draft or {}).get("attributes", {})
            )
            best_by_llm = _llm_rank_categories(all_candidates[:5], product_keywords, draft, state)
            # v0.34: LLM 认为候选都不合适 → 用建议搜索词二次搜索并入候选
            if best_by_llm and best_by_llm.get("_llm_suggest"):
                _sugg2 = str(best_by_llm.get("suggest_keywords", "") or "").strip()
                logger.info(f"   🔍 LLM 建议搜索词: {_sugg2}，二次搜索并入候选")
                if _sugg2:
                    try:
                        query = get_category_query()
                        _re2 = query.search_nodes(_sugg2, top_k=10, node_type="type")
                        if _re2:
                            _seen_keys = {(c.get("description_category_id"), c.get("type_id")) for c in all_candidates}
                            for _rc in _re2:
                                _key = (_rc.get("description_category_id"), _rc.get("type_id"))
                                if _key not in _seen_keys:
                                    all_candidates.append(_rc)
                                    _seen_keys.add(_key)
                            best_by_llm = _llm_rank_categories(all_candidates[:10], product_keywords, draft, state)
                            logger.info(f"   ✅ 建议词二次搜索后重试 LLM: 候选 {len(all_candidates)}")
                    except Exception as _se2:
                        logger.warning(f"   ⚠️ 建议词二次搜索失败: {_se2}")
            if best_by_llm:
                llm_cid = best_by_llm.get("description_category_id", 0)
                llm_tid = best_by_llm.get("type_id", 0)
                if llm_cid and llm_tid and (llm_cid != description_category_id or llm_tid != type_id):
                    logger.info(f"✅ LLM fallback 重新分类: {description_category_id}/{type_id} → {llm_cid}/{llm_tid} ({best_by_llm.get('full_path', '')})")
                    # ✅ v0.9.0: 类目变更后完整重建属性 schema + items + final_attributes
                    rebuild_result = _rebuild_for_new_category(
                        new_dc=llm_cid, new_type=llm_tid,
                        draft=draft, images=images,
                        ozon_client_id=ozon_client_id, ozon_api_key=ozon_api_key,
                        weight_grams=weight_grams, dimensions=dimensions,
                        price_rub=price_rub, old_price_rub=old_price_rub,
                        currency_code=currency_code, token=token,
                        ru_category_path=ru_category_path,
                    )
                    if rebuild_result:
                        description_category_id = llm_cid
                        type_id = llm_tid
                        category_path = best_by_llm.get("full_path", category_path)
                        attr_list = rebuild_result["attr_list"]
                        dict_lookup = rebuild_result["dict_lookup"]
                        items = rebuild_result["items"]
                        final_attributes = rebuild_result["final_attributes"]
                        llm_attributes = final_attributes
                        llm_name = rebuild_result["llm_name"]
                        # 更新俄语路径
                        try:
                            from sqlalchemy import text as _sql_text4
                            with get_session() as _s4:
                                _row4 = _s4.execute(_sql_text4(
                                    "SELECT full_path FROM category_tree_nodes "
                                    "WHERE description_category_id=:cid AND type_id=:tid AND language='RU' LIMIT 1"
                                ), {"cid": llm_cid, "tid": llm_tid}).fetchone()
                                if _row4:
                                    ru_category_path = _row4[0]
                        except Exception:
                            pass
                        logger.info(f"   ✅ 类目变更+属性重建完成: {len(final_attributes)} 个属性")
                    else:
                        # 降级: 至少更新 attr_list
                        new_attr_schema = query.get_attribute_schema(llm_cid, llm_tid)
                        if new_attr_schema:
                            if isinstance(new_attr_schema, dict) and new_attr_schema.get("result"):
                                attr_list = new_attr_schema["result"]
                            elif isinstance(new_attr_schema, list):
                                attr_list = new_attr_schema
                        description_category_id = llm_cid
                        type_id = llm_tid
                        category_path = best_by_llm.get("full_path", category_path)
                        try:
                            from sqlalchemy import text as _sql_text4
                            with get_session() as _s4:
                                _row4 = _s4.execute(_sql_text4(
                                    "SELECT full_path FROM category_tree_nodes "
                                    "WHERE description_category_id=:cid AND type_id=:tid AND language='RU' LIMIT 1"
                                ), {"cid": llm_cid, "tid": llm_tid}).fetchone()
                                if _row4:
                                    ru_category_path = _row4[0]
                        except Exception:
                            pass
                        logger.warning(f"   ⚠️ 仅更新 schema（重建失败）: {len(attr_list)} 个属性")
                else:
                    logger.warning(f"   ⚠️ LLM fallback 选中相同类目或无变化")
            else:
                # LLM fallback 也失败 → 保留原类目 ID（降级到 pg_trgm 最高 sim）
                logger.error(
                    f"❌ 类目一致性严重失败：产品「{llm_name[:60]}」与类目「{category_path}」无共同关键词，"
                    f"且 pg_trgm 和 LLM 重新匹配均失败。保留原类目 [{description_category_id}/{type_id}]，由 Ozon 验证。"
                )

    # =====================================================
    # Step 7: 返回结果 dict（LangGraph 自动合并到 GlobalState）
    # =====================================================
    progress.log_node_success(f"类目={category_path}, 属性={len(final_attributes)}个, items={len(items)}个")

    logger.info(f"✅ 统一组装完成: 类目=[{description_category_id}/{type_id}], 属性={len(final_attributes)}个, items={len(items)}个")

    return {
        "description_category_id": str(description_category_id),
        "type_id": str(type_id),
        # ⚠️ v0.32 修复: 匹配类目中文末级名回填 state（生图提示词 {{category}} 兜底，draft.category 恒空时）
        "category_name": category_path.split(">")[-1].strip() if category_path else "",
        "attributes_schema": attr_list,
        "dictionary_values": {str(k): v for k, v in dict_lookup.items()},  # ← 键必须是 str（PrepareOzonUploadInput 要求）
        "final_attributes": final_attributes,
        "llm_attributes": llm_attributes,
        "learned_attributes": {},
        "ozon_payloads": [{"items": items}],
        "match_confidence": match_confidence,  # v5: 路由阻断用
        # 传递 LLM 生成的俄语标题
        "name": llm_name,
    }


# ==================== 辅助函数 ====================


def _extract_keywords(title: str, description: str, attributes: dict[str, Any]) -> str:
    """从产品数据中提取搜索关键词（使用 jieba 分词+词性标注，取所有有意义词）"""
    import re
    try:
        import jieba
        import jieba.posseg as pseg
    except ImportError:
        jieba = None
        pseg = None

    # 清理标题：取前 100 字符（足够覆盖产品名）
    clean = re.sub(r'[^\u4e00-\u9fff\w]', ' ', title)[:100]

    if jieba and pseg:
        # 词性标注分词，按优先级排序
        # 名词 > 动名词 > 形容词 > 其他（去噪）
        NOISE_WORDS = {'无', '手动', '全部', '展开', '参数', '厂家', '批发', '一件', '代发',
                       '跨境', '货源', '直销', '新款', '爆款', '热卖', '促销', '一件代发'}
        # v0.8.0: 泛化词黑名单也用于过滤 jieba 关键词，防止语义稀释
        _GENERIC_WORDS = {"运动", "休闲", "传统", "家用", "日用", "通用", "其他", "配件", "附件"}
        word_scores: list[tuple[str, float]] = []
        
        try:
            for word, flag in pseg.cut(clean):
                w = word.strip()
                if len(w) < 2:
                    continue
                if w in NOISE_WORDS or w in _GENERIC_WORDS:
                    continue
                # 词性权重：名词(n/ns/nr/nt/nz) = 3.0, 动名词(vn) = 2.0, 
                #           形容词(a/an) = 1.5, 其他实词 = 1.0
                if flag.startswith('n'):
                    score = 3.0
                elif flag == 'vn':
                    score = 2.0
                elif flag.startswith('a'):
                    score = 1.5
                elif flag in ('v', 'vd', 'vi'):
                    score = 1.0
                else:
                    score = 0.5
                word_scores.append((w, score))
        except Exception:
            # pseg 可能在某些平台上不可用，回退到普通分词
            words = list(jieba.cut(clean))
            _noise = NOISE_WORDS | _GENERIC_WORDS
            meaningful = [w.strip() for w in words 
                         if len(w.strip()) >= 2 and w.strip() not in _noise]
            return ' '.join(meaningful[:8])
        
        # 按分数降序排列，取前 8 个（或全部如果不足 8 个）
        word_scores.sort(key=lambda x: x[1], reverse=True)
        top_words = [w for w, _ in word_scores[:8]]
        
        if not top_words:
            # 如果过滤后为空，回退取所有 >=2 字的词
            words = list(jieba.cut(clean))
            _noise = NOISE_WORDS | _GENERIC_WORDS
            meaningful = [w.strip() for w in words 
                         if len(w.strip()) >= 2 and w.strip() not in _noise]
            return ' '.join(meaningful[:8])
        
        return ' '.join(top_words)
    else:
        # 回退：取前 20 个字符
        return clean[:20]


def _check_category_consistency(
    llm_name: str,
    category_path: str,
    description_category_id: int,
    type_id: int,
) -> bool:
    """
    跨类目一致性校验：对比 LLM 生成的俄语产品名与分配的 Ozon 类目名称。

    Returns:
        True if consistent (or cannot check), False if mismatch detected
    """
    if not llm_name or not category_path:
        return True

    # 提取类目路径中的关键俄语词（取最后两级，通常是最具体的分类）
    path_parts = [p.strip() for p in category_path.split(">") if p.strip()]
    leaf_keywords = set()
    for part in path_parts[-2:]:  # 最后两级
        for word in part.lower().split():
            if len(word) >= 3:
                leaf_keywords.add(word)

    # 检查产品名中是否包含任一类目关键词
    name_lower = llm_name.lower()
    overlap = [kw for kw in leaf_keywords if kw in name_lower]

    if not overlap and leaf_keywords:
        logger.warning(
            f"⚠️ 跨类目一致性警告：产品名「{llm_name[:80]}」与类目「{category_path}」"
            f" 无共同关键词。类目词: {leaf_keywords}。"
            f" 这可能导致 Ozon 审核拒绝（DESCRIPTION_DECLINE）。"
            f" 建议检查类目匹配是否正确 (desc_cat_id={description_category_id}, type_id={type_id})。"
        )
        return False
    elif overlap:
        logger.info(f"✅ 跨类目一致性通过：产品名与类目「{category_path}」匹配关键词: {overlap}")
    return True


def _rebuild_for_new_category(
    new_dc: int, new_type: int,
    draft: dict, images: list,
    ozon_client_id: str, ozon_api_key: str,
    weight_grams: int, dimensions: dict,
    price_rub: str, old_price_rub: str, currency_code: str,
    token: str,
    ru_category_path: str = "",
) -> dict | None:
    """
    v0.9.0: 类目变更后重建属性 schema + items + final_attributes。
    
    类目变更时, LLM fallback 或 pg_trgm re-match 选中了新类目,
    但 Step 4+5 已经用旧类目 schema 构建了 items 和 final_attributes。
    此函数用新类目重新执行 Step 3→4→5→6。
    
    Returns: {"items", "final_attributes", "llm_name", "attr_list", "dict_lookup"} or None
    """
    try:
        query = get_category_query()
        
        # Step 3': 获取新类目的属性 schema（PG 缓存优先, Ozon API 回退）
        new_attr_list = query.get_attribute_schema(new_dc, new_type)
        if new_attr_list:
            if isinstance(new_attr_list, dict) and new_attr_list.get("result"):
                new_attr_list = new_attr_list["result"]
        if not new_attr_list:
            # Ozon API 回退
            logger.info(f"   PG 缓存未命中新类目 [{new_dc}/{new_type}]，调用 Ozon API...")
            new_attr_list = _fetch_attribute_schema_from_ozon(
                ozon_client_id, ozon_api_key, new_dc, new_type
            )
        if not new_attr_list:
            logger.error(f"   ❌ 无法获取新类目 schema: [{new_dc}/{new_type}]")
            return None
        
        logger.info(f"   ✅ 新类目 schema: {len(new_attr_list)} 个属性")
        
        # Step 3'': 预加载字典值（ZH_HANS，与初始逻辑一致）
        new_dict_lookup: dict[int, list[dict[str, Any]]] = {}
        for attr in new_attr_list:
            dict_id = attr.get("dictionary_id", 0)
            if dict_id and dict_id > 0:
                attr_id = int(attr.get("id", 0))
                values = query.get_dictionary_values(attr_id, new_dc, new_type)
                if not values or (isinstance(values, list) and len(values) == 0):
                    logger.info(f"   PG 缓存未命中 attr={attr_id}（新类目），调用 Ozon API...")
                    values = _fetch_dict_values_from_ozon(
                        ozon_client_id, ozon_api_key,
                        new_dc, new_type, attr_id,
                        language="ZH_HANS",
                    )
                    if values:
                        _cache_dict_values(attr_id, new_dc, new_type, values, language="ZH_HANS")
                if values and isinstance(values, list) and len(values) > 0:
                    new_dict_lookup[attr_id] = values
                elif isinstance(values, dict) and values.get("result"):
                    new_dict_lookup[attr_id] = values["result"]
        
        logger.info(f"   ✅ 新类目字典值: {len(new_dict_lookup)} 个字典属性")
        
        # Step 4': 重建 items（确定性构建，不调 LLM）
        new_items = _build_items_deterministically(
            draft=draft,
            description_category_id=new_dc,
            type_id=new_type,
            attr_list=new_attr_list,
            dict_lookup=new_dict_lookup,
            images=images,
            ozon_client_id=ozon_client_id,
            ozon_api_key=ozon_api_key,
            weight_grams=weight_grams,
            dimensions=dimensions,
            price_rub=price_rub,
            old_price_rub=old_price_rub,
            currency_code=currency_code,
            token=token,
        )
        if not new_items:
            logger.error("   ❌ 新类目重建 items 失败")
            return None
        
        # Step 5': 校验 + 补充必填属性
        new_items = _validate_and_enrich_items(
            items=new_items,
            attr_list=new_attr_list,
            dict_lookup=new_dict_lookup,
            images=images,
            ozon_client_id=ozon_client_id,
            ozon_api_key=ozon_api_key,
            description_category_id=new_dc,
            type_id=new_type,
            weight_grams=weight_grams,
            dimensions=dimensions,
            draft_title=draft.get("title", ""),
            supplier=draft.get("supplier", ""),
            ru_category_path=ru_category_path,
        )
        
        # Step 6': 提取 final_attributes
        new_final_attrs: list[dict[str, Any]] = []
        if new_items and new_items[0].get("attributes"):
            for attr in new_items[0]["attributes"]:
                for v in (attr.get("values") or []):
                    new_final_attrs.append({
                        "attribute_id": attr["id"],
                        "value": v.get("value", ""),
                        "dictionary_value_id": v.get("dictionary_value_id", 0),
                        "source": "llm",
                    })
        
        new_llm_name = str(new_items[0].get("name", ""))[:500] if new_items and new_items[0].get("name") else ""
        
        logger.info(f"   ✅ 新类目重建完成: {len(new_items)} items, {len(new_final_attrs)} 属性")
        return {
            "items": new_items,
            "final_attributes": new_final_attrs,
            "llm_name": new_llm_name,
            "attr_list": new_attr_list,
            "dict_lookup": new_dict_lookup,
        }
    except Exception as e:
        logger.error(f"   ❌ 新类目重建异常: {e}")
        return None


def _llm_match_category(
    title: str,
    description: str,
    attributes: dict[str, Any],
    candidates: list[dict[str, Any]],
    token: str,
) -> Optional[dict[str, Any]]:
    """LLM 从候选类目列表中选出最佳匹配"""
    try:
        workspace = os.getenv("APP_WORKSPACE_PATH", "/app")
        cfg_path = os.path.join(workspace, "config/category_match_v2_cfg.json")

        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        llm_cfg = cfg.get("config", {})
        model_id = llm_cfg.get("model", "deepseek-v4-flash")
        sp_template = cfg.get("sp", "")
        up_template = cfg.get("up", "")

        sp_tpl = Template(sp_template)
        up_tpl = Template(up_template)

        system_prompt = sp_tpl.render({})

        # 准备模板变量
        attr_flat = {}
        if attributes:
            for k, v in attributes.items():
                if isinstance(v, (str, int, float)):
                    attr_flat[k] = str(v)

        user_prompt = up_tpl.render({
            "title": title,
            "description": description[:500] if description else "",
            "attributes": attr_flat,
            "candidates": candidates,
        })

        resp = call_mxou_chat_api(
            token=token,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model_id,
            temperature=0.0,
            max_tokens=1024,
        ) or ""

        if not resp.strip():
            logger.error("LLM 类目匹配返回空")
            return None

        # 清理 JSON
        resp = resp.replace("```json", "").replace("```", "").strip()
        # 尝试提取 JSON 对象
        match = re.search(r'\{[^{}]*"description_category_id"[^{}]*\}', resp, re.DOTALL)
        if match:
            resp = match.group(0)

        result = json.loads(resp)
        logger.info(f"   LLM 类目匹配: {result.get('category_path', '')} (confidence={result.get('confidence', '?')})")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"LLM 类目匹配 JSON 解析失败: {e}, raw={resp[:200]}")
        return None
    except Exception as e:
        logger.error(f"LLM 类目匹配异常: {e}")
        return None


def _fetch_attribute_schema_from_ozon(
    ozon_client_id: str,
    ozon_api_key: str,
    description_category_id: int,
    type_id: int,
) -> list[dict[str, Any]]:
    """从 Ozon API 获取属性 Schema（回退路径）"""
    try:
        url = "https://api-seller.ozon.ru/v1/description-category/attribute"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "description_category_id": description_category_id,
            "type_id": type_id,
            "language": "ZH_HANS",  # 中文属性名用于匹配 1688 产品属性
        }
        resp = session.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", [])
        # ⚠️ v0.22 防御: Ozon 异常/限流时 result 可能非 list（会进 state.attributes_schema）
        if not isinstance(result, list):
            result = []
        logger.info(f"   Ozon API 返回 {len(result)} 个属性")
        return result
    except Exception as e:
        logger.error(f"Ozon 属性 API 调用失败: {e}")
        return []


def _build_items_deterministically(
    draft: dict[str, Any],
    description_category_id: int,
    type_id: int,
    attr_list: list[dict[str, Any]],
    dict_lookup: dict[int, list[dict[str, Any]]],
    images: list[str],
    ozon_client_id: str,
    ozon_api_key: str,
    weight_grams: int,
    dimensions: dict[str, int],
    price_rub: str,
    old_price_rub: str,
    currency_code: str,
    token: str,
) -> list[dict[str, Any]]:
    """
    确定性构建 /v3/product/import items JSON（不调用 LLM）。
    
    属性映射策略：
    1. 用 Ozon 属性的中文名匹配 1688 产品属性（draft.attributes）
    2. 字典属性：在 dict_lookup 中查找匹配的值，回退到 Ozon search API
    3. 自由文本属性：直接填入 1688 属性值（prepare 节点会用 LLM 翻译成俄语）
    4. 无匹配的必填属性：留空，由 _validate_and_enrich_items 填默认值
    """
    # ── 构建属性索引 ──
    attr_by_id: dict[int, dict[str, Any]] = {int(a["id"]): a for a in attr_list if "id" in a}
    
    # ── 1688 产品属性 ──
    product_attrs: dict[str, str] = {}
    raw_attrs = draft.get("attributes", {})
    if isinstance(raw_attrs, dict):
        for k, v in raw_attrs.items():
            product_attrs[str(k).strip()] = str(v).strip()
    
    # ── 属性名匹配辅助函数 ──
    # v0.32: 共享同义词表（原本只在 prepare _fill_optional_dict_attrs 用，assemble 0 映射）
    _synonyms = load_attr_synonyms()

    def _match_product_attr(ozon_attr_name: str) -> Optional[str]:
        """用 Ozon 属性中文名匹配 1688 产品属性值

        v0.32 词汇分歧修复：原 3 层（精确 / 包含 / 空格 split 分词）对无空格中文
        完全失效（.split() 只按空格切，中文整串一个 token），且同义词表不进本函数
        → 日志实证「属性映射数=0」。现改为：精确 → 包含 → jieba 分词子串重叠 → 同义词组。
        """
        name_lower = ozon_attr_name.lower().strip()
        # 精确匹配
        for pa_name, pa_val in product_attrs.items():
            if pa_name.lower() == name_lower:
                return pa_val
        # 包含匹配
        for pa_name, pa_val in product_attrs.items():
            if name_lower in pa_name.lower() or pa_name.lower() in name_lower:
                return pa_val
        # jieba 分词子串重叠匹配（v0.32 替代失效的 .split()）
        # 对「属性名」分词取 ≥2 字词，任一 token 互相包含即命中
        # （如「商品材质」vs「主要材质」共享 token「材质」——既非子串也非同义词组）。
        try:
            import jieba as _jieba
            ozon_tokens = [w for w in _jieba.cut(name_lower) if len(w) >= 2]
            if ozon_tokens:
                for pa_name, pa_val in product_attrs.items():
                    pa_tokens = [w for w in _jieba.cut(pa_name.lower()) if len(w) >= 2]
                    if pa_tokens and any(o in p or p in o for o in ozon_tokens for p in pa_tokens):
                        return pa_val
        except Exception:
            pass
        # 同义词组匹配（attr_synonyms.json）：同组双向包含才返回，防错误值
        matched_name = match_attr_name_synonym(name_lower, product_attrs.keys(), _synonyms)
        if matched_name is not None:
            return product_attrs[matched_name]
        return None
    
    def _find_dict_value(attr_id: int, product_value: str) -> tuple[int, str]:
        """在字典值中查找匹配，返回 (dictionary_value_id, value)"""
        if not product_value:
            return (0, "")
        values = dict_lookup.get(attr_id, [])
        if not values:
            return (0, product_value)
        # 精确匹配
        pv_lower = product_value.lower().strip()
        for v in values:
            if isinstance(v, dict):
                if str(v.get("value", "")).lower().strip() == pv_lower:
                    return (v.get("id", 0), str(v.get("value", "")))
        # 包含匹配
        for v in values:
            if isinstance(v, dict):
                vv = str(v.get("value", "")).lower().strip()
                if pv_lower in vv or vv in pv_lower:
                    return (v.get("id", 0), str(v.get("value", "")))
        return (0, product_value)

    # ⚠️ v0.29.x: _find_dict_value 命中时返回的 value 可能是 ZH_HANS 中文
    # (dict_lookup 缓存是中文)。上传前 value 字段必须去中文(字典属性以
    # dictionary_value_id 为权威, 中文 value 文本会被 Ozon 审核拒)。
    def _clean_dict_value(dict_id: int, value: str) -> str:
        import re as _re
        if dict_id > 0 and value and _re.search(r"[\u4e00-\u9fff]", str(value)):
            return ""
        return str(value or "")
    
    # ── 构建变体列表 ──
    variants = draft.get("variants", [])
    if not isinstance(variants, list):
        variants = []
    is_multi = len(variants) > 1
    
    variant_list: list[dict[str, Any]] = variants if is_multi else [{}]
    
    items: list[dict[str, Any]] = []
    
    for idx, variant in enumerate(variant_list):
        # 确定 offer_id
        if is_multi:
            offer_id = str(variant.get("sku_id", f"{draft.get('item_id', 'unknown')}_{idx}"))
            var_price = str(variant.get("price", price_rub))
            var_old_price = str(variant.get("original_price", old_price_rub))
        else:
            offer_id = str(draft.get("sku_id", draft.get("item_id", f"item_{idx}")))
            var_price = str(price_rub)
            var_old_price = str(old_price_rub)
        
        item: dict[str, Any] = {
            "description_category_id": description_category_id,
            "type_id": type_id,
            "offer_id": offer_id,
            "name": str(draft.get("title", ""))[:500],
            "price": var_price,
            "old_price": var_old_price,
            "currency_code": currency_code,
            "vat": "0",
            "dimension_unit": "mm",
            "weight_unit": "g",
            "depth": dimensions.get("length", 100),
            "width": dimensions.get("width", 100),
            "height": dimensions.get("height", 50),
            "weight": weight_grams,
            "images": (images or [])[:15],
            "primary_image": images[0] if images else "",
            "complex_attributes": [],
            "images360": [],
            "pdf_list": [],
            "barcode": "",
            "attributes": [],
        }
        
        # ── 构建属性列表 ──
        attrs: list[dict[str, Any]] = []
        for schema_attr in attr_list:
            attr_id = int(schema_attr.get("id", 0))
            if attr_id == 0:
                continue
            
            dict_id = schema_attr.get("dictionary_id", 0)
            attr_name_cn = schema_attr.get("name", "")
            
            # 跳过文本类属性（由 prepare_ozon_upload 或 _validate 处理）
            # 4191=描述, 4180=关键字, 9048=变体绑定名, 23171=hashtag
            if attr_id in (4191, 4180, 9048, 23171):
                continue

            # ⚠️ v0.16: 海关编码属性（ТН ВЭД 等）不填——由平台/税费系统自动关联，
            # 手动乱填会被拒或误导审核。按 ID 或属性名识别（RU/ZH/EN 关键词）。
            if is_customs_attr(attr_id, attr_name_cn):
                logger.info(f"   ⏭️ 海关编码属性[{attr_id}]({attr_name_cn})跳过（Ozon 自动关联）")
                continue
            
            # 品牌（85, 5076）— 留给 _validate_and_enrich_items 处理
            if attr_id in BRAND_ATTRIBUTE_IDS:
                continue
            # 原产国（4389）— 留给 _validate_and_enrich_items 处理
            if attr_id == COUNTRY_ATTR_ID:
                continue
            
            # 匹配 1688 产品属性
            product_value = _match_product_attr(attr_name_cn)
            
            if dict_id and dict_id > 0 and product_value:
                # 字典属性 → 查找 dictionary_value_id
                dict_val_id, dict_val = _find_dict_value(attr_id, product_value)
                # ⚠️ v0.29.x: 命中缓存(ZH_HANS)时 value 可能是中文 → 置空(dict_id 权威)
                dict_val = _clean_dict_value(dict_val_id, dict_val)
                if dict_val_id > 0:
                    attrs.append({
                        "complex_id": 0,
                        "id": attr_id,
                        "values": [{"dictionary_value_id": dict_val_id, "value": dict_val}],
                    })
                    logger.debug(f"   ✅ 属性映射: [{attr_id}] {attr_name_cn} = {product_value} → dict_id={dict_val_id}")
                else:
                    # ⚠️ v0.13: 字典值未匹配 → 不再写 dictionary_value_id=0 文本兜底！
                    # Ozon 字典属性只接受列表中的 dictionary_value_id，手填文本 → "属性值不正确，请从列表中选择一个属性值"
                    # 跳过该属性，由 _validate_and_enrich_items 用 /values/search 修正或补默认字典值
                    logger.warning(f"   ⚠️ 字典属性[{attr_id}] {attr_name_cn} = {product_value} 未匹配字典值，跳过（交由 validate 修正）")
            elif product_value:
                # 自由文本属性
                attrs.append({
                    "complex_id": 0,
                    "id": attr_id,
                    "values": [{"dictionary_value_id": 0, "value": product_value}],
                })
                logger.debug(f"   ✅ 文本属性: [{attr_id}] {attr_name_cn} = {product_value}")
            # 无匹配值 → 不添加，_validate_and_enrich_items 会补默认值
        
        item["attributes"] = attrs
        items.append(item)
    
    logger.info(f"   确定性构建完成: {len(items)} items, 属性映射数={sum(len(it['attributes']) for it in items)}")
    return items


def _validate_and_enrich_items(
    items: list[dict[str, Any]],
    attr_list: list[dict[str, Any]],
    dict_lookup: dict[int, list[dict[str, Any]]],
    images: list[str],
    ozon_client_id: str,
    ozon_api_key: str,
    description_category_id: int,
    type_id: int,
    weight_grams: int,
    dimensions: dict[str, int],
    draft_title: str = "",
    supplier: str = "",
    ru_category_path: str = "",
) -> list[dict[str, Any]]:
    """校验并补充 items 字段（属性补全、品牌修正、hashtag 生成等）"""

    # 构建属性索引
    attr_by_id: dict[int, dict[str, Any]] = {
        int(a["id"]): a for a in attr_list if "id" in a
    }
    required_attr_ids = {
        int(a["id"]) for a in attr_list
        if a.get("is_required", False) and "id" in a
    }

    validated_items: list[dict[str, Any]] = []

    for idx, item in enumerate(items):
        # === 基本字段补全 ===
        if not item.get("description_category_id"):
            item["description_category_id"] = description_category_id
        if not item.get("type_id"):
            item["type_id"] = type_id
        if not item.get("currency_code"):
            item["currency_code"] = "RUB"
        if not item.get("vat"):
            item["vat"] = "0"
        if not item.get("dimension_unit"):
            item["dimension_unit"] = "mm"
        if not item.get("weight_unit"):
            item["weight_unit"] = "g"
        if not item.get("depth") or item.get("depth") == 0:
            item["depth"] = dimensions.get("length", 100)
        if not item.get("width") or item.get("width") == 0:
            item["width"] = dimensions.get("width", 100)
        if not item.get("height") or item.get("height") == 0:
            item["height"] = dimensions.get("height", 50)
        if not item.get("weight") or item.get("weight") == 0:
            item["weight"] = weight_grams

        # 图片
        if not item.get("images"):
            item["images"] = images[:15]
        if not item.get("primary_image") and images:
            item["primary_image"] = images[0] if images else ""

        # 数组字段
        item.setdefault("complex_attributes", [])
        item.setdefault("images360", [])
        item.setdefault("pdf_list", [])
        item.setdefault("barcode", item.get("barcode", ""))

        # === 属性校验 ===
        attrs = item.get("attributes", [])
        seen_ids: set[int] = set()

        validated_attrs: list[dict[str, Any]] = []
        for attr in attrs:
            if not isinstance(attr, dict):
                continue

            attr_id = int(attr.get("id", 0))
            if attr_id == 0:
                continue
            if attr_id in seen_ids:
                logger.warning(f"   重复 attribute_id={attr_id}，跳过")
                continue
            seen_ids.add(attr_id)

            # 确保有 complex_id
            if "complex_id" not in attr:
                attr["complex_id"] = 0

            # 校验 values
            values = attr.get("values", [])
            if not isinstance(values, list):
                values = [values]
            if not values:
                values = [{"dictionary_value_id": 0, "value": ""}]

            validated_values = []
            for v in values:
                if not isinstance(v, dict):
                    continue
                dict_val_id = v.get("dictionary_value_id", 0)
                value = v.get("value", "")

                # 字典属性校验 dictionary_value_id
                schema_attr = attr_by_id.get(attr_id, {})
                dict_id = schema_attr.get("dictionary_id", 0)

                if dict_id and dict_id > 0 and dict_val_id == 0:
                    # 尝试从 dict_lookup 中查找匹配
                    dict_vals = dict_lookup.get(attr_id, [])
                    if isinstance(dict_vals, list):
                        for dv in dict_vals:
                            if isinstance(dv, dict) and dv.get("value", "").lower() == str(value).lower():
                                dict_val_id = dv.get("id", 0)
                                logger.info(f"   ✅ 修正 dictionary_value_id: attr={attr_id}, value='{value}' → id={dict_val_id}")
                                break
                    # ✅ v0.9.0: 精确匹配失败 → /values/search API 模糊搜索
                    if dict_val_id == 0 and value and len(str(value).strip()) >= 2:
                        try:
                            url = "https://api-seller.ozon.ru/v1/description-category/attribute/values/search"
                            headers = {
                                "Client-Id": ozon_client_id,
                                "Api-Key": ozon_api_key,
                                "Content-Type": "application/json",
                            }
                            payload = {
                                "attribute_id": attr_id,
                                "description_category_id": int(description_category_id),
                                "type_id": int(type_id),
                                "value": str(value).strip(),
                                "limit": 3,
                                # ⚠️ v0.29.x: 1688 中文属性值 → ZH_HANS 直查
                                # (旧代码无 language 参数=默认 RU → 中文搜不到 → 翻译再搜,
                                #  绕一大圈。dictionary_value_id 跨语言通用, 中文直查即命中)
                                "language": "ZH_HANS",
                            }
                            resp = session.post(url, json=payload, headers=headers, timeout=15)
                            if resp.status_code == 200:
                                search_data = resp.json()
                                search_result = search_data.get("result", [])
                                if search_result and len(search_result) > 0:
                                    dict_val_id = search_result[0].get("id", 0)
                                    matched_value = search_result[0].get("value", "")
                                    logger.info(f"   ✅ /values/search 匹配: attr={attr_id}, '{value}' → id={dict_val_id}, value='{matched_value}'")
                                else:
                                    # 中文搜不到 → 翻译后俄语再搜
                                    logger.info(f"   ⚠️ /values/search 无结果: attr={attr_id}, value='{value}'，尝试翻译后搜索")
                        except Exception as _search_e:
                            logger.warning(f"   ⚠️ /values/search 异常: attr={attr_id}, value='{value}': {_search_e}")

                # ⚠️ v0.13: 字典属性经 dict_lookup + /values/search 仍未命中 dict_id → 跳过该属性
                # 绝不写 dictionary_value_id=0 文本兜底（Ozon 只接受列表中的字典值，文本→"请从列表中选择一个属性值"）。
                # 若是必填属性，下方 missing_required 会走标题/属性名搜索 → 取字典第一个有效 dict_id 的完整路径。
                if dict_id and dict_id > 0 and not dict_val_id:
                    logger.warning(f"   ⚠️ 字典属性 attr={attr_id} 值='{value}' 无法匹配字典值，跳过（交由必填属性补全处理）")
                    continue

                validated_values.append({
                    "dictionary_value_id": int(dict_val_id) if dict_val_id else 0,
                    "value": str(value),
                })

            attr["values"] = validated_values
            validated_attrs.append(attr)

        # ✅ P0: attr=8229（Тип товара / 产品类型）主动填充
        # 用俄语类目路径的末级名称（如 "Секаторы"），这是 Ozon 审核的关键属性
        TYPE_ATTR_ID = 8229
        present_ids = {int(a["id"]) for a in validated_attrs if "id" in a}
        missing_required = required_attr_ids - present_ids
        type_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == TYPE_ATTR_ID), None)
        if not type_attr and ru_category_path:
            type_name = ru_category_path.split(">")[-1].strip()
            if type_name:
                # 查找 8229 的 schema 确认是字典还是文本属性
                schema_8229 = attr_by_id.get(TYPE_ATTR_ID, {})
                if schema_8229.get("dictionary_id", 0) > 0:
                    # ⚠️ v0.29.x 修复: 8229(类型)的字典值 id == type_id 本身
                    # (实测手持风扇 148495146 / 杀虫剂 99385)。优先按 type_id
                    # 从字典值里精确匹配, 失败才退回类目名搜索。
                    dict_vals = dict_lookup.get(TYPE_ATTR_ID, [])
                    found = False
                    if isinstance(dict_vals, list):
                        for dv in dict_vals:
                            if isinstance(dv, dict) and int(dv.get("id") or 0) == int(type_id or 0):
                                # ⚠️ v0.30.0: dict_lookup 是 ZH_HANS 中文缓存，value 可能是中文
                                # （实测「垂钓诱饵」→ validate 拦截 8229 含中文）。
                                # dict_id 权威 → value 置空（与 _clean_dict_value 一致）。
                                _v8229 = dv.get("value", "")
                                if any('\u4e00' <= ch <= '\u9fff' for ch in str(_v8229)):
                                    _v8229 = ""
                                validated_attrs.append({
                                    "complex_id": 0, "id": TYPE_ATTR_ID,
                                    "values": [{"dictionary_value_id": int(type_id), "value": _v8229}],
                                })
                                found = True
                                logger.info(f"   🎯 attr 8229 按 type_id 匹配: {type_id} → {dv.get('value', '')}{' [中文已置空]' if _v8229 != dv.get('value', '') else ''}")
                                break
                    if not found:
                        # 字典属性：按类目名匹配字典值(type_name 俄语 vs ZH_HANS 中文,
                        # 精确/包含通常失败, 走 API 搜索兜底)
                        # ⚠️ v0.29.x: 本地匹配(ZH_HANS)对俄语 type_name 必然失败,
                        # 直接走下方 API /values/search(RU) 搜索, 保留此段仅作兜底。
                        if isinstance(dict_vals, list):
                            for dv in dict_vals:
                                if isinstance(dv, dict) and dv.get("value", "").lower() == type_name.lower():
                                    validated_attrs.append({
                                        "complex_id": 0, "id": TYPE_ATTR_ID,
                                        "values": [{"dictionary_value_id": dv["id"], "value": dv["value"]}],
                                    })
                                    found = True
                                    logger.info(f"   🎯 attr 8229 精确匹配: {type_name} (dict_id={dv['id']})")
                                    break
                        if not found and isinstance(dict_vals, list) and dict_vals:
                            # 模糊匹配
                            for dv in dict_vals:
                                if isinstance(dv, dict) and (type_name.lower() in dv.get("value", "").lower() or dv.get("value", "").lower() in type_name.lower()):
                                    validated_attrs.append({
                                        "complex_id": 0, "id": TYPE_ATTR_ID,
                                        "values": [{"dictionary_value_id": dv["id"], "value": dv["value"]}],
                                    })
                                    found = True
                                    logger.info(f"   🎯 attr 8229 模糊匹配: {type_name} → {dv['value']}")
                                    break
                    if not found:
                        # ⚠️ v0.29.x 修复: dict_lookup 是 ZH_HANS 中文值, type_name 是俄语
                        # (如 "Секаторы") → 本地匹配必然失败(286 次缺失根因)。
                        # 用官方 API /values/search(language=RU) 搜 type_name,
                        # dictionary_value_id 跨语言通用, 拿到的 id 直接可用。
                        try:
                            from utils.ozon_client import ozon_post
                            _resp = ozon_post(
                                client_id=ozon_client_id,
                                api_key=ozon_api_key,
                                endpoint="/v1/description-category/attribute/values/search",
                                body={
                                    "attribute_id": TYPE_ATTR_ID,
                                    "description_category_id": int(description_category_id),
                                    "type_id": int(type_id),
                                    "value": type_name,
                                    "limit": 5,
                                },
                                language="RU",
                            )
                            _results = _resp.get("result", [])
                            if _results:
                                first = _results[0]
                                validated_attrs.append({
                                    "complex_id": 0, "id": TYPE_ATTR_ID,
                                    "values": [{"dictionary_value_id": first.get("id", 0), "value": first.get("value", "")}],
                                })
                                found = True
                                logger.info(f"   🎯 attr 8229 API搜索匹配: {type_name} → {first.get('value', '')} (dict_id={first.get('id')})")
                        except Exception as _api_e:
                            logger.debug(f"   attr 8229 API搜索失败: {_api_e}")
                    if not found:
                        logger.warning(f"   ⚠️ attr 8229 在字典中未找到 '{type_name}'，跳过（交由 Ozon 验证）")
                else:
                    # 自由文本属性：直接用俄语类目末级
                    validated_attrs.append({
                        "complex_id": 0, "id": TYPE_ATTR_ID,
                        "values": [{"dictionary_value_id": 0, "value": type_name}],
                    })
                    logger.info(f"   🎯 attr 8229 文本填充: {type_name}")
            elif TYPE_ATTR_ID in missing_required:
                missing_required.discard(TYPE_ATTR_ID)  # 已处理过

        # 特殊属性的已知默认值（常见必填属性）
        # ⚠️ v0.13: 4958(Назначение/用途) 和 9163(Пол/性别) 是字典属性（dictionary_id>0），
        # 绝不能设文本默认值——Ozon 只接受列表中的 dictionary_value_id，文本→"请从列表中选择一个属性值"。
        # 它们由上方字典匹配路径处理（标题/属性名搜索 → 取第一个有效 dict_id），这里不设 default。
        KNOWN_DEFAULTS: dict[int, str] = {
            8205: "730",              # Срок годности в днях（保质期天数）— 2年
            8962: "1",                # Количество предметов（件数）
            8292: "0",                # Объединить на одной карточке（合并卡牌）— 0=不合并
            # 9782: 字典属性（Класс опасности товара），值从 Ozon API 字典获取，不设 default
            # 23487: 自由文本属性，用 draft.supplier 填充，不设默认值
            # 4958: 字典属性（Назначение），不设 default — 走字典匹配路径
            # 9163: 字典属性（Пол），不设 default — 走字典匹配路径
        }

        for missing_id in sorted(missing_required):
            schema_attr = attr_by_id.get(missing_id, {})
            if not schema_attr:
                continue

            # 品牌(85/31/5076) 不走字典兜底——恒为「无品牌」，由下方 BRAND_ATTRIBUTE_IDS
            # 强制段统一补充，避免无谓的 Ozon 字典拉取 + 误导性「无法获取字典值」ERROR
            if missing_id in BRAND_ATTRIBUTE_IDS:
                continue

            attr_name = schema_attr.get("name", "?")
            dict_id = schema_attr.get("dictionary_id", 0)

            # ⚠️ v0.16: 海关编码属性必填也跳过——绝不"标题搜索/取字典第一个值"乱填 HS code
            if is_customs_attr(missing_id, attr_name):
                logger.info(f"   ⏭️ 必填海关编码属性[{missing_id}]({attr_name})跳过（Ozon 自动关联，不手动填）")
                continue

            new_attr: dict[str, Any] = {
                "complex_id": 0,
                "id": missing_id,
                "values": [],
            }

            # ✅ 特殊处理：attr=23487（Производитель/制造商）用 supplier 填充
            if missing_id == 23487:
                if supplier:
                    new_attr["values"] = [{"dictionary_value_id": 0, "value": supplier[:50]}]
                    validated_attrs.append(new_attr)
                    logger.info(f"   ✅ 制造商 attr=23487 使用供应商: {supplier[:30]}")
                else:
                    logger.warning(f"   ⚠️ 制造商 attr=23487 无供应商数据，跳过")
                continue

            if dict_id and dict_id > 0:
                # 字典属性 → 优先用 Ozon API 搜索匹配值
                dict_vals = dict_lookup.get(missing_id, [])
                matched = False

                # 如果缓存无值，从 Ozon API 获取
                # ⚠️ v0.29.x: 9782(危险等级)强制 RU 拉取——ZH_HANS 值匹配"非危险"
                # 不可靠(中文表述多样), 必填属性必须用 RU 官方值确认安全默认。
                if missing_id in HAZARD_DICT_ATTR_IDS or not isinstance(dict_vals, list) or not dict_vals:
                    try:
                        from utils.ozon_client import ozon_post
                        _fetch_resp = ozon_post(
                            client_id=ozon_client_id,
                            api_key=ozon_api_key,
                            endpoint="/v1/description-category/attribute/values",
                            body={
                                "attribute_id": missing_id,
                                "description_category_id": int(description_category_id),
                                "type_id": int(type_id),
                                "language": "RU",
                                "limit": 100,
                                "last_value_id": 0,
                            },
                        )
                        _fetched = _fetch_resp.get("result", [])
                        if _fetched:
                            dict_vals = _fetched
                            logger.info(f"   📡 API 获取字典值: attr={missing_id}, {len(_fetched)}条")
                            # 写入 PG 缓存（RU 语言），供后续相同类目的产品复用
                            try:
                                _cache_dict_values(missing_id, int(description_category_id), int(type_id), _fetched, language="RU")
                            except Exception as _ce:
                                logger.debug(f"   RU 字典缓存写入跳过 attr={missing_id}: {_ce}")
                    except Exception as _fe:
                        logger.debug(f"   API 获取字典值失败 attr={missing_id}: {_fe}")

                # 尝试用产品标题搜索字典值（比取第一个更准确）
                if draft_title and isinstance(dict_vals, list) and dict_vals:
                    try:
                        from utils.ozon_client import ozon_post
                        search_resp = ozon_post(
                            client_id=ozon_client_id,
                            api_key=ozon_api_key,
                            endpoint="/v1/description-category/attribute/values/search",
                            body={
                                "attribute_id": missing_id,
                                "description_category_id": int(description_category_id),
                                "type_id": int(type_id),
                                "value": draft_title[:50],
                                "limit": 5,
                                # ⚠️ v0.29.x: draft_title 是 1688 中文标题 → ZH_HANS 直查
                                # (旧代码 language=RU → 中文搜不到)
                            },
                            language="ZH_HANS",
                        )
                        search_results = search_resp.get("result", [])
                        if search_results:
                            first = search_results[0]
                            new_attr["values"] = [{
                                "dictionary_value_id": first.get("id", 0),
                                "value": str(first.get("value", "")),
                            }]
                            matched = True
                            logger.info(f"   ✅ 必填字典属性{missing_id}({attr_name}) API搜索匹配: {first.get('value', '')}")
                    except Exception as e:
                        logger.debug(f"   字典搜索失败(attr={missing_id}): {e}")

                if not matched:
                    # 回退1：用属性名（俄语）搜索字典值
                    # ⚠️ v0.29.x: attr_name 来自属性 schema(ZH_HANS 中文名, 如「材质」)
                    # → 必须 ZH_HANS 查询; 旧代码 language=RU 中文搜不到必然失败。
                    if not matched and attr_name:
                        try:
                            from utils.ozon_client import ozon_post
                            _name_search = ozon_post(
                                client_id=ozon_client_id,
                                api_key=ozon_api_key,
                                endpoint="/v1/description-category/attribute/values/search",
                                body={
                                    "attribute_id": missing_id,
                                    "description_category_id": int(description_category_id),
                                    "type_id": int(type_id),
                                    "value": attr_name[:30],
                                    "limit": 5,
                                },
                                language="ZH_HANS",
                            )
                            _name_results = _name_search.get("result", [])
                            if _name_results:
                                first = _name_results[0]
                                new_attr["values"] = [{
                                    "dictionary_value_id": first.get("id", 0),
                                    "value": str(first.get("value", "")),
                                }]
                                matched = True
                                logger.info(f"   ✅ 必填字典属性{missing_id}({attr_name}) 属性名搜索匹配: {first.get('value', '')}")
                        except Exception as _ne:
                            logger.debug(f"   属性名搜索失败(attr={missing_id}): {_ne}")

                    # 回退2（v0.21）：危险属性只挑「非危险」安全默认；其他属性仅唯一值才填
                    # 不再"取第一个字典值"——9782 曾因此被填成"爆炸物 Category 1"（BR_hazard_class1）
                    if not matched:
                        fallback = pick_dict_fallback_value(missing_id, attr_name, dict_vals)
                        if fallback:
                            new_attr["values"] = [{
                                "dictionary_value_id": fallback[0],
                                "value": fallback[1],
                            }]
                            matched = True
                            logger.info(f"   ✅ 必填字典属性{missing_id}({attr_name}) 安全兜底: {fallback[1]}")

                    # 回退3：仍然无值 → 记录警告，不写入空值（让Ozon跳过该属性）
                    if not matched:
                        logger.error(f"   ❌ 必填字典属性{missing_id}({attr_name}) 无法获取任何字典值，跳过写入空值")
                        continue  # 不添加空值属性，避免触发 error_attribute_values_empty
            else:
                # 自由文本属性 → 用已知默认值或留空
                default_val = KNOWN_DEFAULTS.get(missing_id, "")
                # ⚠️ v0.16: 无默认值的必填自由文本 → 跳过不写空串（空串上传触发
                # error_attribute_values_empty；宁可交给 validation_retry_loop 靶向修）
                if not default_val:
                    logger.warning(f"   ⚠️ 必填文本属性{missing_id}({attr_name}) 无默认值，跳过写入空值（交由 retry 靶向修）")
                    continue
                new_attr["values"] = [{"dictionary_value_id": 0, "value": default_val}]
                logger.info(f"   ✅ 必填文本属性{missing_id}({attr_name}) 使用默认值: {default_val}")

            validated_attrs.append(new_attr)
            logger.warning(f"   ⚠️ 补充缺失必填属性: id={missing_id} ({attr_name})")

        # === 特殊属性修正 ===
        # 品牌（85, 5076）— 无条件强制为"无品牌"
        for brand_id in BRAND_ATTRIBUTE_IDS:
            brand_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == brand_id), None)
            if brand_attr:
                values = brand_attr.get("values", [])
                for v in values:
                    old_val = v.get("value", "")
                    v["dictionary_value_id"] = NO_BRAND_DICT_ID
                    v["value"] = NO_BRAND_VALUE
                    if old_val and old_val != NO_BRAND_VALUE:
                        logger.info(f"   ✅ 品牌 attribute_id={brand_id} '{old_val}' → 'Нет бренда'")
            else:
                # 品牌属性不存在，补充为"无品牌"
                validated_attrs.append({
                    "complex_id": 0,
                    "id": brand_id,
                    "values": [{"dictionary_value_id": NO_BRAND_DICT_ID, "value": NO_BRAND_VALUE}],
                })
                logger.info(f"   ✅ 补充品牌 attribute_id={brand_id} = 'Нет бренда'")

        # 原产国（4389）
        country_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == COUNTRY_ATTR_ID), None)
        if country_attr:
            values = country_attr.get("values", [])
            for v in values:
                if v.get("dictionary_value_id", 0) == 0:
                    v["dictionary_value_id"] = CHINA_DICT_ID
                    v["value"] = CHINA_VALUE
        else:
            # 4389 是很多类目的必填属性，如果缺失则补充
            validated_attrs.append({
                "complex_id": 0,
                "id": COUNTRY_ATTR_ID,
                "values": [{"dictionary_value_id": CHINA_DICT_ID, "value": CHINA_VALUE}],
            })

        # Hashtag #23171: 生成俄语标签（不能是品牌名！）
        hashtag_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == FORCE_ATTR_23171), None)
        if hashtag_attr:
            values = hashtag_attr.get("values", [])
            for v in values:
                val = str(v.get("value", ""))
                # 如果是品牌值 "Нет бренда" 或无意义值，替换为生成的 hashtag
                if val == NO_BRAND_VALUE or val == "" or not val.startswith("#"):
                    new_tags = _generate_hashtags(item.get("name", ""))
                    v["value"] = new_tags
                    v["dictionary_value_id"] = 0  # 23171 是自由文本
                    logger.info(f"   ✅ hashtag #23171 修正为: {new_tags}")
        elif FORCE_ATTR_23171 in {int(a.get("id", 0)) for a in attr_list}:
            # Schema 中有 23171 但 LLM 没有生成，补充
            new_tags = _generate_hashtags(item.get("name", ""))
            validated_attrs.append({
                "complex_id": 0,
                "id": FORCE_ATTR_23171,
                "values": [{"dictionary_value_id": 0, "value": new_tags}],
            })
            logger.info(f"   ✅ hashtag #23171 补充生成: {new_tags}")

        # ✅ 可选字典属性补充：提升属性覆盖率（影响Ozon产品评分）
        # ⚠️ v0.13.1: 不再"取字典第一个值"盲补！字典多值时第一个值语义随机
        # （如"风格/用途"被填了与产品无关的值 → Ozon 报"属性值不正确，请从列表中选择"）。
        # ⚠️ v0.16: 增强——多值属性先按产品标题匹配（本地 ZH_HANS 字典值标题词包含匹配，
        # 仅唯一命中才补；再 Ozon /values/search RU 官方匹配兜底）。匹配不到仍跳过（宁缺毋滥）。
        # 该可选属性若 1688 有匹配值，_build_items_deterministically 已填；此处只兜"字典唯一值"+标题命中场景。
        present_after = {int(a["id"]) for a in validated_attrs if "id" in a}
        optional_dict_attrs = [
            a for a in attr_list
            if a.get("id") and not a.get("is_required")
            and a.get("dictionary_id", 0) > 0
            and int(a["id"]) not in present_after
            and int(a["id"]) not in (23171, 23536)  # 跳过hashtag和标记码
            and not is_customs_attr(int(a["id"]), a.get("name", ""))  # ⚠️ v0.16: 跳过海关编码属性
        ]
        filled_optional = 0
        skipped_optional = 0
        for opt_attr in optional_dict_attrs[:10]:  # 最多补10个
            opt_id = int(opt_attr["id"])
            opt_name = opt_attr.get("name", "?")
            dict_vals = dict_lookup.get(opt_id, [])
            match_val: Optional[dict[str, Any]] = None

            if isinstance(dict_vals, list) and len(dict_vals) == 1:
                # 字典只有一个可选值 → 补充（值确定，不会语义错误）
                only = dict_vals[0]
                if isinstance(only, dict) and only.get("id"):
                    match_val = only
            elif isinstance(dict_vals, list) and len(dict_vals) > 1:
                # 字典多值且无 1688 匹配 → 按产品标题匹配（v0.16 增强，替代纯跳过）
                # ① 本地：标题词在 ZH_HANS 字典值里包含匹配，仅当恰好 1 个值命中才补
                if draft_title:
                    title_words = [
                        w for w in re.split(r"[\s,，、/|()（）\[\]·\-]+", draft_title)
                        if w and len(w) >= 2
                    ]
                    hits = []
                    for v in dict_vals:
                        if not isinstance(v, dict):
                            continue
                        vv = str(v.get("value", ""))
                        if any(w in vv for w in title_words):
                            hits.append(v)
                    if len(hits) == 1:
                        match_val = hits[0]
                # ② 本地未命中 → Ozon /values/search（RU）官方匹配兜底
                if match_val is None:
                    try:
                        from utils.ozon_client import ozon_post
                        _search = ozon_post(
                            client_id=ozon_client_id,
                            api_key=ozon_api_key,
                            endpoint="/v1/description-category/attribute/values/search",
                            body={
                                "attribute_id": opt_id,
                                "description_category_id": int(description_category_id),
                                "type_id": int(type_id),
                                "value": (draft_title[:50] if draft_title else opt_name)[:50],
                                "limit": 5,
                                # ⚠️ v0.29.x: 搜索词是 1688 中文标题 → ZH_HANS 直查
                            },
                            language="ZH_HANS",
                        )
                        _results = _search.get("result", [])
                        if _results:
                            match_val = _results[0]
                    except Exception as e:
                        logger.debug(f"   可选字典搜索失败(attr={opt_id}): {e}")

            if match_val is not None and match_val.get("id"):
                validated_attrs.append({
                    "complex_id": 0, "id": opt_id,
                    "values": [{"dictionary_value_id": match_val["id"], "value": str(match_val.get("value", ""))}],
                })
                filled_optional += 1
            else:
                # 字典多值且无匹配 → 跳过，避免填语义错误的值被 Ozon 拒绝
                skipped_optional += 1
                logger.debug(f"   ⚠️ 可选字典属性[{opt_id}]({opt_name}) 无匹配值，跳过盲补")
        if filled_optional:
            logger.info(f"   📊 补充可选字典属性: {filled_optional}个（唯一值/标题命中）")
        if skipped_optional:
            logger.info(f"   📊 跳过可选字典属性盲补: {skipped_optional}个（无匹配，避免错误值）")

        # 9048（变体绑定名）= item_id，与 prepare_ozon_upload_node 逻辑一致
        if FORCE_ATTR_9048 not in present_ids and FORCE_ATTR_9048 not in {int(a["id"]) for a in validated_attrs}:
            item_id_val = item.get("offer_id", "unknown")
            validated_attrs.append({
                "complex_id": 0,
                "id": FORCE_ATTR_9048,
                "values": [{"dictionary_value_id": 0, "value": item_id_val}],
            })

        item["attributes"] = validated_attrs
        validated_items.append(item)

    return validated_items


def _fetch_category_tree_from_ozon(
    ozon_client_id: str,
    ozon_api_key: str,
) -> list[dict[str, Any]] | None:
    """从 Ozon API 获取类目树并返回原始数据"""
    try:
        url = "https://api-seller.ozon.ru/v1/description-category/tree"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json",
        }
        payload = {"language": "ZH_HANS"}
        resp = session.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # 返回 result 列表（保持与 category_cache 存储格式一致）
        result = data.get("result", [])
        logger.info(f"✅ Ozon API 返回类目树: {len(result)} 个顶层类目")
        return result
    except Exception as e:
        logger.error(f"❌ Ozon 类目树 API 调用失败: {e}")
        return None


# ==================== Dictionary Values Helpers ====================

def _fetch_dict_values_from_ozon(
    ozon_client_id: str,
    ozon_api_key: str,
    description_category_id: int,
    type_id: int,
    attribute_id: int,
    language: str = "ZH_HANS",
) -> list[dict[str, Any]] | None:
    """从 Ozon API 获取属性的字典值（按指定语言，分页拉全）"""
    try:
        url = "https://api-seller.ozon.ru/v1/description-category/attribute/values"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json",
        }
        # ⚠️ v0.13: limit 100 → 5000 + last_value_id 分页拉全
        # 大字典（如颜色 1494 条）只取前 100 会导致目标值匹配不到 → 文本兜底 → "请从列表中选择一个属性值"
        result: list[dict[str, Any]] = []
        last_value_id: int = 0
        while True:
            payload = {
                "attribute_id": attribute_id,
                "description_category_id": description_category_id,
                "type_id": type_id,
                "language": language,  # 中文字典值用于匹配 1688 属性值（dictionary_value_id 跨语言一致）
                "limit": 5000,
                "last_value_id": last_value_id,
            }
            resp = session.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            page = data.get("result", [])
            if not page:
                break
            result.extend(page)
            if len(page) < 5000:
                break
            # 分页游标：Ozon 用 last_value_id 返回下一页
            last_value_id = int(page[-1].get("id", last_value_id))
            if last_value_id == payload["last_value_id"]:  # 防死循环
                break
        logger.info(f"   ✅ Ozon API 返回 attr={attribute_id} 字典值: {len(result)} 条（分页拉全）")
        return result
    except Exception as e:
        logger.warning(f"   ⚠️ Ozon API 字典值 attr={attribute_id} 失败: {e}")
        return None


def _cache_dict_values(
    attribute_id: int,
    description_category_id: int,
    type_id: int,
    values: list[dict[str, Any]],
    language: str = "ZH_HANS",
):
    """将字典值写入 PG 缓存（按语言分别缓存）"""
    try:
        from utils.local_db_manager import LocalDBManager
        local_db = LocalDBManager()
        local_db.set_dictionary_value_cache(
            attribute_id=attribute_id,
            description_category_id=description_category_id,
            type_id=type_id,
            values_data=values,
            language=language,  # fetch 什么语言就 cache 什么语言
            expires_in=86400,
        )
        logger.info(f"   ✅ 字典值缓存写入成功: attr={attribute_id}, {len(values)} 条")
    except Exception as e:
        logger.warning(f"   ⚠️ 字典值缓存写入失败: {e}")


# ==================== Hashtag 生成 ====================

# 俄语关键词字典（按产品类型）
_HASHTAG_RU: dict[str, str] = {
    "секатор": "секатор сад садовый обрезка инструмент",
    "ножницы": "ножницы сад садовый обрезка инструмент",
    "грабли": "грабли сад садовый уборка листья",
    "лопата": "лопата сад садовый копка инструмент",
    "перчатки": "перчатки сад садовый защита работа",
    "шланг": "шланг сад полив вода",
    "лейка": "лейка сад полив вода",
    "горшок": "горшок цветы растения декор",
    "сеялка": "сеялка сад посадка семена",
    "удобрение": "удобрение сад растения подкормка",
}


def _generate_hashtags(name: str) -> str:
    """根据俄语标题生成 3-5 个 hashtag（不含品牌名）"""
    if not name:
        return "#товар"

    name_lower = name.lower()
    tags: list[str] = []

    # 从预定义字典匹配
    for keyword, tag_str in _HASHTAG_RU.items():
        if keyword in name_lower:
            tags = [f"#{t}" for t in tag_str.split()[:5]]
            break

    if not tags:
        # 从标题中提取俄语单词（排除短词和停用词）
        import re
        stopwords = {"для", "из", "и", "в", "на", "с", "по", "от", "не", "или", "а", "то", "как"}
        words = re.findall(r'[а-яё]{3,}', name_lower)
        meaningful = [w for w in words if w not in stopwords][:4]
        if meaningful:
            tags = [f"#{w}" for w in meaningful]
            tags.append("#товар")
        else:
            tags = ["#товар"]

    return " ".join(tags[:5])


def _llm_rank_categories(
    candidates: list[dict], keywords: str, draft: dict, state
) -> dict | None:
    """v4 LLM fallback：低置信度时让 LLM 从候选类目中选最佳匹配。
    增强：domain_hint 引导 + 1688类目面包屑 + 建议搜索词（候选都不合适时二次搜索）
    """
    try:
        from utils.mxou_api import call_mxou_chat_api

        product_title = (draft or {}).get("title", "") or getattr(state, "competitor_name", "") or ""
        product_attrs = (draft or {}).get("attributes", {}) or {}
        source_cat = (draft or {}).get("source_category", "") or ""
        attr_text = ", ".join(f"{k}={v}" for k, v in (product_attrs.items() if isinstance(product_attrs, dict) else []) if v)[:200]

        # v4: Load domain hints for LLM guidance
        domain_guidance = ""
        try:
            from utils.ozon_category_query import get_category_query
            hints = get_category_query()._load_domain_hints()
            if hints:
                kw_set = set((keywords or "").lower().split())
                triggered = []
                for dh in hints:
                    for kw in kw_set:
                        if kw in (dh.get("trigger_keywords") or []):
                            triggered.append(dh)
                            break
                if triggered:
                    guide_lines = []
                    for t in triggered[:3]:
                        g = f"  - 优先: {t['target_top_category']}"
                        if t.get("exclude_top_category"):
                            g += f" (排除: {t['exclude_top_category']})"
                        guide_lines.append(g)
                    domain_guidance = "领域规则:\n" + "\n".join(guide_lines) + "\n"
        except Exception:
            pass

        cand_text = "\n".join(
            f"{i+1}. {c.get('full_path', '')} (sim={c.get('similarity', 0):.2f})"
            for i, c in enumerate(candidates)
        )

        # v0.34: 结构化输出 JSON —— LLM 选候选编号；若候选都不合适, 输出 suggest_keywords
        # 供上层用建议词二次搜索正确类目（deepseek-v4-flash 推理模型 max_tokens 需足够大,
        # 10/200 都会被 reasoning_tokens 吃光输出为空 → fallback 恒失败）
        prompt = f"""Choose the best Ozon category for this product. Output JSON only.

产品: {product_title[:150]}
1688类目: {source_cat[:150]}
关键词: {keywords[:150]}
属性: {attr_text or 'N/A'}
{domain_guidance}
候选类目:
{cand_text}

返回 JSON:
{{"candidate_index": <1-{len(candidates)} 的整数, 候选中最匹配的; 若都不合适填 0>,
 "suggest_keywords": "<候选都不合适时, 给出 1-3 个俄语或中文搜索词用于重新搜索正确类目; 合适则空字符串>"}}"""

        result = call_mxou_chat_api(
            token=getattr(state, "token", ""),
            system_prompt="You are a product categorization expert. Follow domain rules if provided. Output valid JSON.",
            user_prompt=prompt,
            model="deepseek-v4-flash", temperature=0.0, max_tokens=4096,
        )
        if not result:
            return None
        # 解析 JSON（容忍首尾非 JSON 字符）
        import json as _json, re as _re2
        _m = _re2.search(r'\{.*\}', result, _re2.DOTALL)
        if not _m:
            return None
        try:
            parsed = _json.loads(_m.group(0))
        except Exception:
            return None
        idx = int(parsed.get("candidate_index", 0) or 0)
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]
        # 候选都不合适 → 返回带建议词的标记, 上层用 suggest_keywords 二次搜索
        suggest = str(parsed.get("suggest_keywords", "") or "").strip()
        if suggest:
            return {"suggest_keywords": suggest, "_llm_suggest": True}
    except Exception as e:
        logger.warning("LLM category ranking failed: %s", e)
    return None


# ═══════════════════════════════════════════════════════════
# v4: 分层类目匹配 (L0→L1→L2)
# ═══════════════════════════════════════════════════════════

def _match_category_layered(
    query, source_category: str, source_keywords: str, keywords: str,
    candidates: list, leaf_name: str,
    source_category_id=None,
) -> dict | None:
    """L0: 学习缓存查找 → 高置信命中直接返回；否则返回 None"""
    if not source_category or not leaf_name:
        return None
    try:
        from utils.local_db_manager import LocalDBManager as _LDB
        _ldb = _LDB()
        # ✅ v0.25 T1: 1688 类目数字 ID 优先（跨店铺稳定），其次末级名
        mappings = _ldb.get_category_mapping_by_source_id(int(source_category_id)) if source_category_id else None
        if not mappings:
            mappings = _ldb.get_category_mapping_by_leaf(leaf_name)
        logger.info(f"L0 lookup: leaf='{leaf_name}' → {len(mappings or [])} results")
        if not mappings:
            logger.info(f"L0 miss: no mapping for '{leaf_name}'")
            return None
        best = mappings[0]
        logger.info(f"L0 candidate: succ={best.get('success_count')} conf={best.get('confidence')} dc={best.get('description_category_id')}")
        if best.get("success_count", 0) < 1 or best.get("confidence", 0) < 0.6:
            logger.info(f"L0 skip: succ={best.get('success_count')} conf={best.get('confidence')} below threshold")
            return None
        verified = query.get_node(best["description_category_id"], best["type_id"])
        if not verified:
            logger.warning(f"L0 fail: dc={best['description_category_id']} type={best['type_id']} not found in PG")
            return None
        logger.info(f"🎯 L0命中: '{leaf_name}' → [{best['description_category_id']}/{best['type_id']}] "
                    f"success={best['success_count']} conf={best['confidence']:.2f}")
        return {
            "description_category_id": best["description_category_id"],
            "type_id": best["type_id"],
            "category_path": best.get("category_path_zh", "") or verified.get("full_path", ""),
            "confidence": "high",
            "reason": f"L0 learned (leaf='{leaf_name}', success={best['success_count']})",
        }
    except Exception as e:
        logger.debug(f"L0 skip: {e}")
        return None


def _apply_fingerprint_rerank(query, candidates: list, source_keywords: str, keywords: str) -> list:
    """L2: 指纹重排"""
    jieba_kw = [w.strip() for w in (source_keywords or keywords).split() if len(w.strip()) >= 2]
    if not jieba_kw:
        jieba_kw = [w.strip() for w in keywords.split() if len(w.strip()) >= 2]
    if jieba_kw and len(candidates) > 1:
        candidates = query.score_candidates_by_fingerprint(candidates, jieba_kw)
        if candidates:
            best = candidates[0]
            logger.info(f"🔢 L2指纹: top={best['node_name'][:30]} fp={best.get('fingerprint_score',0):.2f}")
    return candidates


def _log_match_attempt(state, title: str, source_category: str, keywords: str,
                       category_result: dict, match_layer: str, confidence: float,
                       candidates: list) -> None:
    """v4: 写入 category_match_log 审计表"""
    try:
        import json as _json, psycopg2 as _pg
        from storage.database.db import get_db_url as _gdu
        task_id = getattr(state, 'task_id', '') or ''
        if not task_id:
            logger.warning(f"match_log skip: task_id is empty (state type={type(state).__name__})")
            return
        conn = _pg.connect(_gdu())
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO category_match_log (task_id, source_title, source_category, source_keywords,
                    matched_description_category_id, matched_type_id, match_layer, confidence, candidates_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (
                task_id, (title or "")[:500], (source_category or "")[:500],
                [w.strip() for w in keywords.split() if len(w.strip()) >= 2][:20],
                int(category_result.get("description_category_id", 0)),
                int(category_result.get("type_id", 0)),
                match_layer, confidence,
                _json.dumps([{"dc": c.get("description_category_id"), "tp": c.get("type_id"),
                    "name": c.get("node_name", ""), "sim": c.get("similarity", 0),
                    "fp": c.get("fingerprint_score", 0), "path": c.get("full_path", "")
                } for c in (candidates or [])[:15]], ensure_ascii=False),
            ))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"match_log write failed (non-fatal): {e}")
