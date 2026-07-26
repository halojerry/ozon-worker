"""配额检查节点 — 上传前检查 Ozon 店铺配额，配额不足时阻断上传避免资源浪费。

放置在 ozon_validate 和 ozon_upload 之间，作为上传前的最后一道防线。
配额不足时返回 blocked 状态，由图的 conditional edge 路由到 validation_retry_wrapper 处理。
"""

from __future__ import annotations

from typing import Dict, Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state import OzonUploadOutput
from utils.ozon_client import ozon_check_quota
from utils.logger import get_logger

logger = get_logger(__name__)


def check_quota_node(
    state,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> OzonUploadOutput:
    """检查 Ozon 店铺配额，配额不足时阻断上传。

    检查项：
    1. 每日创建配额 (daily_create) — 今日已用 >= 上限 → 阻断
    2. 总产品上限 (total) — 当前总数 >= 上限 → 阻断
    3. 剩余不足 5 个 → 警告但允许继续

    如果配额不足，返回 error_message 让 graph 路由到 validation_retry_wrapper，
    retry wrapper 的超时等待后会再次检查配额（Ozon 每日 0 点重置）。
    """
    logger.info("🔍 检查 Ozon 店铺配额...")

    ozon_client_id = getattr(state, "ozon_client_id", "")
    ozon_api_key = getattr(state, "ozon_api_key", "")

    if not ozon_client_id or not ozon_api_key:
        logger.error("缺少 Ozon API 凭证，无法检查配额")
        return _quota_blocked(
            state,
            "缺少 Ozon API 凭证，无法检查配额",
        )

    quota = ozon_check_quota(
        client_id=ozon_client_id,
        api_key=ozon_api_key,
    )

    daily_used = quota["daily_used"]
    daily_limit = quota["daily_limit"]
    total_used = quota["total_used"]
    total_limit = quota["total_limit"]
    remaining_daily = quota["remaining_daily"]
    remaining_total = quota["remaining_total"]
    quota_error = quota.get("error")

    logger.info(
        "配额状态: 日创建 %d/%d (剩%d), 总产品 %d/%d (剩%d), 日更新 %d/%d",
        daily_used, daily_limit, remaining_daily,
        total_used, total_limit, remaining_total,
        quota["daily_update_used"], quota["daily_update_limit"],
    )

    # 配额查询失败（网络问题等）— 不阻塞上传
    if quota_error and not quota["ok"]:
        # ok=False 且无 error 说明是真正的配额耗尽
        pass
    elif quota_error:
        logger.warning("配额查询异常但允许继续: %s", quota_error)
        return _quota_ok(state)

    # 每日配额耗尽
    if daily_used >= daily_limit:
        logger.error(
            "❌ 每日创建配额已耗尽 (%d/%d)，阻断上传。每日 0 点 (UTC) 重置。",
            daily_used, daily_limit,
        )
        return _quota_blocked(
            state,
            f"每日创建配额已耗尽 ({daily_used}/{daily_limit})，请等待每日 0 点 (UTC) 重置",
        )

    # 总配额耗尽
    if total_used >= total_limit:
        logger.error(
            "❌ 店铺产品总数已达上限 (%d/%d)，阻断上传。请归档旧产品释放空间。",
            total_used, total_limit,
        )
        return _quota_blocked(
            state,
            f"店铺产品总数已达上限 ({total_used}/{total_limit})，请归档旧产品后重试",
        )

    # 配额紧张警告
    if remaining_daily <= 5:
        logger.warning("⚠️ 今日剩余配额紧张: %d 个，请谨慎使用", remaining_daily)
    if remaining_total <= 10:
        logger.warning("⚠️ 总剩余配额紧张: %d 个，建议归档旧产品", remaining_total)

    logger.info("✅ 配额检查通过: 日剩余 %d, 总剩余 %d", remaining_daily, remaining_total)
    return _quota_ok(state)


def _quota_ok(state) -> OzonUploadOutput:
    """配额检查通过，透传所有字段。"""
    return OzonUploadOutput(
        product_id=getattr(state, "product_id", None),
        upload_status="",
        purchase_url=getattr(state, "purchase_url", ""),
        purchase_cost=getattr(state, "purchase_cost", ""),
        sku_id=getattr(state, "sku_id", ""),
        profit_estimation=getattr(state, "profit_estimation", {}),
        error_message="",
        validation_errors=[],
        stages={"check_quota": "ok"},
    )


def _quota_blocked(state, reason: str) -> OzonUploadOutput:
    """配额检查失败，阻断上传。"""
    return OzonUploadOutput(
        product_id=None,
        upload_status="failed",
        purchase_url=getattr(state, "purchase_url", ""),
        purchase_cost=getattr(state, "purchase_cost", ""),
        sku_id=getattr(state, "sku_id", ""),
        profit_estimation=getattr(state, "profit_estimation", {}),
        error_message=f"[QUOTA_BLOCKED] {reason}",
        validation_errors=[],
        failed_stage="check_quota",
        stages={"check_quota": "blocked"},
    )
