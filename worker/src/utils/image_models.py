"""生图节点模型配置（v0.25）— imagegen.json 热加载。

与 image_prompts 同机制：每次现读磁盘（无缓存），改文件下一次生图生效；
配置缺失/损坏时回退 gpt-image-2（保持 v0.24 默认行为）。
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_NODE_MODEL = "gpt-image-2"


def get_image_model(node_key: str) -> str:
    """取指定生图节点（main/white_bg/scene_1…）的模型；缺失回退 gpt-image-2。"""
    workspace = os.getenv("APP_WORKSPACE_PATH") or os.getcwd()
    cfg_path = os.path.join(workspace, "config", "imagegen.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        model = (cfg.get("nodes") or {}).get(node_key)
        if model and isinstance(model, str) and model.strip():
            return model.strip()
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("生图模型配置加载失败(%s): %s，回退 %s", cfg_path, e, DEFAULT_NODE_MODEL)
    return DEFAULT_NODE_MODEL
