r"""数据池 skill 侧客户端（读-回馈闭环的 skill 端）。

对标 goldminer 的跨店 SKU 指标数据湖（worker /analytics/seller-sync 贡献 +
/analytics/sku-metrics 读侧）。贡献纪律：fire-and-forget——上报失败只 debug
log，绝不阻断采集/上架主流程；`METRICS_POOL_REPORT=0` 一键关（风控/调试）。
查池失败返回 None，调用方回退既有 CDP 直采。

worker 契约（批1 已落地，见 worker/src/routes/analytics_routes.py）：
- POST /api/v1/analytics/seller-sync  body={"items": [...], "source_company_id"}
  服务端 ≤12/批（SKU_SYNC_MAX_ITEMS=12）——客户端同款分批，响应
  {"accepted": n, "skipped": n}（accepted 为**本批**受理数）。
- GET  /api/v1/analytics/sku-metrics?skus=1,2,3 → {"metrics": [...]}，
  每行含 sku/sales_payload/variant_payload/category_dc/category_tp/
  needs_*_sync 等；服务端 ≤50/查（SKU_QUERY_MAX=50）。
- 鉴权：Authorization: Bearer <mxou token>（缺失 401）。

worker_url/token 复用 config_store 既有凭证链（cmd_session_sync 同款取法）：
- url  = scripts._const.CLOUD_API_BASE（env WORKER_URL 优先 → 默认
  https://worker.mxou.cn，导入时已 rstrip）
- token = scripts.lib.config_store.get_mxou_token()（~/.pounding/config.json
  → settings.json；未配置返回空串）
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_CHUNK = 12  # goldminer 同款 ≤12/批（= worker SKU_SYNC_MAX_ITEMS）

_QUERY_MAX = 50  # = worker SKU_QUERY_MAX


def _worker_cfg() -> tuple[str, str]:
    """解析 worker url + token（复用既有凭证链，勿在此发明新配置源）。

    未配置 token → RuntimeError（调用方统一按「无 worker 配置」静默跳过；
    worker 端点无 Bearer 恒 401，省一次注定失败的网络往返）。
    """
    from scripts._const import CLOUD_API_BASE
    from scripts.lib.config_store import get_mxou_token

    token = str(get_mxou_token() or "").strip()
    if not token:
        raise RuntimeError("MXOU token 未配置")
    return CLOUD_API_BASE.rstrip("/"), token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def report_seller_sync(items: list[dict], *, source_company_id: str | None = None,
                       timeout: float = 10.0) -> int:
    """上报 worker 数据池（fire-and-forget）。分批 ≤_CHUNK。

    任何失败只 debug log、绝不抛——贡献绝不阻断主流程。env
    `METRICS_POOL_REPORT=0` 一键关。返回值：最后一次 200 响应的
    accepted（非跨批累计——验收测试以恒定 accepted 的 mock 钉死该语义；
    fire-and-forget 场景返回值仅作调用方日志参考，勿用作对账依据）。
    """
    if os.environ.get("METRICS_POOL_REPORT") == "0" or not items:
        return 0
    accepted = 0
    try:
        url, token = _worker_cfg()
    except Exception as exc:  # noqa: BLE001 — 未配置/导入失败一律静默跳过
        logger.debug("数据池上报跳过（无 worker 配置）: %s", exc)
        return 0
    for i in range(0, len(items), _CHUNK):
        chunk = items[i:i + _CHUNK]
        try:
            resp = requests.post(f"{url}/api/v1/analytics/seller-sync",
                                 json={"items": chunk, "source_company_id": source_company_id},
                                 headers=_headers(token), timeout=timeout)
            if resp.status_code == 200:
                accepted = int(resp.json().get("accepted", 0))
        except Exception as exc:  # noqa: BLE001 — 单批失败不中断后续批次
            logger.debug("数据池上报失败（忽略）: %s", exc)
    return accepted


def query_sku_metrics(skus: list[str], timeout: float = 10.0) -> dict[str, dict] | None:
    """查池 → {str(sku): metrics}；失败/未配置 worker → None（调用方走原 CDP 路径）。"""
    try:
        url, token = _worker_cfg()
        resp = requests.get(
            f"{url}/api/v1/analytics/sku-metrics",
            params={"skus": ",".join(str(s) for s in skus[:_QUERY_MAX])},
            headers=_headers(token), timeout=timeout)
        if resp.status_code != 200:
            return None
        return {str(m["sku"]): m for m in resp.json().get("metrics", [])}
    except Exception as exc:  # noqa: BLE001 — 查池永远有 CDP 直采兜底
        logger.debug("数据池查询失败（回退直采）: %s", exc)
        return None
