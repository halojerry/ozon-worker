"""R3a: 类目/佣金只读路由族（自 main.py 迁出）。

两个 router：
- ``router``（v1-relative，由 main ``v1.include_router`` 注册）：
    GET /api/v1/mappings/lookup            已学习类目映射查询（W11）
- ``root_router``（绝对路径，由 main ``app.include_router`` 注册）：
    GET /categories/search          + GET /api/v1/categories/search
    GET /categories/attributes      + GET /api/v1/categories/attributes
    GET /commissions/lookup         + GET /api/v1/commissions/lookup
  （categories/commissions 是 legacy 与 /api/v1 双挂，沿用原 main 的 @app.get 绝对路径形态）

依赖纪律（R3a）：鉴权走 api.deps_tenant.verify_bearer_from_request（租户解析
services.tenant_service）；类目树/凭证/schema/佣金依赖保持函数内懒加载原状；
禁止 ``from main import``。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
root_router = APIRouter()


@router.get("/mappings/lookup", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "found": True,
        "mappings": [{"dc": "91936", "tp": "91540", "confidence": 0.85}],
    }}}},
})
async def v1_mappings_lookup(request: Request):
    """类目映射查询（W11）：skill 端按关键词查已学习 Ozon 类目映射。

    query: keyword（1688 中文类目名）→ {found, mappings: [{dc, tp, confidence}]}
    复用 category_mapping_learn.lookup_mapping（精确 leaf/ID，成功数+置信门槛）；
    未命中时按 source_keywords 重叠兜底（ozon_category_query 同表同门槛）。
    category_mapping 表全局共享（无 tenant 隔离——类目映射是平台级知识，PRD §3.3）。
    """
    # B 租户 guard 二期：Bearer 提取→verify（无限流，对齐原内联序列）收敛单行；
    # 全局共享无租户过滤 → verify_bearer（不加 resolve_tenant，行为逐字等价）。
    from api.deps_tenant import verify_bearer_from_request
    verify_bearer_from_request(request, rate_limited=False)

    keyword = (request.query_params.get("keyword") or "").strip()
    if not keyword:
        return {"found": False, "mappings": []}

    from utils.category_mapping_learn import lookup_mapping, MIN_SUCCESS_COUNT, MIN_CONFIDENCE
    result = lookup_mapping(leaf_name=keyword)
    if result:
        return {"found": True, "mappings": [result]}

    from utils.ozon_category_query import OzonCategoryQuery
    rows = OzonCategoryQuery().get_category_mapping_by_keywords([keyword], min_overlap=1, top_k=10)
    mappings = []
    for r in rows:
        if (r.get("success_count") or 0) < MIN_SUCCESS_COUNT or (r.get("confidence") or 0) < MIN_CONFIDENCE:
            continue
        mappings.append({
            "dc": str(r["description_category_id"]),
            "tp": str(r["type_id"]),
            "confidence": float(r.get("confidence") or 0.7),
        })
    return {"found": bool(mappings), "mappings": mappings}


# ==================== 类目/属性只读端点（v0.70 采集箱手工改配） ====================
# GET /api/v1/categories/search?q=  与  GET /api/v1/categories/attributes?dc=&tp=[&attr_id=]
# webui 采集箱「类目选择器 + 属性表单」的数据源：用户在草稿上指定 dc/tp（写
# draft.ozon_category.source=manual）+ 属性值（draft.attributes），worker 侧按
# 权威直通上传（assemble `_is_skill_authoritative` manual 权威，test_manual_category_
# authority_v070 锁定）。两个端点全局只读（类目树/缓存表无租户数据），鉴权与
# analytics 读端点同源。
# ⚠️ v0.71 红线修订（原 v0.70「缓存只读不回源」）：本地实证 7992 类目对 vs
# attribute_cache 12 行——只读缓存使「选类目必出属性表单」在 99.8% 类目上不成立。
# 改为与管线懒加载同语义的交互版（services/category_schema_service.py）：缓存
# 优先 → 未命中用租户默认店铺凭证按需拉一次 Ozon → 回写 30d → 失败降级
# found=False+reason（前端提示，不破坏页面）。字典值按单属性按需（?attr_id=，
# 下拉打开时拉，翻页≤3 页），首屏保持 1 次 API。

@root_router.get("/categories/search", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "items": [{
            "description_category_id": "91936",
            "type_id": "91938",
            "node_name": "Автомобильный компрессор",
            "category_path": "Авто и мото / Автоинструменты / Автомобильный компрессор",
            "similarity": 0.87,
        }],
    }}}}})
@root_router.get("/api/v1/categories/search", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "items": [{
            "description_category_id": "91936",
            "type_id": "91938",
            "node_name": "Автомобильный компрессор",
            "category_path": "Авто и мото / Автоинструменты / Автомобильный компрессор",
            "similarity": 0.87,
        }],
    }}}}})
async def v1_categories_search(request: Request):
    """类目树搜索（ZH_HANS）：?q=关键词&limit=20 → 候选 {dc, tp, node_name, category_path}。

    复用 OzonCategoryQuery.search_nodes（jieba 分词 + LIKE，node_type=type 保证
    返回有效 dc/tp 组合）。供 webui 采集箱 manual 类目选择器 / agent 类目确认。
    """
    # B 租户 guard 二期：Bearer 提取→verify→限流 三段内联收敛单行（含限流对齐
    # 原序列）；无租户消费 → verify_bearer（不加 resolve_tenant，行为逐字等价）。
    from api.deps_tenant import verify_bearer_from_request
    verify_bearer_from_request(request, rate_limited=True)

    q_text = (request.query_params.get("q") or "").strip()
    if not q_text:
        return {"items": []}
    try:
        top_k = max(1, min(int(request.query_params.get("limit", 20)), 50))
    except (TypeError, ValueError):
        top_k = 20
    from utils.ozon_category_query import get_category_query
    try:
        rows = get_category_query().search_nodes(
            q_text, top_k=top_k, node_type="type", language="ZH_HANS")
    except Exception as exc:
        # T2(api-M1 补): 503 固定文案，异常细节只进日志
        logger.warning("category tree unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="category tree temporarily unavailable")
    return {"items": [{
        "description_category_id": str(r.get("description_category_id", "") or ""),
        "type_id": str(r.get("type_id", "") or ""),
        "node_name": str(r.get("node_name", "") or ""),
        "category_path": str(r.get("full_path", "") or ""),
        "similarity": float(r.get("similarity", 0) or 0),
    } for r in rows]}


@root_router.get("/categories/attributes", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        # schema 模式（?dc=&tp=）；?attr_id= 时 values 为顶层键
        "found": True,
        "cached": True,
        "fetched": False,
        "attributes": [{
            "id": 4180,
            "name": "Тип",
            "required": True,
            "type": "String",
            "dictionary_id": 0,
            "is_collection": False,
            "max_value_count": 1,
        }],
    }}}}})
@root_router.get("/api/v1/categories/attributes", tags=["analytics"], responses={
    200: {"content": {"application/json": {"example": {
        "found": True,
        "cached": True,
        "fetched": False,
        "attributes": [{
            "id": 4180,
            "name": "Тип",
            "required": True,
            "type": "String",
            "dictionary_id": 0,
            "is_collection": False,
            "max_value_count": 1,
        }],
    }}}}})
async def v1_categories_attributes(request: Request):
    """类目属性 schema + 字典值（缓存优先，未命中按需拉取回写）。

    - ?dc=&tp= → {found, cached, fetched, attributes}；属性键形状与 assemble
      消费一致（id/dictionary_id/name/required/type）+ is_collection/
      max_value_count（值数出口闸同源字段，UI 提示多值/上限）。
    - ?dc=&tp=&attr_id= → 单属性字典值按需拉取 {found, cached, fetched, values}
      （表单下拉打开时调用，避免一个类目几十个字典属性打满首屏）。
    - 未命中且无店铺凭证/拉取失败 → found=False + reason（降级不抛错）。
    """
    # B 租户 guard 二期：Bearer 提取→verify→限流 三段内联收敛单行（原序列位，
    # 鉴权先于参数校验）；无租户列消费但需租户取凭证 → 三段 helper。
    from api.deps_tenant import verify_bearer_from_request
    clean_token = verify_bearer_from_request(request, rate_limited=True)

    dc = (request.query_params.get("dc") or "").strip()
    tp = (request.query_params.get("tp") or "").strip()
    if not dc.isdigit() or not tp.isdigit():
        raise HTTPException(status_code=422, detail="query params dc/tp must be numeric")
    attr_id = (request.query_params.get("attr_id") or "").strip()

    # 按需拉取凭证：租户默认店铺 → 任一 active 店铺回退（schema 与店铺无关，
    # 无默认店铺也应能拉）；两者皆无 → 纯缓存语义，降级提示
    # v0.73 租户统一：凭证按真实 user_id 取（哈希租户 → 懒拉永远无凭证）。
    # 在 try 外调用——resolve_tenant 的 fail-closed（401/503）是鉴权语义，
    # 不得被下方「凭证解析失败降级纯缓存」吞掉。
    # resolve_tenant 保持原位（dc/tp 422 校验之后）；入参 clean token 与原 raw
    # token 等价（resolve_tenant 内部自剥 sk-，缓存键同形）。
    from services.tenant_service import resolve_tenant
    _tenant = resolve_tenant(clean_token)
    _client_id = _api_key = ""
    try:
        from services.credential_service import get_default_credential, list_credentials
        _cred = get_default_credential(_tenant)
        if _cred:
            _client_id = str(_cred.get("ozon_client_id") or "")
            _api_key = str(_cred.get("api_key") or "")
        else:
            for _c in (list_credentials(_tenant) or []):
                if str(_c.get("status") or "") != "active":
                    continue
                try:
                    from services.credential_service import get_decrypted
                    _client_id, _api_key = get_decrypted(_tenant, str(_c["id"]))
                    break
                except Exception:
                    continue
    except Exception as _cred_e:
        logger.debug("categories/attributes 凭证解析失败（降级纯缓存）: %s", _cred_e)

    from services.category_schema_service import (
        get_attribute_values_with_lazy_fetch,
        get_attributes_with_lazy_fetch,
    )

    # ── 单属性字典值按需（?attr_id=）──
    if attr_id:
        if not attr_id.isdigit():
            raise HTTPException(status_code=422, detail="query param attr_id must be numeric")
        try:
            import asyncio as _asyncio
            res = await _asyncio.to_thread(
                get_attribute_values_with_lazy_fetch,
                int(attr_id), int(dc), int(tp), _client_id, _api_key,
            )
        except Exception as exc:
            # T2(api-M1 补): 503 固定文案，异常细节只进日志
            logger.warning("attribute values unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="attribute values temporarily unavailable")
        return {
            "found": bool(res.get("found")),
            "cached": bool(res.get("cached")),
            "fetched": bool(res.get("fetched")),
            "values": res.get("values") or [],
            **({"reason": res["reason"]} if res.get("reason") else {}),
        }

    # ── schema（缓存优先 → 按需拉取回写）──
    try:
        import asyncio as _asyncio
        res = await _asyncio.to_thread(
            get_attributes_with_lazy_fetch, int(dc), int(tp), _client_id, _api_key,
        )
    except Exception as exc:
        # T2(api-M1 补): 503 固定文案，异常细节只进日志
        logger.warning("attribute cache unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="attribute cache temporarily unavailable")
    if not res.get("found"):
        out_fail = {"found": False, "cached": False, "attributes": []}
        if res.get("reason"):
            out_fail["reason"] = res["reason"]
        return out_fail
    out: list[dict] = []
    from utils.ozon_category_query import get_category_query
    for a in (res.get("schema") or [])[:200]:
        if not isinstance(a, dict):
            continue
        attr_id_int = int(a.get("id") or a.get("description_attribute_id") or 0)
        item = {
            "id": attr_id_int,
            "name": str(a.get("name", "") or ""),
            # ⚠️ Ozon 原始字段是 is_required（warm/懒加载存原始响应）——
            # 此前读 a.get("required") 恒 False（webui 必填标记一直没生效）
            "required": bool(a.get("is_required", a.get("required", False))),
            "type": str(a.get("type", "") or ""),
            "dictionary_id": int(a.get("dictionary_id", 0) or 0),
            "is_collection": bool(a.get("is_collection", False)),
            "max_value_count": int(a.get("max_value_count", 0) or 0),
        }
        if item["dictionary_id"] > 0 and attr_id_int > 0:
            try:
                vals = get_category_query().get_dictionary_values(attr_id_int, int(dc), int(tp))
            except Exception:
                vals = None
            if vals:
                src_vals = vals.get("result") if isinstance(vals, dict) else vals
                item["values"] = [{"id": v.get("id"), "value": v.get("value")}
                                  for v in (src_vals or [])[:100]
                                  if isinstance(v, dict) and v.get("id")]
        out.append(item)
    return {"found": True, "cached": bool(res.get("cached")),
            "fetched": bool(res.get("fetched")), "attributes": out}


# ==================== 佣金查询端点（任务 2.1） ====================
# GET /api/v1/commissions/lookup?category_id={description_category_id}（+ legacy /commissions/lookup 双挂）
# 读 category_commission 缓存表（全局共享，无 tenant 隔离——类目佣金是平台级知识）。
# 鉴权与 analytics 读端点一致（Bearer token → _verify_analytics_token + rate_limiter）。

@root_router.get("/commissions/lookup", tags=["commissions"], responses={
    200: {"content": {"application/json": {"example": {
        # 未命中 → {"found": false}
        "found": True,
        "fbs": {"leq_1500": 0.16, "leq_5000": 0.12, "gt_5000": 0.10},
        "fbo": {"leq_1500": 0.14, "leq_5000": 0.11, "gt_5000": 0.09},
        "source": "prices_api",
    }}}}})
@root_router.get("/api/v1/commissions/lookup", tags=["commissions"], responses={
    200: {"content": {"application/json": {"example": {
        "found": True,
        "fbs": {"leq_1500": 0.16, "leq_5000": 0.12, "gt_5000": 0.10},
        "fbo": {"leq_1500": 0.14, "leq_5000": 0.11, "gt_5000": 0.09},
        "source": "prices_api",
    }}}}})
async def http_commissions_lookup(request: Request):
    """类目佣金查询：按 description_category_id 查 category_commission 缓存表。

    query: category_id（Ozon 类目 ID，必填整数）
    → 命中  {"found": true, "fbs": {"leq_1500","leq_5000","gt_5000"}, "fbo": {...}, "source"}
    → 未命中 {"found": false}
    鉴权: Authorization: Bearer <token>（剥离 sk- 前缀）；Supabase 未配置 → 本地放行。
    """
    # B 租户 guard 二期：Bearer 提取→verify→限流 三段内联收敛单行（含限流对齐
    # 原序列）；全局共享缓存表无租户消费 → verify_bearer（不加 resolve_tenant）。
    from api.deps_tenant import verify_bearer_from_request
    verify_bearer_from_request(request, rate_limited=True)

    raw = (request.query_params.get("category_id") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="category_id is required")
    try:
        category_id = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="category_id must be an integer")

    from utils.commission_resolver import get_category_commission

    row = get_category_commission(category_id)
    if row is None:
        return {"found": False}
    return {
        "found": True,
        "fbs": {
            "leq_1500": row.get("fbs_leq_1500"),
            "leq_5000": row.get("fbs_leq_5000"),
            "gt_5000": row.get("fbs_gt_5000"),
        },
        "fbo": {
            "leq_1500": row.get("fbo_leq_1500"),
            "leq_5000": row.get("fbo_leq_5000"),
            "gt_5000": row.get("fbo_gt_5000"),
        },
        "source": row.get("source"),
    }
