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
from utils.image_prompts import get_image_prompt  # ✅ v0.15: 提示词外置配置（热加载）
from utils.image_models import get_image_model  # ✅ v0.25: 节点模型路由
from utils.task_image_cache import get_image, save_image, _task_id_from_config  # ✅ v0.26: 重跑不重烧生图

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

    # ⚠️ v0.14 B5: 连原始图都没有 → 跳过生图（避免无参考随机主图）
    if not ref_images:
        logger.warning("⚠️ main_image_gen: 无任何参考图（Phase1失败且无原始图），跳过主图生成")
        return MainImageOutput(main_image=None)

    # ✅ v0.26: 重跑不重烧生图 — 同一任务已生成过 → 直接复用
    _tid = _task_id_from_config(config)
    if _tid:
        cached = get_image(_tid, "main")
        if cached:
            logger.info("命中任务生图缓存(main)，复用已有图片，跳过生图")
            return MainImageOutput(main_image=cached)
    
    title = clean_title_for_image_prompt(draft.get("title", ""))
    # ⚠️ v0.15: 提示词外置 config/image_prompts.json（热加载，改文件即生效，无需重建镜像）
    prompt = get_image_prompt("main", title=title)

    try:
        # ✅ 调用统一mxou API（正确参数: images/aspectRatio/replyType）
        image_url = call_mxou_image_api(
            model=get_image_model("main"),
            token=token,
            prompt=prompt,
            ref_images=ref_images if ref_images else None,
            aspect_ratio="3:4",
            timeout=180,
            max_retries=2
        )
        
        if image_url and isinstance(image_url, str) and image_url:
            if _tid:
                save_image(_tid, "main", image_url)
            return MainImageOutput(main_image=image_url)
        
        return MainImageOutput(main_image=None)
    except Exception as e:
        logger.error(f"Main image generation failed: {str(e)}")
        return MainImageOutput(main_image=None)
