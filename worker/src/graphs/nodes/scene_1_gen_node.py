# Phase 2图片生成节点 - 场景图1 (v4: 使用 image_gen_factory 共享逻辑)
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state_image_gen import Scene1Input, Scene1Output
from utils.image_gen_factory import generate_image, build_phase2_refs, make_prompt

def scene_1_gen_node(state: Scene1Input, config: RunnableConfig, runtime: Runtime[Context]) -> Scene1Output:
    if not state.draft or not state.token:
        return Scene1Output(scene_1_image=None)
    prompt = make_prompt(state.draft.get("title", ""), state.draft.get("description", ""), getattr(state, 'scene_context_1', ''))
    image_url = generate_image(token=state.token, prompt=prompt, ref_images=build_phase2_refs(state), node_name="scene_1_gen")
    return Scene1Output(scene_1_image=image_url)
