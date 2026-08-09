"""属性同义词共享加载器（v0.32 属性名词汇分歧修复）。

单一事实源：`config/attr_synonyms.json` 由 assemble `_match_product_attr`（assemble_ozon_product_node）
与 prepare `_fill_optional_dict_attrs`（prepare_ozon_upload_node）共同消费，
避免两处各自加载造成配置漂移（之前该文件只在 prepare 侧使用，assemble 侧匹配不到 → 0 映射）。

路径解析：优先 `APP_WORKSPACE_PATH`（Docker 内 /app）；未设置时回退 `os.getcwd()`
（本地单测从 worker/ 目录运行，`config/attr_synonyms.json` 直接可达）。
缓存按 workspace 键控 —— 生产一个进程只读一次文件；测试侧 mock 不同
APP_WORKSPACE_PATH 时能读到各自的 fixture。
"""
from __future__ import annotations

import json
import os

_CACHE_KEY: str | None = None
_CACHE: dict | None = None


def load_attr_synonyms() -> dict:
    """读取 attr_synonyms.json（模块级缓存）；缺失/损坏 → {}。"""
    global _CACHE, _CACHE_KEY
    workspace = os.getenv("APP_WORKSPACE_PATH") or os.getcwd()
    if _CACHE is not None and _CACHE_KEY == workspace:
        return _CACHE
    cfg_path = os.path.join(workspace, "config", "attr_synonyms.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _CACHE = data if isinstance(data, dict) else {}
    except Exception:
        _CACHE = {}
    _CACHE_KEY = workspace
    return _CACHE
