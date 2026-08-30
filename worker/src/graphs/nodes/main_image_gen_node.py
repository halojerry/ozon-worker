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
from utils.mxou_api import MxouContentViolationError  # v0.62 R4: 内容违规 → 任务明确失败
from utils.prompt_assembler import assemble_prompt, merge_visual_vars  # ✅ v0.31: 视觉变量注入（Wave 2: LLM + 确定性合并）
from utils.color_preset import resolve_color_preset  # ✅ v0.32 Wave 2: 配色预设路由
from utils.image_models import get_image_model  # ✅ v0.25: 节点模型路由
from utils.task_image_cache import get_image, save_image, _task_id_from_config, _force_regen_from_config, _regen_version_from_config  # v0.26/v0.41: 重跑不重烧生图 + 版本化
from utils.image_gen_plan import slot_enabled  # T7b: image_gen_plan 前置条件（plan 无该 slot → 跳过）

logger = logging.getLogger(__name__)

def main_image_gen_node(state: MainImageInput, config: RunnableConfig, runtime: Runtime[Context]) -> MainImageOutput:
    """
    title: 主图生成
    desc: 生成营销主图（Phase2）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    # ✅ T7b: image_gen_plan 前置条件（plan 无 main_image → 直接跳过，不调生图 API）
    if not slot_enabled(config, "main_image", state):
        logger.info("image_gen_plan 未选择 main_image，跳过生图")
        return MainImageOutput(main_image=None)
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
    # ⚠️ v0.40: 只用白底图（颜色最纯）——多角度图角度色差 + gpt-image-2 创意漂移
    # 导致主图颜色与其他图不一致（用户实测）。单参考图 + prompt 保真约束更稳。
    ref_images: List[str] = []
    white_bg = getattr(state, "white_bg_image", None)
    multi_angle = getattr(state, "multi_angle_image", None)
    
    if white_bg and isinstance(white_bg, str) and white_bg.strip():
        ref_images.append(white_bg.strip())
    # v0.40: multi_angle 不再作为第二参考图（避免两参考图色差综合漂移）；
    # 仅白底图缺失时兜底
    elif multi_angle and isinstance(multi_angle, str) and multi_angle.strip():
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

    # ✅ v0.26/v0.41: 重跑不重烧生图 + force_regen 绕过缓存读（webui 重生成）
    _tid = _task_id_from_config(config)
    if _tid and not _force_regen_from_config(config):
        cached = get_image(_tid, "main")
        if cached:
            logger.info("命中任务生图缓存(main)，复用已有图片，跳过生图")
            return MainImageOutput(main_image=cached)
    
    title = clean_title_for_image_prompt(draft.get("title", ""))
    # ⚠️ v0.31+Wave 2: 提示词走 prompt_assembler（确定性 extract 低优先 + state.visual_vars LLM 高优先 + 配色预设）
    _vv = merge_visual_vars(draft or {}, getattr(state, "visual_vars", None), getattr(state, "category_name", ""))
    _cp = resolve_color_preset((draft or {}).get("category", ""))
    prompt = assemble_prompt("main", title=title, **_vv, color_preset=_cp)

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
        
        # ⚠️ v0.40: 主模型(gpt-image-2)失败/卡轮询超时 → 模型降级重新生成
        # （不是用别的图顶替——主图必须真正生成）。API 内部对轮询超时不降级
        # （防双倍计费），但主图是关键图，宁可承担双倍计费风险也要保证有主图。
        # v0.60 三级降级：gpt-image-2 → nano-banana-fast → nano-banana-2-lite（120s/级）
        if not (image_url and isinstance(image_url, str) and image_url):
            for _fb_model in ("nano-banana-fast", "nano-banana-2-lite"):
                logger.warning("⚠️ main_image_gen: 主模型 gpt-image-2 失败/超时，降级 %s 重新生成", _fb_model)
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
                except MxouContentViolationError:
                    raise  # v0.62 R4: 内容违规不降级（降级模型同 prompt 同样违规）
                except Exception as _fb_exc:
                    logger.error(f"主图降级生成失败({_fb_model}): {_fb_exc}")
                    image_url = None
                if image_url and isinstance(image_url, str) and image_url:
                    break

        if image_url and isinstance(image_url, str) and image_url:
            if _tid:
                save_image(_tid, "main", image_url,
                           version=_regen_version_from_config(config),
                           params=state.model_dump())
            return MainImageOutput(main_image=image_url)
        
        return MainImageOutput(main_image=None)
    except MxouContentViolationError:
        raise  # v0.62 R4: 内容违规 → 任务失败，写入「图片内容违规，请调整商品图片/标题」
    except Exception as e:
        logger.error(f"Main image generation failed: {str(e)}")
        return MainImageOutput(main_image=None)
