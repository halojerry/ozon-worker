# Phase 2图片生成节点 - 多信息图
import os
import json
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state_image_gen import MultiInfoInput, MultiInfoOutput
from utils.progress_logger import ProgressLogger  # 导入进度日志助手
from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用
from utils.mxou_api import clean_title_for_image_prompt

logger = logging.getLogger(__name__)

def multi_info_gen_node(state: MultiInfoInput, config: RunnableConfig, runtime: Runtime[Context]) -> MultiInfoOutput:
    """
    title: 多信息图生成
    desc: 生成多信息展示图（Phase2）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    draft = state.draft
    token = state.token
    
    # 初始化进度日志助手
    progress = ProgressLogger()
    
    # 记录节点开始（含进度百分比）
    progress.log_node_start("multi_info_gen_node", "多信息图生成节点")
    
    # 内联逻辑：直接从Phase1图片中选择参考图
    clean_ref = state.multi_angle_image or state.white_bg_image
    
    if not draft or not token:
        return MultiInfoOutput(multi_info_image=None)
    
    title = clean_title_for_image_prompt(draft.get("title", ""))
    prompt = f"产品：{title}。生成该产品的俄语信息展示图。要求：展示产品核心卖点/参数/使用说明（俄语文字），信息布局清晰，适合俄罗斯电商平台Ozon商品详情页使用。产品必须与参考图一致，不得生成其他产品。"
    
    # 构建参考图列表：使用Phase1的图片作为参考（内联逻辑）
    ref_images: List[str] = []
    if clean_ref and isinstance(clean_ref, str) and clean_ref:
        ref_images.append(clean_ref)
    
    # ✅ Phase1失败时不回退到原始1688图片（避免广告内容：返利/抽奖/QR码等）
    # 当Phase1白底图/多角度图缺失时，跳过Phase2生成（返回None），不使用含广告的原图
    
    
    try:
        # ✅ 调用统一mxou API（正确参数: images/aspectRatio/replyType）
        image_url = call_mxou_image_api(
            token=token,
            prompt=prompt,
            ref_images=ref_images if ref_images else None,
            aspect_ratio="3:4",
            timeout=90,
            max_retries=3
        )
        
        if image_url and isinstance(image_url, str) and image_url:
            return MultiInfoOutput(multi_info_image=image_url)
        
        return MultiInfoOutput(multi_info_image=None)
    except Exception as e:
        logger.error(f"Multi-info image generation failed: {str(e)}")
        return MultiInfoOutput(multi_info_image=None)
