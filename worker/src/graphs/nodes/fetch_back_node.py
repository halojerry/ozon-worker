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

from utils.http_session import session

logger = logging.getLogger(__name__)

from graphs.state import FetchBackInput, FetchBackOutput

_API = "https://api-seller.ozon.ru/v4/product/info/attributes"

# 遥测命名空间（结构化日志，Sentry/日志分析用）：attr.outcome
_ATTR_OUTCOME = "attr.outcome"


def _call_fetch_back(
    ozon_client_id: str,
    ozon_api_key: str,
    product_id: str,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """调 /v4/product/info/attributes 回读商品属性。失败返回 []。"""
    headers = {
        "Client-Id": ozon_client_id,
        "Api-Key": ozon_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "filter": {"product_id": [str(product_id)]},
        "limit": limit,
        "sort_by": "id",
        "sort_dir": "asc",
    }
    try:
        resp = session.post(_API, headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            result = resp.json().get("result") or []
            return result if isinstance(result, list) else []
        logger.warning("fetch_back /v4 返回 %s: %s", resp.status_code, resp.text[:200])
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

    return FetchBackOutput(fetch_back_result=result, progress_counter=25)
