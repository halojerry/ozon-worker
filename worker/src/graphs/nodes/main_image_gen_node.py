# Phase 2图片生成节点 - 主图
import os
import json
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state_image_gen import MainImageInput, MainImageOutput
from utils.progress_logger import ProgressLogger  # 导入进度日志助手
from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用
from utils.mxou_api import clean_title_for_image_prompt

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
    progress.log_node_start("main_image_gen_node", "主图生成节点")
    
    # ✅ v0.11: 多SKU产品由 variant_primary_loop 生成变体主图，main_image_gen 跳过
    variants = draft.get('variants', []) if isinstance(draft, dict) else []
    if len(variants) > 1:
        logger.info("多SKU产品(%d个变体)，main_image_gen 跳过（由 variant_primary_loop 处理）", len(variants))
        return MainImageOutput(main_image=None)
    
    if not draft or not token:
        return MainImageOutput(main_image=None)
    
    # 构建参考图：优先使用Phase1白底图（更干净），其次多角度图，最后回退到原始产品图
    ref_images: List[str] = []
    white_bg = getattr(state, "white_bg_image", None)
    multi_angle = getattr(state, "multi_angle_image", None)
    
    if white_bg and isinstance(white_bg, str) and white_bg.strip():
        ref_images.append(white_bg.strip())
    if multi_angle and isinstance(multi_angle, str) and multi_angle.strip():
        ref_images.append(multi_angle.strip())
    # 如果都没有，回退到原始产品图
    if not ref_images:
        original_images = getattr(state, "original_images", [])
        if isinstance(original_images, list) and len(original_images) > 0:
            ref_images = [str(img) for img in original_images[:2] if isinstance(img, str) and img.strip()]
            logger.info(f"Phase1图片均失败，使用原始产品图作为参考: {len(ref_images)}张")
    
    title = clean_title_for_image_prompt(draft.get("title", ""))
    prompt = f"产品：{title}。生成该产品的电商营销主图。要求：创意营销风格、可包含场景化背景、突出产品卖点、适合Ozon平台商品卡首图展示、高清细节、无其他品牌logo/水印、适合俄罗斯电商平台展示、符合俄罗斯人民审美。"

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
