import json
import logging
import requests
from utils.http_session import session
from utils.ozon_client import ozon_post
from utils.ozon_errors import OzonError
from typing import Dict, Any, List
import time as _time

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state import OzonUploadInput, OzonUploadOutput
from utils.progress_logger import ProgressLogger
from utils.logger import get_logger, log_ozon_api_call
from utils.ozon_client import find_product_by_offer, ozon_check_quota

logger = get_logger(__name__)

# v0.69 T2.2: CREATE 前 offer 存在性检查开关（默认开）。
# 生产实证：对已存在的 declined 死卡重跑 graph → 裸 CREATE → Ozon 对已存在 offer_id
# 自动加 _0 后缀新建卡（549733785579 → 549733785579_0），旧卡残留 → 修一张死卡多一张
# 尸体卡占 SKU 配额。置 False 回退现状（裸 CREATE）。
UPSERT_BY_OFFER = True


def _maybe_upsert_existing_offer(
    ozon_payload: Dict[str, Any],
    ozon_client_id: str,
    ozon_api_key: str,
    is_follow_sell: bool = False,
) -> None:
    """v0.69 T2.2: CREATE 前置闸——Ozon 侧同 offer 已存在 → 注入 product_id 转 UPDATE。

    只处理「真 CREATE」：item 无 product_id（编辑更新/跟卖 UPDATE 的 item 已带
    product_id，跳过）且非跟卖（is_follow_sell——跟卖本就要并卡/CREATE 重建，语义不动；
    import-by-sku pending 路径在节点主流程提前返回，不会走到这里）。

    - 存在（任何 state，含 declined/archived 死卡）→ item["product_id"] = int(pid)：
      /v3/product/import 带 product_id 即同卡更新（对齐 prepare UPDATE 分支的 payload
      形状），后续 ozon_status 轮询链天然复用（UPDATE 有真实 product_id）。
    - 不存在/查询失败 → 原样 CREATE：find_product_by_offer 非致命封装吞 API 异常，
      此处再兜一层防御纵深——预检失败绝不阻塞正常上架。
    """
    if not UPSERT_BY_OFFER or is_follow_sell:
        return
    for item in (ozon_payload.get("items") or []):
        if not isinstance(item, dict) or item.get("product_id"):
            continue  # 已是 UPDATE 语义（编辑更新/跟卖），不动
        offer_id = str(item.get("offer_id") or "").strip()
        if not offer_id:
            continue
        try:
            existing = find_product_by_offer(
                client_id=ozon_client_id, api_key=ozon_api_key, offer_id=offer_id,
            )
        except Exception as exc:  # 防御纵深：查询层兜底之外的任何异常也不阻塞 CREATE
            logger.warning(
                "offer 存在性预检异常（继续 CREATE）: offer_id=%s: %s", offer_id, str(exc)[:200],
            )
            continue
        if not existing:
            continue
        pid = str(existing.get("product_id") or "").strip()
        if not pid.isdigit():
            logger.warning(
                "offer 已存在但 product_id 非法（继续 CREATE 防错更新）: offer_id=%s product_id=%r",
                offer_id, existing.get("product_id"),
            )
            continue
        item["product_id"] = int(pid)
        state_label = existing.get("state") or (
            "archived" if existing.get("archived") else "unknown"
        )
        logger.info(
            "🔄 offer 已存在（state=%s, product_id=%s）→ 覆盖更新不新建（防 _0 尸体卡）: offer_id=%s",
            state_label, pid, offer_id,
        )


def try_set_min_price_floor(
    ozon_client_id: str,
    ozon_api_key: str,
    offer_id,
    product_id,
    price,
    old_price=None,
    promo_price=None,
    logger_obj=None,
):
    """v0.65: 上架成功后给单个商品补送促销底线 min_price（promo_price 即 min_price 底线）。

    - 只处理「真实 product_id + 有 promo_price（三档）」的 CREATE 单；UPDATE/follow 模式 / 无
      promo_price 的调用方在调用前跳过（本函数只做防御性再检查）。
    - 幂等：每卡只调一次；失败仅 logger.warning，绝不抛错影响主流程。
    - 真实调用点在 ozon_status_node：/v3/product/import 只返回 task_id，真实 product_id/
      offer_id 要等 /v1/product/import/info 轮询确认 imported 后才有，而 import/prices 对
      不存在的商品返回 NOT_FOUND_ERROR，故不能在 ozon_upload CREATE 时同步调。

    Returns:
        bool — 是否成功设置（True）或无需设置（跳过返回 False，不视为失败）。
    """
    log = logger_obj if logger_obj is not None else logger
    _pid = str(product_id or "").strip()
    _oid = str(offer_id or "").strip()
    if not _pid or not _pid.isdigit() or not _oid:
        log.warning(
            "min_price 底线设置跳过：缺真实 product_id/offer_id（pid=%r, offer_id=%r）",
            product_id, offer_id,
        )
        return False
    if promo_price is None or str(promo_price) in ("", "0", "None"):
        log.warning("min_price 底线设置跳过：无 promo_price（非三档）")
        return False
    try:
        _pp = int(promo_price)
    except (TypeError, ValueError):
        log.warning("min_price 底线设置跳过：promo_price 非法 %r", promo_price)
        return False
    if _pp <= 0:
        return False
    try:
        from utils.ozon_client import update_min_price_floor

        update_min_price_floor(
            client_id=str(ozon_client_id),
            api_key=str(ozon_api_key),
            offer_id=_oid,
            product_id=int(_pid),
            price=price,
            old_price=old_price,
            min_price=_pp,
        )
        log.info("✅ min_price 底线设置成功: product_id=%s offer_id=%s min_price=%s", _pid, _oid, _pp)
        return True
    except Exception as exc:  # 底线设置失败不阻断主流程
        log.warning("min_price 底线设置失败(不影响上架): product_id=%s: %s", _pid, str(exc)[:300])
        return False


def ozon_upload_node(
    state: OzonUploadInput, 
    config: RunnableConfig, 
    runtime: Runtime[Context]
) -> OzonUploadOutput:
    """
    title: Ozon商品上传节点
    desc: 接收prepared_payload，发送Ozon API(v3/product/import)上传商品，返回task_id用于后续状态轮询
    integrations: Ozon API
    """
    ctx = runtime.context

    logger.info("开始Ozon商品上传...")
    progress = ProgressLogger()
    progress.log_node_start("ozon_upload_node", "Ozon商品上传节点")
    progress.log_node_action("正在发送Ozon API上传商品...")

    # 从state获取prepared_payload
    ozon_payload = state.ozon_payload
    ozon_client_id = state.ozon_client_id
    ozon_api_key = state.ozon_api_key

    purchase_url = state.purchase_url
    purchase_cost = state.purchase_cost
    sku_id = state.sku_id
    profit_estimation = state.profit_estimation

    # 检查error_message和validation_errors（阻止上传）
    error_message: str = state.error_message if state.error_message else ""
    validation_errors: list = state.validation_errors if state.validation_errors else []

    if error_message and ("严重错误" in error_message or "验证失败" in error_message):
        logger.error(f"ozon_validate发现严重错误，阻止上传: {error_message}")
        logger.error(f"validation_errors详情: {validation_errors}")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message=f"Ozon预检测失败: {error_message}",
            validation_errors=validation_errors,
            failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带（默认值已归零）
            stages={"ozon_upload": "blocked_by_validation"}
        )

    if validation_errors and len(validation_errors) > 0:
        logger.warning(f"ozon_validate发现{len(validation_errors)}个验证警告，但允许继续上传")
    
    # 验证payload完整性
    if not ozon_payload:
        logger.error("prepared_payload为空，无法上传")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message="Prepared payload is required",
            failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
        )
    
    if not ozon_client_id or not ozon_api_key:
        logger.error("缺少Ozon API认证信息")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            error_message="Missing Ozon API credentials",
            failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
        )
    
    # 验证payload是否包含必需字段
    items = ozon_payload.get("items", [])
    if not items:
        logger.error("payload缺少items数组")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            error_message="Payload missing items array",
            failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
        )
    
    # 验证第一个item是否包含必需字段
    first_item = items[0]
    required_fields = ["name", "offer_id", "description_category_id", "type_id", 
                      "price", "old_price", "vat", "currency_code", 
                      "weight", "weight_unit", "depth", "width", "height", "dimension_unit"]
    
    missing_fields = []
    for field in required_fields:
        if field not in first_item:
            missing_fields.append(field)
    
    if missing_fields:
        logger.warning(f"payload缺少字段: {missing_fields}")
        # 不阻止上传，只记录警告
    
    # v0.22 P2a: api 模式 import-by-sku 已提交但 product_id 未回 → 跳过 v3 import
    # （避免与后台 import 竞争同 offer_id 创建双卡；由后续轮询 import/info 收尾）
    # ✅ v0.69 T2.2: import_submitted 已声明进 OzonUploadInput（此前未声明被 langgraph
    # 按 Input model 过滤，getattr 恒 False 属死代码）——守卫自此真实生效。
    if getattr(state, "import_submitted", False) and not getattr(state, "product_id", None):
        logger.warning("⚠️ import-by-sku 处理中（import_submitted），跳过 v3 import，返回 pending")
        return OzonUploadOutput(
            product_id=None,
            upload_status="pending",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
        )

    # 发送Ozon API上传请求
    try:
        # ✅ 上传前检查配额（使用 ozon_client 统一封装）
        quota = ozon_check_quota(
            client_id=ozon_client_id,
            api_key=ozon_api_key,
        )
        if not quota["ok"]:
            return OzonUploadOutput(
                product_id=None, upload_status="failed",
                purchase_url=purchase_url, purchase_cost=purchase_cost,
                sku_id=sku_id, profit_estimation=profit_estimation,
                error_message=(
                    f"配额不足: 日创建 {quota['daily_used']}/{quota['daily_limit']}"
                    f", 总产品 {quota['total_used']}/{quota['total_limit']}"
                ),
                failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
            )
        if quota["remaining_total"] <= 5:
            logger.warning("⚠️ 产品配额仅剩 %d 个！建议归档旧产品释放空间", quota["remaining_total"])

        # ✅ v0.69 T2.2: CREATE 前置闸——Ozon 侧同 offer 已存在（含 declined 死卡）
        # → 注入 product_id 转 UPDATE（同 offer 覆盖不新建，消 _0 尸体卡）。
        # 非致命：查询失败/异常均按「不存在」放行 CREATE，绝不阻塞上架。
        _maybe_upsert_existing_offer(
            ozon_payload,
            ozon_client_id,
            ozon_api_key,
            is_follow_sell=bool(getattr(state, "is_follow_sell", False)),
        )

        # F-F01（2026-09-09 审计）：收敛 ozon_post——此前 session.post 直发无
        # 429/5xx 重试、无全局限流，Ozon 一次限流即整任务失败再走整图重试。
        # 调用日志由 ozon_post 内部记录；OzonError 带类型化 status_code/payload。
        logger.info("发送Ozon API请求: /v3/product/import")
        logger.info(f"Payload items数量: {len(items)}")
        logger.info(f"第一个item的name: {first_item.get('name', 'N/A')}")
        logger.info(f"第一个item的currency_code: {first_item.get('currency_code', 'N/A')}")
        logger.info(f"第一个item的vat: {first_item.get('vat', 'N/A')}")

        _t0 = _time.monotonic()
        try:
            data = ozon_post(
                ozon_client_id, ozon_api_key,
                "/v3/product/import", ozon_payload, timeout=60,
            )
        except OzonError as exc:
            _dur = (_time.monotonic() - _t0) * 1000
            log_ozon_api_call(
                method="POST", endpoint="/v3/product/import",
                status_code=exc.status_code or 0, duration_ms=_dur,
                request_summary={"items_count": len(items)},
                response_summary=None,
            )
            logger.error(f"Ozon API错误（重试耗尽）: {exc}")
            return OzonUploadOutput(
                product_id=None,
                upload_status="failed",
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                error_message=f"Ozon API error: {exc}",
                failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
            )
        _dur = (_time.monotonic() - _t0) * 1000

        log_ozon_api_call(
            method="POST", endpoint="/v3/product/import",
            status_code=200, duration_ms=_dur,
            request_summary={"items_count": len(items)},
            response_summary={"task_id": data.get("result", {}).get("task_id")},
        )

        logger.info(f"Ozon API响应: {json.dumps(data, indent=2, ensure_ascii=False)}")

        # 解析响应
        result = data.get("result", {})
        task_id = result.get("task_id", "")

        if task_id:
            logger.info(f"Ozon上传任务创建成功，task_id: {task_id}（后续ozon_status_node用此task_id轮询状态）")
            # ✅ v0.73 上传静默收口（Issue6 上游）：import task_id 不是商品 ID——
            # 旧「向后兼容」写法 product_id=str(task_id) 污染下游语义（T0.4 终态佐证闸、
            # ozon_status 的 pre_product_id min_price 闸、webui/取证展示）。product_id 置
            # None，真实商品 ID 由 ozon_status_node 轮询 import/info 确认 imported 后回填；
            # task_id 走 ozon_task_id 专用通道（OzonStatusInput.product_id 可空，轮询用
            # state.product_id or ozon_task_id 兜底，链路不断）。
            return OzonUploadOutput(
                product_id=None,
                ozon_task_id=str(task_id),  # ✅ P3 修复：隔离任务ID
                upload_status="success",
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                error_message=""
            )
        # ✅ v0.73 上传静默收口（Issue6 上游）：200 但无 import task_id = 上传未真正
        # 落地，显式 failed（此前英文 "Ozon response missing task_id" 无响应摘要，
        # 现场难对账）；带截断响应摘要便于排查。
        _resp_summary = json.dumps(data, ensure_ascii=False)[:300]
        logger.warning("Ozon响应缺少task_id，响应摘要: %s", _resp_summary)
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message=f"Ozon 未返回 import task_id（HTTP 200），响应摘要: {_resp_summary}",
            failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
        )
    
    except requests.exceptions.Timeout:
        logger.error("Ozon API请求超时")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message="Ozon API request timeout",
            failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
        )

    # （json.JSONDecodeError 分支已随 F-F01 收敛 ozon_post 移除——响应解析在
    #   ozon_post 内部，此处不再触碰 raw response）

    except requests.exceptions.RequestException as e:
        logger.error(f"Ozon API请求异常: {str(e)}")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message=f"Ozon API request exception: {str(e)}",
            failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
        )
    
    except Exception as e:
        logger.error(f"未知异常: {str(e)}")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message=f"Unknown exception: {str(e)}",
            failed_stage="ozon_upload",  # ✅ v0.73 Task8: 失败出口显式带
        )
