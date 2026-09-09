"""类目属性 schema 交互端懒加载服务（v0.71）。

v0.70 的「categories/attributes 缓存只读不回源 Ozon」红线修订：本地实证
7992 个 (dc,tp) 类目对 vs attribute_cache 12 行——只读缓存让「选类目必出
属性表单」在 99.8% 的类目上不可能成立（表单永远显示未预热）。

修订后语义与管线懒加载（assemble/retry v0.69 T3.3）完全同源：
- 缓存命中 → 直接返回（零 API）
- 未命中 → 用租户店铺凭证**按需拉一次** Ozon → 回写缓存（30d TTL）→ 返回
- 无凭证/拉取失败 → found=False + reason（前端提示降级，不破坏页面）

字典值按单属性按需（表单下拉打开时拉）：POST
/v1/description-category/attribute/values，limit=2000（契约上限）。
✅ v0.72 三桶策略（utils/dict_value_cache.py）：首页即 has_next 的巨型字典
（如品牌 85）ephemeral 不回写（下拉返回首页值）；小字典按 schema 的
category_dependent 路由全局/scoped 桶。

 Ozon 契约（mcp__ozon__describe_method 核对 2026-09）：
- GetAttributes: {description_category_id, type_id, language} → result[]
- GetAttributeValues: {attribute_id, dc, tp, limit≤2000, last_value_id?,
  language} → {result[], has_next}
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_VALUES_PAGE_LIMIT = 2000


def _normalize_values(raw: Any) -> list[dict]:
    """values 响应 → [{id, value}]（缓存形状与交互形状一致）。"""
    items = raw.get("result") if isinstance(raw, dict) else raw
    out: list[dict] = []
    for v in (items or []):
        if isinstance(v, dict) and v.get("id"):
            out.append({"id": v.get("id"), "value": v.get("value") or ""})
    return out


def get_attributes_with_lazy_fetch(
    dc: int, tp: int, client_id: str, api_key: str,
    language: str = "ZH_HANS",
) -> dict:
    """类目属性 schema：缓存优先 → 按需拉取回写。纯函数式返回，不抛异常。

    返回 {found, cached, fetched, schema, reason?}——schema 为属性 list
    （与 attribute_cache 存储形状一致）。
    """
    from utils.ozon_category_query import get_category_query
    try:
        cached = get_category_query().get_attribute_schema(dc, tp, language=language)
    except Exception as exc:
        return {"found": False, "cached": False, "fetched": False,
                "schema": None, "reason": f"cache_unavailable: {exc}"}
    if cached:
        items = cached.get("result") if isinstance(cached, dict) else cached
        return {"found": bool(items), "cached": True, "fetched": False,
                "schema": items or None}

    if not client_id or not api_key:
        return {"found": False, "cached": False, "fetched": False,
                "schema": None, "reason": "no_credential"}

    try:
        from utils.ozon_client import ozon_post
        resp = ozon_post(
            client_id, api_key,
            "/v1/description-category/attribute",
            {"description_category_id": int(dc), "type_id": int(tp),
             "language": language},
            timeout=20,
        )
    except Exception as exc:
        logger.warning("categories/attributes 按需拉取失败 dc=%s tp=%s: %s", dc, tp, exc)
        return {"found": False, "cached": False, "fetched": False,
                "schema": None, "reason": f"fetch_failed: {exc}"}

    attrs = resp.get("result", []) or []
    if not attrs:
        return {"found": False, "cached": False, "fetched": True,
                "schema": None, "reason": "empty_from_ozon"}

    # 回写 30d（管线懒加载同款；失败不阻断返回）
    try:
        from utils.local_db_manager import LocalDBManager
        LocalDBManager().set_attribute_cache(dc, tp, attrs, language=language)
        logger.info("✅ categories/attributes 按需拉取回写 (dc=%s tp=%s, %d 属性)",
                    dc, tp, len(attrs))
    except Exception as exc:
        logger.debug("schema 回写失败(非致命): %s", exc)
    return {"found": True, "cached": False, "fetched": True, "schema": attrs}


def get_attribute_values_with_lazy_fetch(
    attr_id: int, dc: int, tp: int, client_id: str, api_key: str,
    language: str = "ZH_HANS",
) -> dict:
    """单属性字典值：缓存优先 → 按需拉取回写。不抛异常。

    ✅ v0.72 三桶策略（utils/dict_value_cache.py）：
    - 首页即 has_next（>2000 值巨型字典，如品牌 85）→ ephemeral：**不回写**，
      返回首页值供下拉使用（运行时 value→id 精确查走 /values/search）；
    - 小字典按 schema 的 category_dependent 路由：false → 全局桶 (attr,0,0)
      一份；true → scoped (attr,dc,tp)。schema 本身走
      get_attributes_with_lazy_fetch（PG 优先）现取。

    返回 {found, cached, fetched, values, reason?}。
    """
    from utils.ozon_category_query import get_category_query
    try:
        cached = get_category_query().get_dictionary_values(
            attr_id, dc, tp, language=language)
    except Exception as exc:
        return {"found": False, "cached": False, "fetched": False,
                "values": None, "reason": f"cache_unavailable: {exc}"}
    if cached:
        vals = _normalize_values(cached)
        return {"found": bool(vals), "cached": True, "fetched": False,
                "values": vals or None}

    if not client_id or not api_key:
        return {"found": False, "cached": False, "fetched": False,
                "values": None, "reason": "no_credential"}

    try:
        from utils.ozon_client import ozon_post
        resp = ozon_post(
            client_id, api_key,
            "/v1/description-category/attribute/values",
            {
                "attribute_id": int(attr_id),
                "description_category_id": int(dc),
                "type_id": int(tp),
                "limit": _VALUES_PAGE_LIMIT,
                "language": language,
            },
            timeout=30,
        )
    except Exception as exc:
        logger.warning("字典值按需拉取失败 attr=%s dc=%s tp=%s: %s", attr_id, dc, tp, exc)
        return {"found": False, "cached": False, "fetched": False,
                "values": None, "reason": f"fetch_failed: {exc}"}

    all_vals = _normalize_values(resp)
    if not all_vals:
        return {"found": False, "cached": False, "fetched": True,
                "values": None, "reason": "empty_from_ozon"}

    # ✅ v0.72 首页即 has_next → 巨型字典 ephemeral：不回写（下拉用首页值足够，
    # 避免品牌类无底洞按类目复制进 PG——磁盘事故根因）
    if resp.get("has_next"):
        logger.info("⏭️ 字典 %s 值数超 %s（巨型），ephemeral 不回写（下拉返回首页 %s 值）",
                    attr_id, _VALUES_PAGE_LIMIT, len(all_vals))
        return {"found": True, "cached": False, "fetched": True,
                "values": all_vals, "reason": "ephemeral_dict"}

    # 小字典：按 schema 的 category_dependent 路由落桶
    _attr_row = None
    try:
        _schema_res = get_attributes_with_lazy_fetch(dc, tp, client_id, api_key, language)
        for _a in (_schema_res.get("schema") or []):
            if isinstance(_a, dict) and int(_a.get("id") or 0) == int(attr_id):
                _attr_row = _a
                break
    except Exception:
        pass  # schema 拿不到 → routed_set 缺省保守按 scoped

    try:
        from utils.dict_value_cache import routed_set
        bucket = routed_set(attr_id, all_vals, dc, tp,
                            attr_row=_attr_row, language=language)
        logger.info("✅ 字典值按需拉取回写 (attr=%s dc=%s tp=%s, %s 值, %s 桶)",
                    attr_id, dc, tp, len(all_vals), bucket)
    except Exception as exc:
        logger.debug("字典值回写失败(非致命): %s", exc)
    return {"found": True, "cached": False, "fetched": True, "values": all_vals}
