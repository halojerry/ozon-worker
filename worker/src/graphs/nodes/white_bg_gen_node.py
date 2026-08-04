# Phase 1图片生成节点 - 白底图
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

from graphs.state_image_gen import WhiteBgInput, WhiteBgOutput
from utils.image_quality_evaluator import evaluate_image_quality  # ✅ 关键：导入图片质量评估函数
from utils.progress_logger import ProgressLogger  # ✅ 导入进度日志助手
from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用
from utils.mxou_api import clean_title_for_image_prompt
from utils.image_prompts import get_image_prompt  # ✅ v0.15: 提示词外置配置（热加载）
from utils.image_models import get_image_model  # ✅ v0.25: 节点模型路由

logger = logging.getLogger(__name__)

def white_bg_gen_node(state: WhiteBgInput, config: RunnableConfig, runtime: Runtime[Context]) -> WhiteBgOutput:
    """
    title: 白底图生成
    desc: 生成产品白底展示图（Phase1）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    
    # 初始化进度日志助手
    run_id = config.get('metadata', {}).get('execute_id', 'unknown')
    progress = ProgressLogger(run_id)
    
    # 记录节点开始（含进度百分比）
    progress.log_node_start("white_bg_gen")
    
    # 获取产品信息
    draft = state.draft
    token = state.token
    original_images = state.original_images  # 原始产品图片（参考图）
    
    if not draft or draft == {}:
        progress.log_node_error("Draft数据为空", "检查上游数据摄入节点")
        return WhiteBgOutput(white_bg_image=None)
    
    if not token:
        progress.log_node_error("Token为空", "检查认证节点")
        return WhiteBgOutput(white_bg_image=None)
    
    # 构建生图提示词（中文）
    title = clean_title_for_image_prompt(draft.get("title", ""))
    # ⚠️ v0.15: 提示词外置 config/image_prompts.json（热加载，改文件即生效，无需重建镜像）
    prompt = get_image_prompt("white_bg", title=title)
    
    # 记录具体动作
    progress.log_node_action("正在构建图片生成请求...")
    
    try:
        # 构建参考图列表
        ref_images: List[str] = []
        if isinstance(original_images, list) and len(original_images) > 0:
            # 如果有足够多的原始图片，评估前5张的质量
            if len(original_images) >= 5:
                progress.log_node_action("原始图片数量≥5，启动智能质量评估...")
                
                # 评估前5张图片的质量
                evaluated_images = evaluate_image_quality(original_images[:5], max_evaluation_count=5)
                
                # 选择前2张高质量图片作为产品主图
                if len(evaluated_images) >= 2:
                    ref_images = [img['url'] for img in evaluated_images[:2]]
                    progress.log_node_action(f"智能选择高质量产品主图（按优先级排序）：")
                    for i, img in enumerate(evaluated_images[:2], 1):
                        progress.log_node_action(f"  {i}. priority={img['priority']}, size={img['size']}, url={img['url'][:100]}")
                else:
                    # 如果评估失败，回退到默认逻辑（使用前2张）
                    ref_images = [str(img) for img in original_images[:2] if isinstance(img, str)]
                    progress.log_node_error("图片质量评估失败，回退到默认逻辑（使用前2张）", "检查图片URL是否可访问")
            else:
                # 如果原始图片少于5张，直接使用前2张（无需评估）
                ref_images = [str(img) for img in original_images[:2] if isinstance(img, str)]
                progress.log_node_action("原始图片数量<5，直接使用前2张（无需质量评估）")
            
            # 预处理参考图：1688图片→S3签名URL
            if len(ref_images) > 0:
                progress.log_node_action(f"使用{len(ref_images)}张参考图（已预处理为S3 URL）")
        
        # ✅ 调用统一mxou API（正确参数: images/aspectRatio/replyType）
        progress.log_node_action("正在调用图片生成API...（timeout=180s）")
        image_url = call_mxou_image_api(
            model=get_image_model("white_bg"),
            token=token,
            prompt=prompt,
            ref_images=ref_images if ref_images else None,
            aspect_ratio="3:4",
            timeout=180,
            max_retries=2
        )
        
        if image_url and isinstance(image_url, str) and image_url:
            progress.log_node_success(f"白底图生成成功：图片URL长度={len(image_url)}字符")
            return WhiteBgOutput(white_bg_image=image_url)
        
        progress.log_node_error("API未返回有效图片URL", "检查API响应和参数")
        return WhiteBgOutput(white_bg_image=None)
        
    except Exception as e:
        progress.log_node_error(f"白底图生成异常: {str(e)}", "检查异常详情和网络连接")
        return WhiteBgOutput(white_bg_image=None)
