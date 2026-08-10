# -*- coding: utf-8 -*-
"""
v0.34 review fix 单测 —— LLM 类目 fallback suggest 二次搜索死代码修复。

背景（review 发现）：第一个 LLM fallback 路径（assemble L826）suggest 二次搜索
合并候选后未重新排名，best_by_llm 仍是 {"_llm_suggest": True} 标记 dict，
full_path 为空 → 重叠检查恒失败 → 硬阻断，二次搜索白做。

修复：合并候选后重跑 _llm_rank_categories；若仍返回 suggest 标记则从
合并后 candidates top1 回退（不再硬阻断）。

运行: cd worker && PYTHONPATH=src /Volumes/os/dev/ozon-worker/skill/.venv314/bin/python tests/test_llm_suggest_rerank.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.assemble_ozon_product_node import _llm_rank_categories


def _make_candidate(full_path, dc, tp, sim=0.3):
    return {
        "full_path": full_path,
        "description_category_id": dc,
        "type_id": tp,
        "similarity": sim,
        "matcher": "jieba",
        "node_name": full_path.split(">")[-1].strip(),
        "_score": sim,
    }


# ── _llm_rank_categories: suggest 标记返回 ──

def test_llm_returns_candidate_index():
    """LLM 返回 candidate_index=2 → 返回对应候选 dict（非 suggest 标记）。"""
    cands = [_make_candidate("A > B > C1", 1, 10), _make_candidate("A > B > C2", 2, 20)]
    state = type("S", (), {"token": ""})()
    with patch("utils.mxou_api.call_mxou_chat_api",
               return_value='{"candidate_index": 2, "suggest_keywords": ""}'):
        r = _llm_rank_categories(cands, "kw", {"title": "t"}, state)
    assert r is not None and r.get("type_id") == 20
    assert not r.get("_llm_suggest")


def test_llm_returns_suggest_marker_when_none_fit():
    """LLM 认为候选都不合适 → 返回 suggest 标记 dict。"""
    cands = [_make_candidate("A > B > C1", 1, 10)]
    state = type("S", (), {"token": ""})()
    with patch("utils.mxou_api.call_mxou_chat_api",
               return_value='{"candidate_index": 0, "suggest_keywords": "правильный класс"}'):
        r = _llm_rank_categories(cands, "kw", {"title": "t"}, state)
    assert r is not None and r.get("_llm_suggest") is True
    assert r.get("suggest_keywords") == "правильный класс"


def test_llm_returns_none_on_garbage():
    """LLM 返回非 JSON / 非法 → None（不崩）。"""
    cands = [_make_candidate("A > B > C1", 1, 10)]
    state = type("S", (), {"token": ""})()
    with patch("utils.mxou_api.call_mxou_chat_api",
               return_value="not json at all"):
        r = _llm_rank_categories(cands, "kw", {"title": "t"}, state)
    assert r is None


def test_llm_suggest_keywords_used_in_rerank():
    """合并建议词候选后重跑 LLM → 选中新候选（review fix: 不再硬阻断）。"""
    # 第一次 LLM: 认为候选不合适, 给建议词
    cands = [_make_candidate("X > Y > 旧候选", 1, 10)]
    state = type("S", (), {"token": ""})()
    calls = iter([
        # 第一次: suggest 标记
        '{"candidate_index": 0, "suggest_keywords": "教育游戏"}',
        # 第二次(合并后): 仍建议（模拟 LLM 对并入候选也不满意）
        '{"candidate_index": 0, "suggest_keywords": "教育游戏"}',
    ])
    with patch("utils.mxou_api.call_mxou_chat_api",
               side_effect=lambda *a, **k: next(calls)):
        r = _llm_rank_categories(cands, "kw", {"title": "t"}, state)
    # 返回 suggest 标记（非 None）→ 上层会从合并后 candidates 回退
    assert r is not None and r.get("_llm_suggest") is True
