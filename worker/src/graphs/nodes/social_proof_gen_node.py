# Phase 2图片生成节点 - 社交证明图 (v4: 使用 image_gen_factory 共享逻辑)
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state_image_gen import SocialProofInput, SocialProofOutput
from utils.image_gen_factory import generate_image, build_phase2_refs

def social_proof_gen_node(state: SocialProofInput, config: RunnableConfig, runtime: Runtime[Context]) -> SocialProofOutput:
    if not state.draft or not state.token:
        return SocialProofOutput(social_proof_image=None)
    prompt = (
        "Smartphone snapshot of the product in real use. Natural indoor lighting, "
        "slightly grainy, warm tone, candid unposed feel. Russian UGC style. "
        "Do NOT include: watermarks, logos, prices, discounts, phone numbers, "
        "email, website URLs, QR codes, promotional badges."
    )
    image_url = generate_image(token=state.token, prompt=prompt, ref_images=build_phase2_refs(state), node_name="social_proof_gen")
    return SocialProofOutput(social_proof_image=image_url)
