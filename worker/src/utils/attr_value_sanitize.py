"""属性值数出口闸（v0.71）：按 Ozon schema 规范化每个属性的 values 数量。

背景（2026-09-08 gate 实证）：8229（Тип）schema 标 is_collection 但 Ozon 实际
限单值；prepare._fill_optional_dict_attrs 对 multivalue_ids 把全部模糊命中塞进
values → Ozon ATTRIBUTE_VALUE_COUNT_EXCEEDED 拒单。此前全库没有任何值数上限
检查——Ozon `/v1/description-category/attribute` 响应自带 `max_value_count`
字段（attribute_cache 原样存储），从未被读取（mcp__ozon__describe_method 核对，
2026-09）。

唯一入口纪律：凡把属性 values 发给 Ozon 的路径（prepare 载荷出口、retry flat
合并、retry attributes/update 重发）都必须先过 `cap_attribute_values`。
schema 未收录的属性不设限（宁勿误伤合法多值，如无上限声明的集合属性）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 8229（Тип/类型）契约：dictionary_value_id == type_id，恒单值——即使缓存行
# 缺 max_value_count（旧缓存 TTL 未过期）也强制 cap=1。
TYPE_ATTR_ID = 8229


def build_schema_index(schema: Any) -> Dict[int, Dict[str, Any]]:
    """schema 列表 → {attr_id: item}。非 dict/无 id 条目跳过。"""
    idx: Dict[int, Dict[str, Any]] = {}
    if not isinstance(schema, list):
        return idx
    for item in schema:
        if not isinstance(item, dict):
            continue
        try:
            aid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        idx[aid] = item
    return idx


def resolve_value_cap(schema_item: Optional[Dict[str, Any]], attr_id: int = 0) -> Optional[int]:
    """单属性值数上限。None = 不设限。

    - 8229 恒 1（契约单值，防旧缓存行缺字段）
    - max_value_count > 0 → 该值
    - is_collection 为真 → 不设限（Ozon 未声明上限的合法多值）
    - 其余（单值/字段缺失）→ 1
    """
    if attr_id == TYPE_ATTR_ID or (
        isinstance(schema_item, dict) and schema_item.get("id") == TYPE_ATTR_ID
    ):
        return 1
    if not isinstance(schema_item, dict):
        return None
    try:
        mvc = int(schema_item.get("max_value_count") or 0)
    except (TypeError, ValueError):
        mvc = 0
    if mvc > 0:
        return mvc
    if bool(schema_item.get("is_collection")):
        return None
    return 1


def _value_key(v: Dict[str, Any]) -> Tuple[Any, Any]:
    """values 行去重键：(dictionary_value_id, value 文本)。"""
    try:
        dvid = int(v.get("dictionary_value_id") or 0)
    except (TypeError, ValueError):
        dvid = 0
    return dvid, str(v.get("value") or "")


def cap_attribute_values(
    attrs: Any, schema: Any
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Ozon 格式属性列表（[{"id", "values": [...]}]）按 cap 截断 + 去重。

    返回 (attrs, marks)。marks 只含被裁/去重过的属性：
    [{"attr_id", "kept", "removed", "cap"}]。截断保前排顺序（上游精确/字典
    命中优先的语义由调用方保证）。输入非 list / 条目畸形原样保留。
    """
    if not isinstance(attrs, list):
        return attrs, []
    idx = build_schema_index(schema)
    marks: List[Dict[str, Any]] = []
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        try:
            aid = int(attr.get("id") or attr.get("attribute_id") or 0)
        except (TypeError, ValueError):
            continue
        if not aid:
            continue
        vals = attr.get("values")
        if not isinstance(vals, list) or len(vals) <= 1:
            continue
        cap = resolve_value_cap(idx.get(aid), aid)
        deduped: List[Dict[str, Any]] = []
        seen: set = set()
        for v in vals:
            if not isinstance(v, dict):
                deduped.append(v)
                continue
            k = _value_key(v)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(v)
        kept = deduped if cap is None else deduped[:cap]
        if len(kept) != len(vals):
            attr["values"] = kept
            marks.append({
                "attr_id": aid,
                "kept": len(kept),
                "removed": len(vals) - len(kept),
                "cap": cap if cap is not None else "dedupe",
            })
    return attrs, marks
