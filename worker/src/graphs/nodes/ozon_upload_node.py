import json
import logging
import requests
from utils.http_session import session
from typing import Dict, Any, List
import time as _time

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state import OzonUploadInput, OzonUploadOutput
from utils.progress_logger import ProgressLogger
from utils.logger import get_logger, log_ozon_api_call
from utils.ozon_client import ozon_check_quota

logger = get_logger(__name__)


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
            error_message="Prepared payload is required"
        )
    
    if not ozon_client_id or not ozon_api_key:
        logger.error("缺少Ozon API认证信息")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            error_message="Missing Ozon API credentials"
        )
    
    # 验证payload是否包含必需字段
    items = ozon_payload.get("items", [])
    if not items:
        logger.error("payload缺少items数组")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            error_message="Payload missing items array"
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
                )
            )
        if quota["remaining_total"] <= 5:
            logger.warning("⚠️ 产品配额仅剩 %d 个！建议归档旧产品释放空间", quota["remaining_total"])
        
        url = "https://api-seller.ozon.ru/v3/product/import"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json"
        }
        
        logger.info(f"发送Ozon API请求: {url}")
        logger.info(f"Payload items数量: {len(items)}")
        logger.info(f"第一个item的name: {first_item.get('name', 'N/A')}")
        logger.info(f"第一个item的currency_code: {first_item.get('currency_code', 'N/A')}")
        logger.info(f"第一个item的vat: {first_item.get('vat', 'N/A')}")
        
        _t0 = _time.monotonic()
        response = session.post(
            url,
            headers=headers,
            json=ozon_payload,
            timeout=60
        )
        _dur = (_time.monotonic() - _t0) * 1000

        log_ozon_api_call(
            method="POST", endpoint="/v3/product/import",
            status_code=response.status_code, duration_ms=_dur,
            request_summary={"items_count": len(items)},
            response_summary={"task_id": response.json().get("result", {}).get("task_id")} if response.ok else None,
        )
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"Ozon API响应: {json.dumps(data, indent=2, ensure_ascii=False)}")
            
            # 解析响应
            result = data.get("result", {})
            task_id = result.get("task_id", "")
            
            if task_id:
                logger.info(f"Ozon上传任务创建成功，task_id: {task_id}（后续ozon_status_node用此task_id轮询状态）")
                return OzonUploadOutput(
                    product_id=str(task_id) if task_id else "",  # 返回task_id，ozon_status_node会用它轮询/import/info
                    upload_status="success",
                    purchase_url=purchase_url,
                    purchase_cost=purchase_cost,
                    sku_id=sku_id,
                    profit_estimation=profit_estimation,
                    error_message=""
                )
            else:
                logger.warning("Ozon响应缺少task_id")
                return OzonUploadOutput(
                    product_id=None,
                    upload_status="failed",
                    purchase_url=purchase_url,
                    purchase_cost=purchase_cost,
                    sku_id=sku_id,
                    profit_estimation=profit_estimation,
                    error_message="Ozon response missing task_id"
                )
        else:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get("message", f"HTTP {response.status_code}")
            logger.error(f"Ozon API错误: {error_msg}")
            logger.error(f"完整错误响应: {json.dumps(error_data, indent=2, ensure_ascii=False)}")
            
            return OzonUploadOutput(
                product_id=None,
                upload_status="failed",
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                error_message=f"Ozon API error: {error_msg}"
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
            error_message="Ozon API request timeout"
        )
    
    except json.JSONDecodeError as e:
        logger.error(f"Ozon API响应JSON解析失败: {str(e)}")
        logger.error(f"响应内容: {response.text[:500]}")  # 记录前500字符
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message=f"Ozon API JSON decode error: {str(e)}"
        )
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Ozon API请求异常: {str(e)}")
        return OzonUploadOutput(
            product_id=None,
            upload_status="failed",
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            error_message=f"Ozon API request exception: {str(e)}"
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
            error_message=f"Unknown exception: {str(e)}"
        )