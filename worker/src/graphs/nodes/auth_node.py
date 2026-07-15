"""认证节点 - 验证mxou token + Supabase余额检查 + Ozon店铺信息查询"""
import os
import json
import logging
import requests
from typing import Any, Dict, Optional
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import AuthInput, AuthOutput
from utils.progress_logger import ProgressLogger
from utils.image_url_processor import clear_cache  # ✅ 内存优化：每个产品开始时清理URL缓存


logger = logging.getLogger(__name__)


def query_ozon_seller_info(ozon_client_id: str, ozon_api_key: str) -> Dict[str, Any]:
    """
    查询Ozon店铺信息，获取currency_code
    
    Args:
        ozon_client_id: Ozon Client-Id
        ozon_api_key: Ozon Api-Key
    
    Returns:
        Dict包含currency_code和其他店铺信息
    """
    try:
        url = "https://api-seller.ozon.ru/v1/seller/info"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json"
        }
        
        # 🔍 调试：打印请求参数
        logger.info(f"调用Ozon API: Client-Id={ozon_client_id}, Api-Key={ozon_api_key[:10]}...")
        
        # Ozon API要求POST请求，但body为空对象
        response = requests.post(url, headers=headers, json={}, timeout=30)
        
        # 🔍 调试：打印响应状态和内容
        logger.info(f"Ozon API响应: status={response.status_code}, body={response.text[:200]}...")
        
        if response.status_code != 200:
            logger.error(f"Ozon店铺信息查询失败: {response.status_code} - {response.text}")
            return {"currency_code": "", "error": f"API error: {response.status_code}"}
        
        data: Any = response.json()
        
        # 🔍 调试：打印完整响应结构
        logger.info(f"Ozon API完整响应: {json.dumps(data, ensure_ascii=False)[:500]}...")
        
        # 解析currency_code（关键：决定价格货币类型）
        # 正确路径：data.company.currency（不是data.result.company）
        company = data.get("company", {})
        
        # 🔍 调试：打印company对象
        logger.info(f"company对象: {json.dumps(company, ensure_ascii=False)[:200]}...")
        
        if isinstance(company, dict):
            currency_code = company.get("currency", "")
            
            # 🔍 调试：打印获取到的currency
            logger.info(f"获取到的currency: '{currency_code}'")
            
            # 验证currency_code是否有效
            if currency_code and currency_code in ["CNY", "RUB", "USD", "EUR", "KZT"]:
                logger.info(f"✅ Ozon店铺货币类型验证成功: {currency_code}")
                return {"currency_code": currency_code}
            else:
                logger.warning(f"⚠️ 未知的currency_code: '{currency_code}', 返回空字符串")
                return {"currency_code": ""}
        
        # 解析失败，返回空字符串（让pricing_node处理默认值）
        logger.error("❌ 无法解析Ozon API响应结构：company不是dict")
        return {"currency_code": ""}
        
    except Exception as e:
        logger.error(f"❌ 查询Ozon店铺信息异常: {str(e)}")
        return {"currency_code": "", "error": str(e)}


def auth_node(state: AuthInput, config: RunnableConfig, runtime: Runtime[Context]) -> AuthOutput:
    """
    title: 认证节点
    desc: 验证api.mxou.cn token，检查用户余额，查询Ozon店铺信息（currency_code），返回认证信息。关键：从envelope中提取draft/source/extensions并传递给下游节点。
    integrations: api.mxou.cn API, Ozon API, Supabase
    """
    ctx = runtime.context
    
    # ✅ 内存优化：每个产品工作流开始时清理URL缓存，防止跨产品缓存累积导致内存泄漏
    clear_cache()
    
    # 公共服务配置（从环境变量读取，带fallback默认值）
    supabase_url = os.getenv("SUPABASE_URL", "https://kekmppsuiiokdckdeolv.supabase.co")
    supabase_key = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtla21wcHN1aWlva2Rja2Rlb2x2Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NDYyMDA0NCwiZXhwIjoyMDkwMTk2MDQ0fQ.ZkJMnjrlUQKaUpMU3eug9EQLUsoN0mOWI8wzC3jRkAU")
    
    token = state.token
    ozon_client_id = state.ozon_client_id
    ozon_api_key = state.ozon_api_key
    envelope = state.envelope or {}
    
    # ✅ 从envelope中提取draft、source、extensions（兼容扁平payload结构）
    # 检查envelope是否包含draft字段（标准三层结构）
    if "draft" in envelope and isinstance(envelope.get("draft"), dict):
        # 标准三层结构（envelope包含draft/source/extensions）
        draft = envelope.get("draft", {})
        source = envelope.get("source", {})
        extensions = envelope.get("extensions", {})
        logger.info("✅ Payload结构：标准三层结构（envelope包含draft字段）")
    else:
        # 扁平结构（envelope直接包含产品数据）
        draft = envelope  # 直接使用envelope作为draft
        source = {
            "purchase_url": envelope.get("purchase_url", ""),
            "purchase_cost": envelope.get("purchase_cost", "")
        }
        extensions = {}
        logger.info("✅ Payload结构：扁平结构（envelope直接包含产品数据，已转换为draft）")
    
    logger.info(f"提取的draft字段：title={draft.get('title', '')[:50] if isinstance(draft, dict) else ''}...")
    
    # ✅ 提取原始产品图片（兼容扁平结构）
    original_images: list[str] = []
    if isinstance(draft, dict):
        # 方式1：从draft.images中提取（扁平结构时，draft就是envelope，直接提取envelope.images）
        images_field = draft.get("images", [])
        if isinstance(images_field, list):
            original_images = [str(img) for img in images_field if isinstance(img, str)]
    
    # 方式2：如果draft中没有图片，尝试从envelope.assets中提取
    if len(original_images) == 0:
        assets = envelope.get("assets", {})
        if isinstance(assets, dict):
            image_urls = assets.get("image_urls", [])
            if isinstance(image_urls, list):
                original_images = [str(img) for img in image_urls if isinstance(img, str)]
    
    logger.info(f"提取原始产品图片: {len(original_images)}张")
    if len(original_images) > 0:
        logger.info(f"第一张图片URL: {original_images[0]}")
    
    # 参数验证
    if not token:
        return AuthOutput(
            user_id="",
            token_id="",
            balance=0.0,
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            ozon_client_id=ozon_client_id,
            ozon_api_key=ozon_api_key,
            currency_code="",  # 默认RUB
            draft=draft,  # 即使认证失败，也返回draft等数据
            source=source,
            extensions=extensions,
            original_images=original_images,  # 关键：传递原始图片
            error_code="AUTH_INVALID",
            error_message="Token is required"
        )
    
    try:
        # Step 1: 验证token（查询Supabase tokens表）
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json"
        }
        
        # 查询token（使用key字段，不是token字段）
        # 注意：tokens表的主键字段是key，不是token
        # 从token中剥离sk-前缀（如果有）
        clean_token = token.replace("sk-", "") if token.startswith("sk-") else token
        token_query_url = f"{supabase_url}/rest/v1/tokens?key=eq.{clean_token}&select=*"
        
        # 增加重试机制，应对Supabase临时超时
        response = None
        supabase_unreachable = False
        for _retry_idx in range(3):
            try:
                response = requests.get(token_query_url, headers=headers, timeout=45)
                break
            except Exception as retry_err:
                if _retry_idx < 2:
                    import time as _time
                    _time.sleep(2)
                    continue
                # Supabase完全不可达，降级处理：用默认值继续
                logger.warning(f"Supabase连接失败（3次重试后仍超时），降级处理: {retry_err}")
                supabase_unreachable = True
                break
        if supabase_unreachable:
            logger.warning("Supabase不可达，使用降级模式继续执行工作流")
            return AuthOutput(
                user_id="supabase_offline",
                token_id="supabase_offline",
                balance=999.0,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                ozon_client_id=ozon_client_id,
                ozon_api_key=ozon_api_key,
                currency_code="",
                draft=draft,
                source=source,
                extensions=extensions,
                original_images=original_images,
                error_code="",
                error_message=""
            )
        
        if response is not None and response.status_code >= 500:
            logger.warning(f"Supabase服务器错误({response.status_code})，降级处理")
            return AuthOutput(
                user_id="supabase_offline",
                token_id="supabase_offline",
                balance=999.0,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                ozon_client_id=ozon_client_id,
                ozon_api_key=ozon_api_key,
                currency_code="",
                draft=draft,
                source=source,
                extensions=extensions,
                original_images=original_images,
                error_code="",
                error_message=""
            )
        
        if response is not None and response.status_code != 200:
            logger.error(f"Supabase查询token失败: {response.status_code} - {response.text}")
            return AuthOutput(
                user_id="",
                token_id="",
                balance=0.0,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                ozon_client_id=ozon_client_id,
                ozon_api_key=ozon_api_key,
                currency_code="",
                draft=draft,
                source=source,
                extensions=extensions,
                original_images=original_images,  # 关键：传递原始图片
                error_code="AUTH_INVALID",
                error_message=f"Token validation failed: {response.status_code}"
            )
        
        tokens_data: Any = response.json()
        
        # 验证token是否存在
        if not isinstance(tokens_data, list) or len(tokens_data) == 0:
            return AuthOutput(
                user_id="",
                token_id="",
                balance=0.0,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                ozon_client_id=ozon_client_id,
                ozon_api_key=ozon_api_key,
                currency_code="",
                draft=draft,
                source=source,
                extensions=extensions,
                original_images=original_images,  # 关键：传递原始图片
                error_code="AUTH_INVALID",
                error_message="Token not found"
            )
        
        token_record: Dict[str, Any] = tokens_data[0]
        user_id: str = str(token_record.get("user_id", ""))
        token_id: str = str(token_record.get("id", ""))
        
        if not user_id:
            return AuthOutput(
                user_id="",
                token_id="",
                balance=0.0,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                ozon_client_id=ozon_client_id,
                ozon_api_key=ozon_api_key,
                currency_code="",
                draft=draft,
                source=source,
                extensions=extensions,
                original_images=original_images,  # 关键：传递原始图片
                error_code="AUTH_INVALID",
                error_message="User ID not found in token record"
            )
        
        # Step 2: 查询用户余额（查询Supabase users表）
        user_query_url = f"{supabase_url}/rest/v1/users?id=eq.{user_id}&select=*"
        
        user_response = None
        for _retry_idx in range(3):
            try:
                user_response = requests.get(user_query_url, headers=headers, timeout=45)
                break
            except Exception as retry_err:
                if _retry_idx < 2:
                    import time as _time
                    _time.sleep(2)
                    continue
                raise retry_err
        if user_response is None:
            raise Exception("Supabase用户查询失败（3次重试后仍超时）")
        
        if user_response.status_code != 200:
            logger.error(f"Supabase查询用户失败: {user_response.status_code} - {user_response.text}")
            return AuthOutput(
                user_id=user_id,
                token_id=token_id,
                balance=0.0,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                ozon_client_id=ozon_client_id,
                ozon_api_key=ozon_api_key,
                currency_code="",
                draft=draft,
                source=source,
                extensions=extensions,
                original_images=original_images,  # 关键：传递原始图片
                error_code="AUTH_INVALID",
                error_message=f"User query failed: {user_response.status_code}"
            )
        
        users_data: Any = user_response.json()
        
        if not isinstance(users_data, list) or len(users_data) == 0:
            return AuthOutput(
                user_id=user_id,
                token_id=token_id,
                balance=0.0,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                ozon_client_id=ozon_client_id,
                ozon_api_key=ozon_api_key,
                currency_code="",
                draft=draft,
                source=source,
                extensions=extensions,
                original_images=original_images,  # 关键：传递原始图片
                error_code="AUTH_INVALID",
                error_message="User not found"
            )
        
        user_record: Dict[str, Any] = users_data[0]
        quota: float = float(user_record.get("quota", 0))
        used_quota: float = float(user_record.get("used_quota", 0))
        balance: float = quota - used_quota
        
        # Step 3: 查询Ozon店铺信息（获取currency_code）- 提前查询，确保所有情况都能获取
        ozon_seller_info = query_ozon_seller_info(ozon_client_id, ozon_api_key)
        currency_code = ozon_seller_info.get("currency_code", "")  # 默认空字符串，让pricing_node处理默认值
        logger.info(f"查询Ozon店铺信息: currency_code={currency_code}")
        
        # Step 4: 检查余额是否充足
        if balance <= 0:
            logger.warning(f"用户余额不足: user_id={user_id}, balance={balance}")
            return AuthOutput(
                user_id=user_id,
                token_id=token_id,
                balance=0.0,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                ozon_client_id=ozon_client_id,
                ozon_api_key=ozon_api_key,
                currency_code=currency_code,  # 关键：余额不足时也要传递currency_code
                draft=draft,
                source=source,
                extensions=extensions,
                original_images=original_images,  # 关键：传递原始图片
                error_code="AUTH_EXHAUSTED",
                error_message="Insufficient balance"
            )
        
        logger.info(f"认证成功: user_id={user_id}, balance={balance}, currency_code={currency_code}")
        
        # 认证成功，返回完整信息（包括draft等数据）
        return AuthOutput(
            user_id=user_id,
            token_id=token_id,
            balance=balance,
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            ozon_client_id=ozon_client_id,
            ozon_api_key=ozon_api_key,
            currency_code=currency_code,  # 关键：传递货币类型给下游节点
            draft=draft,  # 关键：传递给下游节点
            source=source,
            extensions=extensions,
            original_images=original_images,  # 关键：传递原始图片给Phase1
            error_code="",
            error_message=""
        )
        
    except requests.RequestException as e:
        logger.error(f"HTTP请求失败: {str(e)}")
        return AuthOutput(
            user_id="",
            token_id="",
            balance=0.0,
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            ozon_client_id=ozon_client_id,
            ozon_api_key=ozon_api_key,
            currency_code="",
            draft=draft,
            source=source,
            extensions=extensions,
            original_images=original_images,  # 关键：传递原始图片
            error_code="AUTH_ERROR",
            error_message=f"HTTP request failed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"认证失败: {str(e)}")
        return AuthOutput(
            user_id="",
            token_id="",
            balance=0.0,
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            ozon_client_id=ozon_client_id,
            ozon_api_key=ozon_api_key,
            currency_code="",
            draft=draft,
            source=source,
            extensions=extensions,
            original_images=original_images,  # 关键：传递原始图片
            error_code="AUTH_ERROR",
            error_message=f"Authentication failed: {str(e)}"
        )