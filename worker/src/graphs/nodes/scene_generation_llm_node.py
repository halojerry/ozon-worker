import os
import json
from typing import Dict, List, Any
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state import SceneGenerationInput, SceneGenerationOutput
from utils.progress_logger import ProgressLogger  # 导入进度日志助手
from utils.mxou_llm import call_mxou_chat_api


def scene_generation_llm_node(state: SceneGenerationInput, config: RunnableConfig, runtime: Runtime[Context]) -> SceneGenerationOutput:
    """
    title: 场景生成LLM节点
    desc: 使用deepseek-V4-flash模型，根据产品信息生成3个适合的使用场景
    integrations: api.mxou.cn LLM (deepseek-v4-flash)
    """
    
    ctx = runtime.context
    
    # 初始化进度日志助手
    progress = ProgressLogger()
    
    # 记录节点开始（含进度百分比）
    progress.log_node_start("scene_generation_llm_node", "场景生成LLM节点")
    
    # 从config读取LLM配置路径
    cfg_file = os.path.join(os.getenv("APP_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    with open(cfg_file, 'r') as fd:
        _cfg = json.load(fd)
    
    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")
    
    # 从draft中提取产品信息
    draft = state.draft if isinstance(state.draft, dict) else {}
    title = draft.get("title", "")
    description = draft.get("description", "")
    category = draft.get("category", "")
    
    # 使用jinja2模板渲染用户提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "title": title,
        "description": description,
        "category": category
    })
    
    # 调用 mxou LLM Chat API（deepseek-v4-flash模型）
    token: str = state.token
    
    try:
        content = call_mxou_chat_api(
            token=token,
            system_prompt=sp,
            user_prompt=user_prompt,
            model=llm_config.get("model", "deepseek-v4-flash"),
            temperature=llm_config.get("temperature", 0.7),
            max_tokens=llm_config.get("max_tokens", 2048)
        )
        
        if content and content.strip():
            # 解析LLM返回的3个场景（假设LLM返回JSON格式）
            # 例如：{"scene_1": "户外运动场景", "scene_2": "办公室桌面场景", "scene_3": "家庭卧室场景"}
            
            try:
                scenes = json.loads(content)
                scene_1_context = scenes.get("scene_1", "户外运动场景")
                scene_2_context = scenes.get("scene_2", "办公室桌面场景")
                scene_3_context = scenes.get("scene_3", "家庭卧室场景")
            except json.JSONDecodeError:
                # 如果LLM返回的不是JSON格式，尝试从文本中提取
                # 假设LLM返回格式：场景1: xxx; 场景2: xxx; 场景3: xxx
                lines = content.split("\n")
                scene_1_context = "户外运动场景"
                scene_2_context = "办公室桌面场景"
                scene_3_context = "家庭卧室场景"
                
                for line in lines:
                    if "场景1" in line or "scene_1" in line:
                        scene_1_context = line.split(":")[-1].strip()
                    elif "场景2" in line or "scene_2" in line:
                        scene_2_context = line.split(":")[-1].strip()
                    elif "场景3" in line or "scene_3" in line:
                        scene_3_context = line.split(":")[-1].strip()
            
            return SceneGenerationOutput(
                scene_context_1=scene_1_context,
                scene_context_2=scene_2_context,
                scene_context_3=scene_3_context
            )
        
        else:
            # 如果API调用失败，返回默认场景
            return SceneGenerationOutput(
                scene_context_1="户外运动场景",
                scene_context_2="办公室桌面场景",
                scene_context_3="家庭卧室场景"
            )
    
    except Exception as e:
        # 如果出现异常，返回默认场景
        return SceneGenerationOutput(
            scene_context_1="户外运动场景",
            scene_context_2="办公室桌面场景",
            scene_context_3="家庭卧室场景"
        )