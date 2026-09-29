"""worker 预估客户端（v0.83 批①）——skill 端唯一算价出口。

背景（docs/PLAN-v083-quality-campaign-v1.md §3 批①）：skill 里曾有 5 处内联定价公式
（discover `_calculate_profit` / cloud_probe 魔数 1.44375 / cli `_estimate_and_print` /
cli `cmd_search` / `estimate_shipping_cny` 兜底），与 worker `compute_price` 链漂移。
本模块把「问 worker 要价」收敛为两个函数，skill 侧零公式：

- ``estimate_batch(items, credential_id=None)`` → POST /api/v1/estimate/batch
- ``estimate_envelope(envelope)``              → POST /api/v1/estimate

**降级纪律**：worker 不可达 / 401 / 404 / 任何异常 → 返回 ``None``（调用方标
``estimate_source="unavailable"`` 并省略预估字段）——**绝不回落 legacy 本地公式**
（回落即制造第二套价，正是本批要消灭的漂移源）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 单批上限（与 worker ESTIMATE_BATCH_MAX_ITEMS 一致）
BATCH_MAX_ITEMS = 50
# worker 预估端点超时（秒）——预估是选品/展示辅助，超时即降级不阻塞主流程
_WORKER_TIMEOUT = 6


def _worker_base_and_token():
    """取 worker base URL 与 MXOU token（复用 config_store 既有凭证链）。"""
    from scripts._const import CLOUD_API_BASE
    from scripts.lib.config_store import get_mxou_token

    return CLOUD_API_BASE, get_mxou_token()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def estimate_batch(items: list[dict], credential_id=None, timeout: int = _WORKER_TIMEOUT) -> list | None:
    """批量预估：POST /api/v1/estimate/batch。

    Args:
        items: 每项 ``{purchase_cost, weight_g, dims_mm, attributes?, currency_code?,
            commission_segments?, dc?}``；>BATCH_MAX_ITEMS 时只取前 BATCH_MAX_ITEMS。
        credential_id: 可选店铺凭证（worker 侧解密探测 3PL）。
        timeout: 秒。

    Returns:
        与 ``items`` 等长的 ``list``（命中项为 worker 返回的 row dict，失败/未返回项为
        ``None``）；整体失败（不可达/非 200/异常/无 token）→ ``None``。
    """
    if not isinstance(items, list) or not items:
        return []
    try:
        import requests

        base, token = _worker_base_and_token()
        if not token:
            return None
        body: dict = {"items": items[:BATCH_MAX_ITEMS]}
        if credential_id:
            body["credential_id"] = str(credential_id)
        resp = requests.post(
            f"{base}/api/v1/estimate/batch",
            json=body,
            headers=_auth_headers(token),
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = resp.json() or {}
        out: list = [None] * len(items)
        for row in data.get("items") or []:
            if not isinstance(row, dict):
                continue
            idx = row.get("index")
            if isinstance(idx, int) and 0 <= idx < len(out) and row.get("ok"):
                out[idx] = row
        return out
    except Exception as exc:  # 网络/解析/鉴权一律降级
        logger.debug("worker estimate/batch 不可用（降级无预估）: %s", str(exc)[:160])
        return None


def estimate_envelope(envelope: dict, overrides: dict | None = None,
                      timeout: int = _WORKER_TIMEOUT) -> dict | None:
    """单信封预估：POST /api/v1/estimate（body ``{envelope, ...overrides}``）。

    Returns: worker 预估算响应 dict；不可达/非 200/异常 → ``None``（降级无预估）。
    """
    if not isinstance(envelope, dict) or not isinstance(envelope.get("draft"), dict):
        return None
    try:
        import requests

        base, token = _worker_base_and_token()
        if not token:
            return None
        body: dict = {"envelope": envelope}
        if isinstance(overrides, dict):
            body.update(overrides)
        # worker ``POST /api/v1/estimate``（estimate_routes.router_estimate）用
        # ``_extract_token_from_body`` 从**请求体 token 字段**取鉴权——只发 Bearer
        # 会被 401 "Token is required" 降级无预估（v0.83 gate B1 实锤）。同源先例
        # ``_query_logistics_from_worker``：body token + Bearer 头同时发（服务端剥
        # 一层 sk-，有无前缀均可）。token 在 overrides 之后落，防被覆盖。
        body["token"] = token
        resp = requests.post(
            f"{base}/api/v1/estimate",
            json=body,
            headers=_auth_headers(token),
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("worker estimate 不可用（降级无预估）: %s", str(exc)[:160])
        return None


def build_batch_item(
    purchase_cost,
    weight_g=None,
    dims_mm=None,
    attributes=None,
    currency_code=None,
    commission_segments=None,
    dc=None,
    scid=None,
) -> dict:
    """组装单条 batch item（键与 worker EstimateBatchItem 对齐；空值省略纪律）。

    scid（v0.83.1）：1688 source_category_id——dc 缺席时 worker 经学习映射表
    反查 dc（佣金冷启动解锁：discover 关键词候选无 Ozon dc 时佣金恒 fallback）。
    """
    item: dict = {"purchase_cost": float(purchase_cost or 0)}
    if weight_g:
        try:
            item["weight_g"] = float(weight_g)
        except (TypeError, ValueError):
            pass
    if isinstance(dims_mm, dict) and dims_mm:
        clean = {}
        for k in ("length", "width", "height"):
            v = dims_mm.get(k)
            if v:
                try:
                    clean[k] = float(v)
                except (TypeError, ValueError):
                    pass
        if clean:
            item["dims_mm"] = clean
    if isinstance(attributes, dict) and attributes:
        item["attributes"] = attributes
    if currency_code:
        item["currency_code"] = str(currency_code)
    if isinstance(commission_segments, dict) and commission_segments:
        item["commission_segments"] = commission_segments
    if dc not in (None, ""):
        item["dc"] = str(dc)
    if scid not in (None, ""):
        item["scid"] = str(scid)
    return item
