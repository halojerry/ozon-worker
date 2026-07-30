# Phase 2图片生成节点 - 主图 (v4: 使用 image_gen_factory 共享逻辑)
import logging
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state_image_gen import MainImageInput, MainImageOutput
from utils.image_gen_factory import generate_image

logger = logging.getLogger(__name__)

def main_image_gen_node(state: MainImageInput, config: RunnableConfig, runtime: Runtime[Context]) -> MainImageOutput:
    draft = state.draft
    token = state.token
    if not draft or not token:
        return MainImageOutput(main_image=None)
    
    # 多SKU跳过 — 由 variant_primary_loop 处理
    variants = draft.get('variants', []) if isinstance(draft, dict) else []
    if len(variants) > 1:
        logger.info("多SKU(%d个变体)，main_image_gen 跳过", len(variants))
        return MainImageOutput(main_image=None)
    
    # 参考图: Phase1 白底图 + 多角度图
    ref_images: List[str] = []
    for attr in ("white_bg_image", "multi_angle_image"):
        img = getattr(state, attr, None)
        if img and isinstance(img, str) and img.strip():
            ref_images.append(img.strip())
    
    prompt = (
        "Создай главное маркетинговое изображение товара. "
        "Привлекательное, профессиональное, для российского маркетплейса Ozon. "
        "СТРОГО ЗАПРЕЩЕНО: любой текст, буквы, цифры, логотипы, бренды, водяные знаки, "
        "QR-коды, ссылки, телефоны, цены, рекламные надписи, скидки, акции. "
        "Изображение должно быть чистым, без какой-либо текстовой информации."
    )
    image_url = generate_image(token=token, prompt=prompt, ref_images=ref_images if ref_images else None, node_name="main_image_gen")
    return MainImageOutput(main_image=image_url)
