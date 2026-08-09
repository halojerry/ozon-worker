"""变体主图循环生成节点 - 直接实现生成逻辑（不调用子图，避免循环导入）"""
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from pydantic import BaseModel, Field

from graphs.state import GlobalState, VariantLoopState, VariantLoopOutput, VariantPrimaryLoopOutput

from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用
from utils.mxou_api import clean_title_for_image_prompt
from utils.prompt_assembler import assemble_prompt, merge_visual_vars  # ✅ v0.31: 视觉变量注入（Wave 2: LLM + 确定性合并）
from utils.color_preset import resolve_color_preset  # ✅ v0.32 Wave 2: 配色预设路由
from utils.image_models import get_image_model  # ✅ v0.25: 节点模型路由
from utils.task_image_cache import get_image, save_image, _task_id_from_config  # ✅ v0.26: 重跑不重烧生图


class VariantPrimaryLoopInput(BaseModel):
    """变体主图循环生成节点输入"""
    variants: List[Dict[str, Any]] = Field(default_factory=list, description="变体SKU列表")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（可选，variant_primary_loop_node不依赖此字段）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（可选，variant_primary_loop_node不依赖此字段）")
    draft: Dict[str, Any] = Field(default_factory=dict, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn API Key（用于图片生成）")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")


def variant_primary_loop_node(
    state: VariantPrimaryLoopInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> VariantPrimaryLoopOutput:
    """
    title: 变体主图循环生成
    desc: 直接循环生成所有变体主图（不调用子图，避免循环导入），使用图生图技术
    
    功能：
    - 判断是否有variants（多SKU变体）
    - 如果有variants，直接循环生成所有主图
    - 使用variant.image作为参考图（图生图，支持任意颜色）
    - 实现失败隔离（一张失败不影响其他）
    integrations: api.mxou.cn图片生成API
    """
    ctx = runtime.context
    
    # ✅ Step 1: 获取variants列表
    variants = state.variants
    
    if not variants or len(variants) == 0:
        # 如果没有variants，返回空列表
        logging.warning("[variant_primary_loop_node] 无variants数据")
        return VariantPrimaryLoopOutput(
            variant_primary_images=[],
            stages={"variant_primary_loop": "无variants，跳过"}
        )
    
    logging.info(f"[variant_primary_loop_node] 开始生成{len(variants)}张变体主图")
    
    # ✅ Step 2: 并发生成所有变体主图
    # ⚠️ v0.14 B4: 串行 → ThreadPoolExecutor(4) 并行（39 变体从小时级降到分钟级，不改图数量）
    # 配合 B3 全局限流器（mxou_acquire）防打爆 450 RPM；pool.map 保持结果顺序与 variants 一致
    variant_primary_images: List[str] = []

    # ✅ Fallback参考图：当variant.image为空时，使用白底图或多角度图作为参考
    fallback_ref: str = state.white_bg_image or state.multi_angle_image or ""

    def _gen_one(idx: int, variant: Dict[str, Any]) -> str:
        """生成单个变体主图（线程内执行，失败隔离）"""
        try:
            sku_name = variant.get("name", f"variant_{idx}")
            sku_image_url = variant.get("image", "")

            # ✅ v0.26: 重跑不重烧生图 — 该变体已生成过 → 直接复用
            _tid = _task_id_from_config(config)
            if _tid:
                cached = get_image(_tid, f"variant_{idx}")
                if cached:
                    logging.info(f"[variant_primary_loop_node] variant[{idx}] 命中生图缓存，复用")
                    return cached

            # ✅ 修复：variant.image为空时，使用fallback参考图（白底图/多角度图）
            if not sku_image_url or not isinstance(sku_image_url, str) or not sku_image_url.strip():
                if fallback_ref:
                    logging.info(f"[variant_primary_loop_node] variant[{idx}]缺少image，使用fallback参考图: {fallback_ref[:80]}")
                    sku_image_url = fallback_ref
                else:
                    logging.warning(f"[variant_primary_loop_node] variant[{idx}]缺少image且无fallback，跳过")
                    return ""

            logging.info(f"[variant_primary_loop_node] 正在生成variant[{idx}]: {sku_name}")

            # ✅ 直接用1688原始URL（mxou可直接访问）
            ref_images = [sku_image_url]

            # 产品标题（所有变体共用同一标题，注入到生图 prompt）
            title = clean_title_for_image_prompt(
                state.draft.get("title", "") if isinstance(state.draft, dict) else ""
            )
            # ⚠️ v0.31+Wave 2: 提示词走 prompt_assembler（state.draft 提取 低优先 + state.visual_vars LLM 高优先 + 配色预设）
            _vv = merge_visual_vars(
                state.draft if isinstance(state.draft, dict) else {},
                getattr(state, "visual_vars", None),
            )
            _cp = resolve_color_preset(
                (state.draft or {}).get("category", "") if isinstance(state.draft, dict) else ""
            )

            # ✅ 调用统一mxou API（正确参数: images/aspectRatio/replyType）
            # ⚠️ v0.15: 提示词外置 config/image_prompts.json（热加载，改文件即生效，无需重建镜像）
            image_url = call_mxou_image_api(
                model=get_image_model("variant_white_bg"),
                token=state.token,
                prompt=assemble_prompt("variant_white_bg", title=title, **_vv, color_preset=_cp),
                ref_images=ref_images,
                aspect_ratio="3:4",
                timeout=180,  # ⚠️ v0.26: 90→180 匹配 grsai 30s+5s 轮询节奏，减少假超时
                max_retries=1  # ⚠️ v0.26: 3→1，violation/failed 有界重试，防变体图无限重烧
            )

            if image_url and isinstance(image_url, str) and image_url:
                logging.info(f"[variant_primary_loop_node] variant[{idx}]生成成功: {image_url[:100]}")
                if _tid:
                    save_image(_tid, f"variant_{idx}", image_url)
                return image_url
            logging.error(f"[variant_primary_loop_node] variant[{idx}]生成失败: API未返回有效URL")
            return ""  # 不写 alicdn URL，留空让上层处理

        except Exception as e:
            logging.error(f"[variant_primary_loop_node] variant[{idx}]生成失败: {e}")
            return ""  # 不写 alicdn URL，留空让上层处理

    with ThreadPoolExecutor(max_workers=4) as pool:
        variant_primary_images = list(pool.map(_gen_one, range(len(variants)), variants))
    
    # ✅ Step 3: 返回结果
    success_count = sum(1 for img in variant_primary_images if img)
    logging.info(f"[variant_primary_loop_node] 完成：成功{success_count}/{len(variants)}张")
    
    return VariantPrimaryLoopOutput(
        variant_primary_images=variant_primary_images,
        stages={"variant_primary_loop": f"生成{success_count}/{len(variants)}张主图"}
    )
