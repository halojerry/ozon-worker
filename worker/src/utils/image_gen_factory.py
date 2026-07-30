"""图片生成工厂 — v4 参数化 8 个 Phase2 生图节点的通用逻辑"""

import logging
from typing import Dict, Any, Optional, List, Callable

from utils.progress_logger import ProgressLogger
from utils.mxou_api import call_mxou_image_api, clean_title_for_image_prompt

logger = logging.getLogger(__name__)


def generate_image(
    *,
    token: str,
    prompt: str,
    ref_images: Optional[List[str]] = None,
    node_name: str = "image_gen",
    aspect_ratio: str = "3:4",
    timeout: int = 90,
    max_retries: int = 3,
) -> Optional[str]:
    """
    通用图片生成 — 封装 MXOU API 调用 + 进度日志 + 错误处理。

    Args:
        token: MXOU API Key
        prompt: 生图提示词
        ref_images: 参考图 URL 列表
        node_name: 节点名（日志用）
        aspect_ratio: 图片宽高比
        timeout: API 超时
        max_retries: 最大重试

    Returns:
        图片 URL，失败返回 None
    """
    progress = ProgressLogger()
    progress.log_node_start(node_name)
    
    if not token:
        logger.warning(f"{node_name}: token 为空，跳过")
        return None
    
    try:
        image_url = call_mxou_image_api(
            token=token,
            prompt=prompt,
            ref_images=ref_images if ref_images else None,
            aspect_ratio=aspect_ratio,
            timeout=timeout,
            max_retries=max_retries,
        )
        if image_url and isinstance(image_url, str) and image_url.strip():
            logger.info(f"✅ {node_name}: {image_url[:60]}...")
            return image_url
        logger.warning(f"⚠️ {node_name}: API 返回空")
        return None
    except Exception as e:
        logger.error(f"❌ {node_name}: {e}")
        return None


def build_phase1_refs(state) -> List[str]:
    """Phase1 参考图：1688 原始图"""
    refs: List[str] = []
    orig = getattr(state, 'original_images', None) or []
    if isinstance(orig, list):
        for u in orig[:3]:
            if isinstance(u, str) and u.startswith("http"):
                refs.append(u)
    return refs


def build_phase2_refs(state) -> List[str]:
    """Phase2 参考图：Phase1 白底图或多角度图"""
    refs: List[str] = []
    multi = getattr(state, 'multi_angle_image', None)
    white = getattr(state, 'white_bg_image', None)
    clean = multi or white
    if clean and isinstance(clean, str) and clean.startswith("http"):
        refs.append(clean)
    return refs


def make_prompt(product_name: str, desc_text: str, scene_context: str = "") -> str:
    """
    构建生图 prompt — 清洗标题，注入场景描述。

    Args:
        product_name: 产品名（会被清洗）
        desc_text: 描述文本（会截断到 200 字符）
        scene_context: LLM 生成的场景描述（scene_1/2/3 专用）

    Returns:
        格式化的 prompt 字符串
    """
    clean_name = clean_title_for_image_prompt(product_name or "товар")
    desc_short = (desc_text or "")[:200]
    
    if scene_context:
        return (
            f"Создай изображение товара в сцене использования. "
            f"Товар: {clean_name}. Сцена: {scene_context}. "
            f"Высокое качество, реалистичный стиль, для российского маркетплейса. "
            f"СТРОГО ЗАПРЕЩЕНО: любой текст, буквы, цифры, логотипы, бренды, водяные знаки, "
            f"QR-коды, ссылки, телефоны, цены, рекламные надписи, скидки, акции. "
            f"Изображение должно быть чистым, без какой-либо текстовой информации."
        )
    
    return (
        f"Создай изображение товара '{clean_name}'. "
        f"Описание: {desc_short}. "
        f"Высокое качество, реалистичный стиль, для российского маркетплейса Ozon. "
        f"СТРОГО ЗАПРЕЩЕНО: любой текст, буквы, цифры, логотипы, бренды, водяные знаки, "
        f"QR-коды, ссылки, телефоны, цены, рекламные надписи, скидки, акции. "
        f"Изображение должно быть чистым, без какой-либо текстовой информации."
    )
