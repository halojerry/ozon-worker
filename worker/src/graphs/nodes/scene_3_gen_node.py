# Phase 2图片生成节点 - 场景图3
import os
import json
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state_image_gen import Scene3Input, Scene3Output
from utils.progress_logger import ProgressLogger  # 导入进度日志助手
from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用

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
    prompt = f"生成产品电商场景图。要求：展示产品在{scene_context_3}场景中的使用效果，温馨氛围，展示产品在特殊场景中的独特应用，吸引消费者兴趣，适合俄罗斯电商平台展示，适合俄罗斯消费者审美。"
    
    # 构建参考图列表：使用Phase1的图片作为参考（内联逻辑）
    ref_images: List[str] = []
    clean_ref = multi_angle_image or white_bg_image  # 选择multi_angle优先，否则white_bg
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
            return Scene3Output(scene_3_image=image_url)
        
        return Scene3Output(scene_3_image=None)
    except Exception as e:
        logger.error(f"Scene 3 image generation failed: {str(e)}")
        return Scene3Output(scene_3_image=None)
