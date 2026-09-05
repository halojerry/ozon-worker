"""Phase 4: LLM 属性消歧安全三件套单测（test_attr_llm_disambiguate.py）。

锁定安全三件套（对抗评审 reasoner-ultrabrain ①）：
1. prompt 有「以上都不对 → -1」出口
2. LLM 只输出候选索引，绝不输出 dict_id（dict_id 从确定性候选重查证）
3. 解析失败/越界/-1/异常 → None（abstain，绝不降级取第一个）
4. 默认关：enabled=False 原样返回 llm_eligible
5. max_tokens ≥ 200（deepseek-v4-flash-vision-exp reasoning 配额教训）
纯函数 + mock LLM，无需 PG/网络。
"""
import json
import os
import sys
import tempfile
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.attr_value_matcher import (  # noqa: E402
    AttrResolution,
    build_disambiguation_prompt,
    disambiguate_candidates,
    load_disambiguation_cfg,
    parse_disambiguation_index,
    unique_or_none,
)


def _llm_eligible_res():
    cands = [{"id": 148495146, "value": "套娃"}, {"id": 99385, "value": "杀虫剂"}]
    return unique_or_none(8229, "类型", cands)


# ── parse_disambiguation_index ──

def test_parse_valid_index():
    assert parse_disambiguation_index("0", 3) == 0
    assert parse_disambiguation_index(" 2 ", 3) == 2
    assert parse_disambiguation_index("2. some text", 3) == 2


def test_parse_minus_one_abstain():
    """-1 = 以上都不对 → None（abstain，跳过不填）。"""
    assert parse_disambiguation_index("-1", 3) is None
    assert parse_disambiguation_index(" -1 ", 3) is None


def test_parse_out_of_range_abstain():
    assert parse_disambiguation_index("5", 3) is None
    assert parse_disambiguation_index("-2", 3) is None


def test_parse_garbage_abstain():
    assert parse_disambiguation_index("", 3) is None
    assert parse_disambiguation_index(None, 3) is None
    assert parse_disambiguation_index("abc", 3) is None
    assert parse_disambiguation_index("我不知道", 3) is None


# ── build_disambiguation_prompt ──

def test_prompt_has_none_exit():
    """安全三件套 ①：sp 必须含「-1/以上都不对」出口。"""
    sp, up = build_disambiguation_prompt("类型", "杀虫剂", [{"id": 1, "value": "x"}])
    assert "-1" in sp
    assert "以上都不对" in sp or "不符" in sp
    assert "dictionary_value_id" in sp  # 明确禁止输出 dict_id


def test_prompt_candidates_numbered():
    sp, up = build_disambiguation_prompt("类型", "杀虫剂", [
        {"id": 148495146, "value": "套娃"}, {"id": 99385, "value": "杀虫剂"},
    ])
    assert "0. 套娃" in up
    assert "1. 杀虫剂" in up


# ── disambiguate_candidates ──

def test_disabled_passthrough():
    """默认关：enabled=False 原样返回 llm_eligible（不烧 LLM）。"""
    res = _llm_eligible_res()
    out = disambiguate_candidates(res, "token", enabled=False)
    assert out.status == "llm_eligible"
    assert out.dictionary_value_id == 0


def test_no_token_passthrough():
    res = _llm_eligible_res()
    out = disambiguate_candidates(res, "", enabled=True)
    assert out.status == "llm_eligible"


def test_llm_picks_index_dict_id_verified():
    """LLM 输出索引 → dict_id 从确定性候选重查证（安全三件套 ②）。"""
    res = _llm_eligible_res()
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="1"):
        out = disambiguate_candidates(res, "token", enabled=True, llm_cfg={"config": {"max_completion_tokens": 2048}})
    assert out.status == "llm_disambiguated"
    assert out.dictionary_value_id == 99385  # candidates[1] = 杀虫剂
    assert out.match_layer == "llm"


def test_llm_abstain_skips():
    """LLM 输出 -1 → skipped（不降级取第一个）。"""
    res = _llm_eligible_res()
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="-1"):
        out = disambiguate_candidates(res, "token", enabled=True, llm_cfg={"config": {}})
    assert out.status == "skipped"
    assert out.reason == "llm_abstain"
    assert out.dictionary_value_id == 0


def test_llm_out_of_range_skips():
    res = _llm_eligible_res()
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="9"):
        out = disambiguate_candidates(res, "token", enabled=True, llm_cfg={"config": {}})
    assert out.status == "skipped"
    assert out.reason == "llm_abstain"


def test_llm_error_skips():
    """LLM 异常 → skipped（宁缺毋滥，不阻塞管线）。"""
    res = _llm_eligible_res()
    with mock.patch("utils.mxou_api.call_mxou_chat_api", side_effect=Exception("timeout")):
        out = disambiguate_candidates(res, "token", enabled=True, llm_cfg={"config": {}})
    assert out.status == "skipped"
    assert out.reason == "llm_error"


def test_llm_never_trusts_raw_dict_id():
    """LLM 若输出非索引内容（如直接给 dict_id）→ 解析失败 abstain。"""
    res = _llm_eligible_res()
    with mock.patch("utils.mxou_api.call_mxou_chat_api", return_value="99385"):
        out = disambiguate_candidates(res, "token", enabled=True, llm_cfg={"config": {}})
    assert out.status == "skipped"  # 99385 不是候选索引（候选只有 0/1）
    assert out.dictionary_value_id == 0


# ── config 加载 ──

def test_config_load_max_tokens_ge_200():
    """配置 max_completion_tokens ≥ 200（reasoning 模型配额教训）。"""
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "config"), exist_ok=True)
    cfg = {
        "config": {"model": "deepseek-v4-flash-vision-exp-vision-exp", "max_completion_tokens": 2048},
        "sp": "sp", "up": "up",
    }
    with open(os.path.join(tmp, "config", "attr_disambiguation_cfg.json"), "w") as f:
        json.dump(cfg, f)
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": tmp}):
        loaded = load_disambiguation_cfg()
    mt = int((loaded.get("config") or {}).get("max_completion_tokens") or 0)
    assert mt >= 200


def test_config_missing_returns_empty():
    tmp = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": tmp}):
        assert load_disambiguation_cfg() == {}
