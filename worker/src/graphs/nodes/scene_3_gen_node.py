# Phase 2图片生成节点 - 场景图3
import os
import json
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state_image_gen import Scene3Input, Scene3Output
from utils.progress_logger import ProgressLogger  # 导入进度日志助手
from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用
from utils.image_prompts import get_image_prompt  # ✅ v0.15: 提示词外置配置（热加载）
from utils.image_models import get_image_model  # ✅ v0.25: 节点模型路由

logger = logging.getLogger(__name__)

def scene_3_gen_node(state: Scene3Input, config: RunnableConfig, runtime: Runtime[Context]) -> Scene3Output:
    """
    title: 场景图3生成
    desc: 生成产品使用场景图3（Phase2）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    draft = state.draft
    token = state.token
    
    # 初始化进度日志助手
    progress = ProgressLogger()
    
    # 记录节点开始（含进度百分比）
    progress.log_node_start("scene_3_gen_node", "场景图3生成节点")
    # 从Phase1获取参考图（内联逻辑：选择multi_angle或white_bg）
    multi_angle_image = state.multi_angle_image
    white_bg_image = state.white_bg_image
    
    if not draft or not token:
        return Scene3Output(scene_3_image=None)
    
    scene_context_3 = state.scene_context_3 or '工作办公场景'  # ✅ 优先使用LLM生成的场景，兜底使用默认值
    # ⚠️ v0.15: 提示词外置 config/image_prompts.json（热加载，改文件即生效，无需重建镜像）
    prompt = get_image_prompt("scene_3", scene_context=scene_context_3)
    
    # 构建参考图列表：使用Phase1的图片作为参考（内联逻辑）
    ref_images: List[str] = []
    clean_ref = multi_angle_image or white_bg_image  # 选择multi_angle优先，否则white_bg
    if clean_ref and isinstance(clean_ref, str) and clean_ref:
        ref_images.append(clean_ref)
    
    # ✅ Phase1失败时不回退到原始1688图片（避免广告内容：返利/抽奖/QR码等）
    # 当Phase1白底图/多角度图缺失时，跳过Phase2生成（返回None），不使用含广告的原图
    # ⚠️ v0.14 B5: 空参考图直接跳过生图
    if not ref_images:
        logger.warning("⚠️ scene_3_gen: Phase1 参考图缺失，跳过生图（避免无参考随机图）")
        return Scene3Output(scene_3_image=None)

    try:
        # ✅ 调用统一mxou API（正确参数: images/aspectRatio/replyType）
        image_url = call_mxou_image_api(
            model=get_image_model("scene_3"),
            token=token,
            prompt=prompt,
            ref_images=ref_images if ref_images else None,
            aspect_ratio="3:4",
            timeout=180,
            max_retries=2
        )
        
        if image_url and isinstance(image_url, str) and image_url:
            return Scene3Output(scene_3_image=image_url)
        
        return Scene3Output(scene_3_image=None)
    except Exception as e:
        logger.error(f"Scene 3 image generation failed: {str(e)}")
        return Scene3Output(scene_3_image=None)
