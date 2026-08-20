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
from utils.prompt_assembler import assemble_prompt, merge_visual_vars  # ✅ v0.31: 视觉变量注入（Wave 2: LLM + 确定性合并）
from utils.color_preset import resolve_color_preset  # ✅ v0.32 Wave 2: 配色预设路由
from utils.image_models import get_image_model  # ✅ v0.25: 节点模型路由
from utils.task_image_cache import get_image, save_image, _task_id_from_config, _force_regen_from_config, _regen_version_from_config  # v0.26/v0.41: 重跑不重烧生图 + 版本化
from utils.image_gen_plan import slot_enabled  # T7b: image_gen_plan 前置条件（plan 无该 slot → 跳过）

logger = logging.getLogger(__name__)

def social_proof_gen_node(state: SocialProofInput, config: RunnableConfig, runtime: Runtime[Context]) -> SocialProofOutput:
    """
    title: 社交证明图生成
    desc: 生成社交证明展示图（Phase2）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    # ✅ T7b: image_gen_plan 前置条件（plan 无 social_proof → 直接跳过，不调生图 API）
    if not slot_enabled(config, "social_proof", state):
        logger.info("image_gen_plan 未选择 social_proof，跳过生图")
        return SocialProofOutput(social_proof_image=None)
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
    
    # ✅ v0.26/v0.41: 重跑不重烧生图 + force_regen 绕过缓存读（webui 重生成）
    _tid = _task_id_from_config(config)
    if _tid and not _force_regen_from_config(config):
        cached = get_image(_tid, "social_proof")
        if cached:
            logger.info("命中任务生图缓存(social_proof)，复用已有图片，跳过生图")
            return SocialProofOutput(social_proof_image=cached)
    
    title = clean_title_for_image_prompt(draft.get("title", ""))
    # ⚠️ v0.31+Wave 2: 确定性 extract 低优先 + state.visual_vars LLM 高优先 + 配色预设
    _vv = merge_visual_vars(draft or {}, getattr(state, "visual_vars", None), getattr(state, "category_name", ""))
    _cp = resolve_color_preset((draft or {}).get("category", ""))
    prompt = assemble_prompt("social_proof", title=title, **_vv, color_preset=_cp)
    
    # 构建参考图列表：使用Phase1的图片作为参考（内联逻辑）
    ref_images: List[str] = []
    clean_ref = white_bg_image or multi_angle_image  # v0.40: 白底图优先（颜色最纯一致，多角度图角度色差）
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
            model=get_image_model("social_proof"),
            token=token,
            prompt=prompt,
            ref_images=ref_images if ref_images else None,
            aspect_ratio="3:4",
            timeout=180,
            max_retries=1
        )
        
        if image_url and isinstance(image_url, str) and image_url:
            if _tid:
                save_image(_tid, "social_proof", image_url,
                           version=_regen_version_from_config(config),
                           params=state.model_dump())
            return SocialProofOutput(social_proof_image=image_url)
        
        # ⚠️ v0.40: 主模型(gpt-image-2)失败/卡轮询超时 → 模型降级重新生成
        # （social_proof 是画廊首张候选，缺失影响主图兜底链）。
        # v0.60 三级降级：gpt-image-2 → nano-banana-fast → nano-banana-2-lite（120s/级）
        logger.warning("⚠️ social_proof_gen: 主模型 gpt-image-2 失败/超时，降级重试")
        for _fb_model in ("nano-banana-fast", "nano-banana-2-lite"):
            try:
                image_url = call_mxou_image_api(
                    model=_fb_model,
                    token=token,
                    prompt=prompt,
                    ref_images=ref_images if ref_images else None,
                    aspect_ratio="3:4",
                    timeout=120,
                    max_retries=2
                )
            except Exception as _fb_exc:
                logger.error(f"social_proof 降级生成失败({_fb_model}): {_fb_exc}")
                image_url = None
            if image_url and isinstance(image_url, str) and image_url:
                break
        if image_url and isinstance(image_url, str) and image_url:
            if _tid:
                save_image(_tid, "social_proof", image_url,
                           version=_regen_version_from_config(config),
                           params=state.model_dump())
            return SocialProofOutput(social_proof_image=image_url)
        return SocialProofOutput(social_proof_image=None)
    except Exception as e:
        logger.error(f"Social proof image generation failed: {str(e)}")
        return SocialProofOutput(social_proof_image=None)
