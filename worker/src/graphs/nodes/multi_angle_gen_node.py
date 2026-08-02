# Phase 1图片生成节点 - 多角度展示图
import os
import json
import time
import requests
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state_image_gen import MultiAngleInput, MultiAngleOutput
from utils.image_quality_evaluator import evaluate_image_quality  # ✅ 关键：导入图片质量评估函数
from utils.progress_logger import ProgressLogger  # 导入进度日志助手
from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用
from utils.mxou_api import clean_title_for_image_prompt

logger = logging.getLogger(__name__)

def multi_angle_gen_node(state: MultiAngleInput, config: RunnableConfig, runtime: Runtime[Context]) -> MultiAngleOutput:
    """
    title: 多角度展示图生成
    desc: 生成产品多角度展示图（Phase1）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    draft = state.draft
    token = state.token
    original_images = state.original_images  # 原始产品图片（参考图）
    
    # 初始化进度日志助手
    progress = ProgressLogger()
    
    # 记录节点开始（含进度百分比）
    progress.log_node_start("multi_angle_gen_node", "多角度展示图生成节点")
    
    if not draft or not token:
        logger.warning("Missing draft or token for multi_angle_gen")
        return MultiAngleOutput(multi_angle_image=None)
    
    # 构建生图提示词（中文）
    title = clean_title_for_image_prompt(draft.get("title", ""))
    prompt = f"产品：{title}。生成该产品的多角度实物展示图。严格要求：纯白背景(#FFFFFF)、纯产品摄影、展示产品正面/侧面/背面不同角度、高清细节清晰、无任何文字/标签/参数/logo、无水印、非信息图/非营销海报、专业电商产品摄影风格。"
    
    try:
        # 构建参考图列表
        ref_images: List[str] = []
        if isinstance(original_images, list) and len(original_images) > 0:
            # 如果有足够多的原始图片，评估前5张的质量
            if len(original_images) >= 5:
                logger.info(f"原始图片数量>=5，启动智能质量评估...")
                
                # 评估前5张图片的质量
                evaluated_images = evaluate_image_quality(original_images[:5], max_evaluation_count=5)
                
                # 选择前2张高质量图片作为产品主图
                if len(evaluated_images) >= 2:
                    ref_images = [img['url'] for img in evaluated_images[:2]]
                    logger.info(f"✅ 智能选择高质量产品主图（多角度生成）:")
                    for i, img in enumerate(evaluated_images[:2], 1):
                        logger.info(f"  {i}. priority={img['priority']}, size={img['size']}, url={img['url'][:100]}")
                else:
                    # 如果评估失败，回退到默认逻辑（使用前2张）
                    ref_images = [str(img) for img in original_images[:2] if isinstance(img, str)]
                    logger.warning("图片质量评估失败，回退到默认逻辑（使用前2张）")
            else:
                # 如果原始图片少于5张，直接使用前2张（无需评估）
                ref_images = [str(img) for img in original_images[:2] if isinstance(img, str)]
                logger.info(f"原始图片数量<5，直接使用前2张（无需质量评估）")
            
            # 预处理参考图：1688图片→S3签名URL
            if len(ref_images) > 0:
                logger.info(f"Using {len(ref_images)} reference images (preprocessed to S3 URLs)")
        
        # ✅ 调用统一mxou API（正确参数: images/aspectRatio/replyType）
        logger.info("正在调用图片生成API...（timeout=90s）")
        image_url = call_mxou_image_api(
            token=token,
            prompt=prompt,
            ref_images=ref_images if ref_images else None,
            aspect_ratio="3:4",
            timeout=90,
            max_retries=3
        )
        
        if image_url and isinstance(image_url, str) and image_url:
            logger.info(f"多角度图生成成功：图片URL长度={len(image_url)}字符")
            return MultiAngleOutput(multi_angle_image=image_url)
        
        logger.error("API未返回有效图片URL")
        return MultiAngleOutput(multi_angle_image=None)
    except Exception as e:
        logger.error(f"Multi-angle image generation failed: {str(e)}")
        return MultiAngleOutput(multi_angle_image=None)
