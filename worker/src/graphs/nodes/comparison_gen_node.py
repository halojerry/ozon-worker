# Phase 2图片生成节点 - 对比图 (v4: 使用 image_gen_factory 共享逻辑)
import logging
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

from graphs.state_image_gen import ComparisonInput, ComparisonOutput
from utils.image_gen_factory import generate_image, build_phase2_refs, make_prompt

logger = logging.getLogger(__name__)

def comparison_gen_node(state: ComparisonInput, config: RunnableConfig, runtime: Runtime[Context]) -> ComparisonOutput:
    draft = state.draft
    token = state.token
    if not draft or not token:
        return ComparisonOutput(comparison_image=None)

    prompt = (
        "Создай изображение для сравнения товаров. "
        "Покажи преимущества продукта, привлекающее покупателей. "
        "Высокое качество, для российского маркетплейса Ozon. "
        "СТРОГО ЗАПРЕЩЕНО: любой текст, буквы, цифры, логотипы, бренды, водяные знаки, "
        "QR-коды, ссылки, телефоны, цены, рекламные надписи, скидки, акции."
    )
    ref_images = build_phase2_refs(state)
    image_url = generate_image(token=token, prompt=prompt, ref_images=ref_images, node_name="comparison_gen")
    return ComparisonOutput(comparison_image=image_url)
