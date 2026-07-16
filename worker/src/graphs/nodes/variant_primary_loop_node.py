"""变体主图循环生成节点 - 直接实现生成逻辑（不调用子图，避免循环导入）"""
import os
import logging
from typing import Dict, Any, List, Optional
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from pydantic import BaseModel, Field

from graphs.state import GlobalState, VariantLoopState, VariantLoopOutput, VariantPrimaryLoopOutput

from utils.mxou_api import call_mxou_image_api  # ✅ 统一mxou API调用


class VariantPrimaryLoopInput(BaseModel):
    """变体主图循环生成节点输入"""
    variants: List[Dict[str, Any]] = Field(default_factory=list, description="变体SKU列表")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（可选，variant_primary_loop_node不依赖此字段）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（可选，variant_primary_loop_node不依赖此字段）")
    draft: Dict[str, Any] = Field(default_factory=dict, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn API Key（用于图片生成）")


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
    
    # ✅ Step 2: 循环生成所有变体主图
    variant_primary_images: List[str] = []
    
    # ✅ Fallback参考图：当variant.image为空时，使用白底图或多角度图作为参考
    fallback_ref: str = state.white_bg_image or state.multi_angle_image or ""
    
    for idx, variant in enumerate(variants):
        try:
            # 获取variant的SKU名称和图片URL
            sku_name = variant.get("name", f"variant_{idx}")
            sku_image_url = variant.get("image", "")
            
            # ✅ 修复：variant.image为空时，使用fallback参考图（白底图/多角度图）
            if not sku_image_url or not isinstance(sku_image_url, str) or not sku_image_url.strip():
                if fallback_ref:
                    logging.info(f"[variant_primary_loop_node] variant[{idx}]缺少image，使用fallback参考图: {fallback_ref[:80]}")
                    sku_image_url = fallback_ref
                else:
                    logging.warning(f"[variant_primary_loop_node] variant[{idx}]缺少image且无fallback，跳过")
                    variant_primary_images.append("")
                    continue
            
            logging.info(f"[variant_primary_loop_node] 正在生成variant[{idx}]: {sku_name}")
            
            # ✅ 直接用1688原始URL（mxou可直接访问）
            ref_images = [sku_image_url]
            
            # ✅ 调用统一mxou API（正确参数: images/aspectRatio/replyType）
            image_url = call_mxou_image_api(
                token=state.token,
                prompt="去除产品背景，生成纯白底图：要求：纯白背景（#FFFFFF），无文字水印，专业产品摄影风格。",
                ref_images=ref_images,
                aspect_ratio="3:4",
                timeout=90,
                max_retries=3
            )
            
            if image_url and isinstance(image_url, str) and image_url:
                variant_primary_images.append(image_url)
                logging.info(f"[variant_primary_loop_node] variant[{idx}]生成成功: {image_url[:100]}")
            else:
                logging.error(f"[variant_primary_loop_node] variant[{idx}]生成失败: API未返回有效URL")
                variant_primary_images.append("")  # 不写 alicdn URL，留空让上层处理
            
        except Exception as e:
            logging.error(f"[variant_primary_loop_node] variant[{idx}]生成失败: {e}")
            variant_primary_images.append("")  # 不写 alicdn URL，留空让上层处理
    
    # ✅ Step 3: 返回结果
    success_count = sum(1 for img in variant_primary_images if img)
    logging.info(f"[variant_primary_loop_node] 完成：成功{success_count}/{len(variants)}张")
    
    return VariantPrimaryLoopOutput(
        variant_primary_images=variant_primary_images,
        stages={"variant_primary_loop": f"生成{success_count}/{len(variants)}张主图"}
    )
