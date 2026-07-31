# Phase 2图片生成节点 - 场景图2 (v4: 使用 image_gen_factory 共享逻辑)
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state_image_gen import Scene2Input, Scene2Output
from utils.image_gen_factory import generate_image, build_phase2_refs, make_prompt

def scene_2_gen_node(state: Scene2Input, config: RunnableConfig, runtime: Runtime[Context]) -> Scene2Output:
    if not state.draft or not state.token:
        return Scene2Output(scene_2_image=None)
    prompt = make_prompt(state.draft.get("title", ""), state.draft.get("description", ""), getattr(state, 'scene_context_2', ''))
    image_url = generate_image(token=state.token, prompt=prompt, ref_images=build_phase2_refs(state), node_name="scene_2_gen")
    return Scene2Output(scene_2_image=image_url)
