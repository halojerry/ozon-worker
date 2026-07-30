# Phase 2图片生成节点 - 详情图 (v4: 使用 image_gen_factory 共享逻辑)
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state_image_gen import DetailImageInput, DetailImageOutput
from utils.image_gen_factory import generate_image, build_phase2_refs

def detail_gen_node(state: DetailImageInput, config: RunnableConfig, runtime: Runtime[Context]) -> DetailImageOutput:
    if not state.draft or not state.token:
        return DetailImageOutput(detail_image=None)
    prompt = (
        "Создай изображение с деталями товара. Чётко покажи детали и материал. "
        "Высокое качество, для российского маркетплейса. "
        "СТРОГО ЗАПРЕЩЕНО: любой текст, буквы, цифры, логотипы, бренды, водяные знаки, "
        "QR-коды, ссылки, телефоны, цены, рекламные надписи, скидки, акции."
    )
    ref_images = build_phase2_refs(state)
    image_url = generate_image(token=state.token, prompt=prompt, ref_images=ref_images, node_name="detail_gen")
    return DetailImageOutput(detail_image=image_url)
