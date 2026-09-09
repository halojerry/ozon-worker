# -*- coding: utf-8 -*-
"""
v0.73 单测 —— LLM 类目仲裁注入 1688 货源类目路径（新信封断桥修复）。

生产实证：skill 新信封把货源类目路径放 source.source_category_path，而
_llm_rank_categories 的 prompt「1688类目:」行只读 draft.source_category
（旧字段，新信封常为空）→ LLM 仲裁缺货源上下文（留香珠 vs 文本候选
sim 0.125「便携式洗衣机」）无从判断 → 弃权 → 低置信入箱。

修复：签名加 source_category 参数（缺省回落 draft.source_category 保持现状），
6 个调用点传主流程已解析的局部变量。

边界注明：PG 类目树 16552 节点（裁剪树）缺「留香珠」叶子 → 文本候选全垃圾；
本修复让 LLM 看到货源路径后可给出 suggest_keywords（如 кондиционер для
белья）走二搜桥接「衣物洗涤柔顺剂」；扩树（导入完整类目）另立项，不在本批。

运行: cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_llm_arbitration_source_ctx_v073.py -q
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.assemble_ozon_product_node import _llm_rank_categories

# 捕获 user_prompt（call_mxou_chat_api 全 kwarg 调用）
_CAPTURED = {}


def _fake_state(**extra):
    """函数内只 getattr(state, 'token'/'competitor_name', ...) — 简单 stub 即可。"""
    attrs = {"token": "", "competitor_name": ""}
    attrs.update(extra)
    return type("S", (), attrs)()


def _capture_call(resp):
    """构造捕获 user_prompt 并返回固定响应的 mock。"""

    def _fake(*args, **kwargs):
        _CAPTURED["user_prompt"] = kwargs.get("user_prompt", "")
        return resp

    return _fake


def _source_line(prompt: str) -> str:
    """取 prompt 中「1688类目:」那一行（含冒号后内容）。"""
    return next(l for l in prompt.splitlines() if l.startswith("1688类目:"))


def _make_candidate(full_path, dc, tp, sim=0.125):
    """带 dc/tp 的候选（选中后按 idx-1 原样返回 dict）。"""
    return {
        "full_path": full_path,
        "description_category_id": dc,
        "type_id": tp,
        "similarity": sim,
        "node_name": full_path.split(">")[-1].strip(),
    }


# ── 1. 传 source_category → prompt 1688类目 行带货源路径（修复前恒空 → RED）──

def test_prompt_contains_source_category_path():
    cands = [_make_candidate("家用电器 > 便携式洗衣机", 1, 10)]
    resp = '{"top_index": 1, "confidence": 0.8, "reason": "匹配", "suggest_keywords": ""}'
    with patch("utils.mxou_api.call_mxou_chat_api", side_effect=_capture_call(resp)):
        r = _llm_rank_categories(
            cands, "留香珠", {}, _fake_state(),
            source_category="个护/家清 > 衣物清洁护理 > 留香珠",
        )
    assert r is not None and r.get("type_id") == 10  # 正常选中候选（top_index=1）
    p = _CAPTURED["user_prompt"]
    assert "个护/家清" in p  # 修复前该行恒空 → 此断言 RED
    assert "留香珠" in _source_line(p)  # 留香珠出现在 1688类目 行（非关键词行）


# ── 2. 不传 source_category 且 draft 无 source_category → 该行为空（锁现状）──

def test_prompt_source_line_empty_without_any_context():
    cands = [{"full_path": "家用电器 > 便携式洗衣机", "similarity": 0.125}]
    resp = '{"top_index": -1, "confidence": 0.0, "reason": "无关", "suggest_keywords": ""}'
    with patch("utils.mxou_api.call_mxou_chat_api", side_effect=_capture_call(resp)):
        _llm_rank_categories(cands, "留香珠", {}, _fake_state())
    assert _source_line(_CAPTURED["user_prompt"]).strip() == "1688类目:"


# ── 3. 缺省回落 draft.source_category（旧信封兼容，行为不变）──

def test_prompt_falls_back_to_draft_source_category():
    cands = [{"full_path": "家用电器 > 便携式洗衣机", "similarity": 0.125}]
    resp = '{"top_index": 1, "confidence": 0.8, "reason": "匹配", "suggest_keywords": ""}'
    with patch("utils.mxou_api.call_mxou_chat_api", side_effect=_capture_call(resp)):
        _llm_rank_categories(
            cands, "留香珠", {"source_category": "个护/家清 > 留香珠"}, _fake_state(),
        )
    assert "留香珠" in _source_line(_CAPTURED["user_prompt"])


# ── 4. 弃权 + suggest_keywords 透传（二搜闭环吃上货源 context）──

def test_abstain_suggest_keywords_passthrough():
    cands = [{"full_path": "家用电器 > 便携式洗衣机", "similarity": 0.125}]
    resp = ('{"top_index": -1, "confidence": 0.0, "reason": "无关", '
            '"suggest_keywords": "кондиционер для белья"}')
    with patch("utils.mxou_api.call_mxou_chat_api", side_effect=_capture_call(resp)):
        r = _llm_rank_categories(
            cands, "留香珠", {}, _fake_state(),
            source_category="个护/家清 > 衣物清洁护理 > 留香珠",
        )
    # 实现形状（3980-3983）: 弃权且带建议词 → {"suggest_keywords": ..., "_llm_suggest": True}
    assert r is not None and r.get("_llm_suggest") is True
    assert r.get("suggest_keywords") == "кондиционер для белья"
    # 弃权路径同样带上了货源 context（LLM 才给得出俄语建议词）
    assert "留香珠" in _source_line(_CAPTURED["user_prompt"])


# ── 5. 参数为空串 → 等价不传（显式空参不破坏旧行为）──

def test_empty_source_category_param_equals_absent():
    cands = [{"full_path": "家用电器 > 便携式洗衣机", "similarity": 0.125}]
    resp = '{"top_index": -1, "confidence": 0.0, "reason": "无关", "suggest_keywords": ""}'
    with patch("utils.mxou_api.call_mxou_chat_api", side_effect=_capture_call(resp)):
        _llm_rank_categories(cands, "留香珠", {}, _fake_state(), source_category="")
    assert _source_line(_CAPTURED["user_prompt"]).strip() == "1688类目:"
