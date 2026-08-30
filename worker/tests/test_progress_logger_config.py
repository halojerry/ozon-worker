"""T3 (v0.62.1 P1-4): workflow_progress.json 路径解析 — 候选链 + 失败缓存防刷屏。

覆盖：
- assets 文件存在 → 加载成功（4 阶段 / 24 标题）
- 默认 config_path=assets/workflow_progress.json → 无 warning 加载
- 显式相对路径 config/（不存在）→ 回落 workspace/assets/ 成功且无 warning
- 路径全部无效 → 默认值 + 二次调用不重复 warning（失败缓存）
- log_node_start 输出含阶段名 + 进度百分比
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from utils import progress_logger


@pytest.fixture(autouse=True)
def _clean_cache():
    progress_logger._cfg_cache.clear()
    yield
    progress_logger._cfg_cache.clear()


def test_load_assets_success():
    """assets/workflow_progress.json 存在 → 4 阶段 / 24 标题。"""
    cfg = progress_logger._load_progress_config("assets/workflow_progress.json")
    assert cfg["total_nodes"] == 24
    assert "phase1_data_preparation" in cfg["stages"]
    assert len(cfg["node_titles"]) == 24


def test_default_config_path_no_warning(caplog):
    """默认 config_path=assets/... → 加载成功且无 warning（修复前 config/ 路径刷屏）。"""
    with caplog.at_level("WARNING"):
        p = progress_logger.ProgressLogger()
    assert p.total_nodes == 24
    assert len(p.stages) == 4
    assert not [r for r in caplog.records if "加载进度配置失败" in r.getMessage()]


def test_fallback_workspace_assets(caplog, tmp_path, monkeypatch):
    """显式相对路径 config/（不存在）→ 回落 workspace/assets/ 成功且无 warning。"""
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(Path(__file__).resolve().parent.parent))
    with caplog.at_level("WARNING"):
        cfg = progress_logger._load_progress_config("config/workflow_progress.json")
    assert cfg["total_nodes"] == 24
    assert not [r for r in caplog.records if "加载进度配置失败" in r.getMessage()]


def test_all_missing_caches_default_no_spam(caplog, tmp_path, monkeypatch):
    """路径全无效 → 默认值；二次调用不重复 warning（失败缓存）。"""
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))  # 空目录无 assets
    bad_path = str(tmp_path / "nope" / "workflow_progress.json")
    with caplog.at_level("WARNING"):
        cfg1 = progress_logger._load_progress_config(bad_path)
        cfg2 = progress_logger._load_progress_config(bad_path)
    assert cfg1 == {"total_nodes": 24, "stages": {}, "node_titles": {}}
    assert cfg2 == cfg1
    spam = [r for r in caplog.records if "加载进度配置失败" in r.getMessage()]
    assert len(spam) <= 1, f"失败应缓存，不重复告警: {len(spam)} 次"


def test_log_node_start_stage_and_percent(caplog):
    """log_node_start 输出含阶段名 + 进度百分比（phase1 节点）。"""
    p = progress_logger.ProgressLogger()
    with caplog.at_level("INFO"):
        p.log_node_start("assemble_ozon_product")
    assert any("进度" in r.getMessage() and "▶" in r.getMessage() for r in caplog.records)
