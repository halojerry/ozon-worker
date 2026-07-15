# Phase 2图片生成节点 - 主图
import os
import json
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state_image_gen import MainImageInput, MainImageOutput
from utils.progress_logger import ProgressLogger  # 导入进度日志助手
from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用

logger = logging.getLogger(__name__)

def main_image_gen_node(state: MainImageInput, config: RunnableConfig, runtime: Runtime[Context]) -> MainImageOutput:
    """
    title: 主图生成
    desc: 生成营销主图（Phase2）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    draft = state.draft
    token = state.token
    
    # 初始化进度日志助手
    progress = ProgressLogger()
    
    # 记录节点开始（含进度百分比）
    progress.log_node_start("main_image_gen_node", "主图生成节点")
    
    # ✅ 始终生成主图（主图是产品卡片的首图，与变体SKU图独立）
    # 变体SKU图由variant_primary_loop处理，但产品主图仍需生成
    
    if not draft or not token:
        return MainImageOutput(main_image=None)
    
    # 构建参考图：优先使用Phase1白底图，其次多角度图，最后回退到原始产品图
    ref_images: List[str] = []
    white_bg = getattr(state, "white_bg_image", None)
    multi_angle = getattr(state, "multi_angle_image", None)
    
    if white_bg and isinstance(white_bg, str) and white_bg.strip():
        ref_images.append(white_bg.strip())
    elif multi_angle and isinstance(multi_angle, str) and multi_angle.strip():
        ref_images.append(multi_angle.strip())
    else:
        # Phase1均失败时，回退到原始产品图（经过质量评估选择最佳图片）
        original_images = getattr(state, "original_images", [])
        if isinstance(original_images, list) and len(original_images) > 0:
            ref_images = [str(img) for img in original_images[:2] if isinstance(img, str) and img.strip()]
            logger.info(f"Phase1图片均失败，使用原始产品图作为参考: {len(ref_images)}张")
    
    title = draft.get("title", "")
    prompt = f"产品：{title}。生成该产品的电商主图。严格要求：纯白背景(#FFFFFF)、纯产品摄影、高清细节、无任何文字/标签/参数/logo/水印、非信息图/非营销海报、专业电商产品主图摄影风格、适合Ozon平台展示。"

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
            return MainImageOutput(main_image=image_url)
        
        return MainImageOutput(main_image=None)
    except Exception as e:
        logger.error(f"Main image generation failed: {str(e)}")
        return MainImageOutput(main_image=None)
