# fetch-back 回读节点（PR-0）— approved 后调 /v4/product/info/attributes 回读真实存储值
#
# 背景（对抗评审 A1/B2）：管线此前是「只写不读」——所有纠错（retry/learning）对标的是
# 我们发送了什么，不是 Ozon 存了什么。Ozon 会静默改写（9048 追加 offer_id、9782 被擦除、
# 字典值强制归一、attributes_with_defaults 自动填默认）。
#
# 本节点在审核通过后：
#   1. /v4/product/info/attributes 按 product_id 回读 Ozon 实际存储的属性
#   2. diff 发送值 vs 存储值（attribute_id + dictionary_value_id + value）
#   3. 遥测日志 attr.outcome：sent vs stored / 被擦除 / 被默认化
#   4. mismatch 且存储值是合法字典值 → 失效本地 dictionary_value_cache 条目（dict 漂移矫正）
#   5. 结果写 state.fetch_back_result（learning_record 依据它收紧学习门）
import logging
from typing import Any, Dict, List, Optional
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

# F-F01（2026-09-09 审计）：收敛 ozon_post（全局限流 + 429/5xx 重试 + 类型化错误）
from utils.ozon_client import ozon_post

# ✅ v0.81 内容评分闭环：唯一逻辑入口 utils/content_enrich（与 prepare 出口闸/
# 清扫脚本三方共用）
from utils.content_enrich import (
    RATING_THRESHOLD,
    build_enrich_update_body,
    enrichment_enabled,
    extract_improve_attrs,
    parse_rating_products,
    pick_product,
    rating_groups_summary,
)

logger = logging.getLogger(__name__)

from graphs.state import FetchBackInput, FetchBackOutput

_API = "/v4/product/info/attributes"
# 内容评级查询（2026-09-26 实机契约：POST body {"skus": ["<product_id>"]}）
_RATING_API = "/v1/product/rating-by-sku"

# 遥测命名空间（结构化日志，Sentry/日志分析用）：attr.outcome
_ATTR_OUTCOME = "attr.outcome"


def _call_fetch_back(
    ozon_client_id: str,
    ozon_api_key: str,
    product_id: str,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """调 /v4/product/info/attributes 回读商品属性。失败返回 []。"""
    payload = {
        "filter": {"product_id": [str(product_id)]},
        "limit": limit,
        "sort_by": "id",
        "sort_dir": "asc",
    }
    try:
        data = ozon_post(ozon_client_id, ozon_api_key, _API, payload, timeout=20)
        result = data.get("result") or []
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.warning("fetch_back /v4 异常: %s", e)
    return []


def _normalize_stored_attrs(stored_item: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """把 /v4 返回的 attributes[] 归一为 {attribute_id: {dict_id, value, values[]}}。"""
    out: Dict[int, Dict[str, Any]] = {}
    for a in stored_item.get("attributes") or []:
        if not isinstance(a, dict):
            continue
        try:
            aid = int(a.get("id") or 0)
        except (ValueError, TypeError):
            continue
        if not aid:
            continue
        vals = a.get("values") or []
        first = vals[0] if vals else {}
        out[aid] = {
            "dictionary_value_id": int(first.get("dictionary_value_id") or 0) if isinstance(first, dict) else 0,
            "value": str(first.get("value") or "") if isinstance(first, dict) else "",
            "values": vals,
        }
    return out


def _contains_cjk(text: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' for ch in str(text or ""))


def _content_rating_enhance(
    state: FetchBackInput,
    product_id: str,
    stored_item: Dict[str, Any],
) -> Dict[str, Any]:
    """v0.81 内容评分闭环（过审后 reactive 层，非致命）。

    过审拿到 product_id 后：rating-by-sku 复检 →
      - rating >= 90：只记录评级，结束；
      - rating < 90：取 groups[].improve_attributes → 可填集非空才动作——
        拉现卡 /v4 全量回显（stored_item 即本节点首查产物）+ 扁平 dimensions +
        price/old_price/currency + images 回显 + 新填属性（4191/11254 专用生成器 +
        fillable_improves 保守裁决）→ 一次 /v3/product/import UPDATE。

    防抖：state.content_rating 非空 = 本任务已复检过，跳过。任何异常 warning +
    审计块留痕，绝不 fail 任务（增强失败不影响已过审的卡）。被 Ozon 擦掉的值
    （erased_attribute_value）是警告不是失败——本步 fire-and-forget 不轮询 import/info。
    """
    audit: Dict[str, Any] = {
        "product_id": str(product_id),
        "threshold": RATING_THRESHOLD,
        "action": "skipped",
        "filled": [],
        "skipped": [],
        "media_gap": [],
    }
    if not enrichment_enabled():
        audit["reason"] = "env_gate_off"
        return audit

    # ① 评级复检
    resp = ozon_post(
        state.ozon_client_id, state.ozon_api_key,
        _RATING_API,
        {"skus": [str(product_id)]},
        timeout=20,
        language="RU",
    )
    products = parse_rating_products(resp)
    product = pick_product(products, product_id)
    if not product:
        audit["reason"] = "no_rating_data"
        return audit
    try:
        rating = float(product.get("rating") or 0)
    except (ValueError, TypeError):
        rating = 0.0
    audit["rating"] = rating
    audit["groups"] = rating_groups_summary(product)
    logger.info("⭐ 内容评级复检 product_id=%s rating=%.0f groups=%s", product_id, rating, audit["groups"])

    # ② 达标即收（防抖：fresh import 后评级每日重算，达标卡不再动）
    if rating >= RATING_THRESHOLD:
        audit["action"] = "above_threshold"
        return audit

    # ③ 可填裁决 + UPDATE 构造（唯一入口 build_enrich_update_body）
    improves = extract_improve_attrs(product)
    try:
        schema_by_id = {
            int(s.get("id") or 0): s
            for s in (state.attributes_schema or []) if isinstance(s, dict) and s.get("id")
        }
    except (ValueError, TypeError):
        schema_by_id = {}
    draft_attrs = {}
    if isinstance(state.draft, dict) and isinstance(state.draft.get("attributes"), dict):
        draft_attrs = state.draft["attributes"]
    pricing = state.pricing_info if isinstance(state.pricing_info, dict) else {}

    body, audit_partial = build_enrich_update_body(
        product_id,
        stored_item,
        improves,
        draft_attrs,
        schema_by_id,
        price=pricing.get("price"),
        old_price=pricing.get("old_price"),
        currency_code=str(pricing.get("currency_code") or "CNY"),
    )
    audit.update(audit_partial)
    if not body:
        audit.setdefault("reason", "nothing_to_fill")
        logger.info("⭐ 内容评级 %s 无可填缺口（%s），不动作", rating, audit.get("reason"))
        return audit

    # ④ 一次 UPDATE（fire-and-forget；erased_attribute_value 类是警告非失败）
    import_resp = ozon_post(
        state.ozon_client_id, state.ozon_api_key,
        "/v3/product/import", body, timeout=60,
    )
    audit["action"] = "updated"
    try:
        audit["import_task_id"] = str(((import_resp or {}).get("result") or {}).get("task_id") or "")
    except Exception:
        audit["import_task_id"] = ""
    logger.info(
        "⭐ 内容评级增强 UPDATE 已提交 product_id=%s rating=%.0f filled=%s skipped=%s media_gap=%s",
        product_id, rating, audit["filled"], audit["skipped"], audit["media_gap"],
    )
    return audit


def fetch_back_node(
    state: FetchBackInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> FetchBackOutput:
    """审核通过后回读 Ozon 存储属性，diff 发送值，输出遥测 + 缓存失效指令。"""
    product_id = state.product_id or ""
    if not product_id or str(product_id) in ("0", "None", ""):
        logger.info("fetch_back: 无 product_id，跳过回读")
        return FetchBackOutput(fetch_back_result={}, progress_counter=25)

    logger.info("🔍 fetch_back: 回读 product_id=%s 的 Ozon 存储属性", product_id)

    stored_items = _call_fetch_back(
        state.ozon_client_id, state.ozon_api_key, product_id,
    )
    if not stored_items:
        logger.warning("fetch_back: 回读无结果（商品可能未同步/已删除），跳过 diff")
        return FetchBackOutput(fetch_back_result={}, progress_counter=25)

    stored_item = stored_items[0]
    stored_attrs = _normalize_stored_attrs(stored_item)
    stored_with_defaults: set = {
        int(x) for x in (stored_item.get("attributes_with_defaults") or [])
        if isinstance(x, (int, str)) and str(x).isdigit()
    }

    # 发送侧 final_attributes（我们上传的属性）
    sent_attrs: Dict[int, Dict[str, Any]] = {}
    for a in state.final_attributes or []:
        if not isinstance(a, dict):
            continue
        try:
            aid = int(a.get("id") or a.get("attribute_id") or 0)
        except (ValueError, TypeError):
            continue
        if not aid:
            continue
        sent_attrs[aid] = {
            "dictionary_value_id": int(a.get("dictionary_value_id") or 0),
            "value": str(a.get("value") or ""),
        }

    mismatches: List[Dict[str, Any]] = []
    erased: List[int] = []
    for aid, sent in sent_attrs.items():
        stored = stored_attrs.get(aid)
        if stored is None:
            # 我们发了但 Ozon 没存（被擦除）
            erased.append(aid)
            logger.info(
                "attr.outcome erased attr=%s sent_dict=%s sent_val=%r stored=None",
                aid, sent["dictionary_value_id"], sent["value"][:40],
                extra={"namespace": _ATTR_OUTCOME},
            )
            continue
        # 字典值 diff：dict_id 权威；dict_id 一致但 value 被 Ozon 改写（语言归一）不算错
        sent_dict = sent["dictionary_value_id"]
        stored_dict = stored["dictionary_value_id"]
        if sent_dict and stored_dict and sent_dict != stored_dict:
            mismatches.append({
                "attribute_id": aid,
                "sent_dictionary_value_id": sent_dict,
                "stored_dictionary_value_id": stored_dict,
                "sent_value": sent["value"][:60],
                "stored_value": stored["value"][:60],
            })
            logger.info(
                "attr.outcome mismatch attr=%s sent_dict=%s stored_dict=%s sent_val=%r stored_val=%r",
                aid, sent_dict, stored_dict, sent["value"][:60], stored["value"][:60],
                extra={"namespace": _ATTR_OUTCOME},
            )
        elif stored_dict and _contains_cjk(stored["value"]):
            # 存储值含中文（异常，正常 Ozon 会俄语化）→ 记录但不阻断
            logger.info(
                "attr.outcome cjk_stored attr=%s stored_val=%r",
                aid, stored["value"][:40],
                extra={"namespace": _ATTR_OUTCOME},
            )

    # attributes_with_defaults：Ozon 自动填了默认值的属性（学习门要排除它们）
    defaulted_by_ozon: List[int] = sorted(stored_with_defaults)

    result: Dict[str, Any] = {
        "product_id": product_id,
        "mismatches": mismatches,
        "erased": erased,
        "defaulted_by_ozon": defaulted_by_ozon,
        "stored_attr_count": len(stored_attrs),
        "sent_attr_count": len(sent_attrs),
        "stored_attrs": stored_attrs,  # 供 learning_record 判断「approved 且未被擦除」
    }

    if mismatches:
        logger.info(
            "fetch_back: %d 个属性 dict_id 漂移 → learning 按 fetch_back_corrected 修正",
            len(mismatches),
        )
    if erased:
        logger.info("fetch_back: %d 个属性被 Ozon 擦除（learning 门排除）: %s", len(erased), erased)
    if defaulted_by_ozon:
        logger.info(
            "fetch_back: Ozon 自动填默认的属性 %d 个（learning 门排除）: %s",
            len(defaulted_by_ozon), defaulted_by_ozon,
        )

    # ✅ v0.81 内容评分闭环（复检闭环，非致命）：approved + product_id 在手 +
    # /v4 回显现成 → 评级复检，<90 且有可填缺口时一次全量回显 UPDATE。
    # 防抖：content_rating 非空 = 本任务已复检。任何异常只 warning 留痕，
    # 绝不影响任务成功（卡已过审）。
    content_rating_audit: Dict[str, Any] = dict(getattr(state, "content_rating", None) or {})
    if content_rating_audit:
        logger.info("fetch_back: 内容评级已复检过（防抖），跳过增强")
    else:
        try:
            content_rating_audit = _content_rating_enhance(state, product_id, stored_item)
        except Exception as exc:
            content_rating_audit = {
                "product_id": str(product_id),
                "action": "error",
                "reason": str(exc)[:200],
            }
            logger.warning("fetch_back: 内容评级增强异常（非致命，不影响任务）: %s", str(exc)[:300])

    result["content_rating"] = content_rating_audit

    return FetchBackOutput(
        fetch_back_result=result,
        content_rating=content_rating_audit,
        progress_counter=25,
    )
