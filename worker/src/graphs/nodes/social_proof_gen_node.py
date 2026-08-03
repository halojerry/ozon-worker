# Phase 2图片生成节点 - 社交证明图
import os
import json
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state_image_gen import SocialProofInput, SocialProofOutput
from utils.progress_logger import ProgressLogger  # 导入进度日志助手
from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用
from utils.mxou_api import clean_title_for_image_prompt
from utils.image_prompts import get_image_prompt  # ✅ v0.15: 提示词外置配置（热加载）

logger = logging.getLogger(__name__)

def social_proof_gen_node(state: SocialProofInput, config: RunnableConfig, runtime: Runtime[Context]) -> SocialProofOutput:
    """
    title: 社交证明图生成
    desc: 生成社交证明展示图（Phase2）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    draft = state.draft
    token = state.token
    
    # 初始化进度日志助手
    progress = ProgressLogger()
    
    # 记录节点开始（含进度百分比）
    progress.log_node_start("social_proof_gen_node", "社交证明图生成节点")
    # 从Phase1获取参考图（内联逻辑：选择multi_angle或white_bg）
    multi_angle_image = state.multi_angle_image
    white_bg_image = state.white_bg_image
    
    if not draft or not token:
        return SocialProofOutput(social_proof_image=None)
    
    title = clean_title_for_image_prompt(draft.get("title", ""))
    # ⚠️ v0.15: 提示词外置 config/image_prompts.json（热加载，改文件即生效，无需重建镜像）
    prompt = get_image_prompt("social_proof")
    
    # 构建参考图列表：使用Phase1的图片作为参考（内联逻辑）
    ref_images: List[str] = []
    clean_ref = multi_angle_image or white_bg_image  # 选择multi_angle优先，否则white_bg
    if clean_ref and isinstance(clean_ref, str) and clean_ref:
        ref_images.append(clean_ref)
    
    # ✅ Phase1失败时不回退到原始1688图片（避免广告内容：返利/抽奖/QR码等）
    # 当Phase1白底图/多角度图缺失时，跳过Phase2生成（返回None），不使用含广告的原图
    # ⚠️ v0.14 B5: 空参考图直接跳过生图
    if not ref_images:
        logger.warning("⚠️ social_proof_gen: Phase1 参考图缺失，跳过生图（避免无参考随机图）")
        return SocialProofOutput(social_proof_image=None)

    try:
        # ✅ 调用统一mxou API（正确参数: images/aspectRatio/replyType）
        image_url = call_mxou_image_api(
            token=token,
            prompt=prompt,
            ref_images=ref_images if ref_images else None,
            aspect_ratio="3:4",
            timeout=180,
            max_retries=2
        )
        
        if image_url and isinstance(image_url, str) and image_url:
            return SocialProofOutput(social_proof_image=image_url)
        
        return SocialProofOutput(social_proof_image=None)
    except Exception as e:
        logger.error(f"Social proof image generation failed: {str(e)}")
        return SocialProofOutput(social_proof_image=None)
