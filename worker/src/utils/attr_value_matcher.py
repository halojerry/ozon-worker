"""属性值匹配纯函数层（v0.40 Phase 1）。

三处（assemble 构建期 / prepare 补全期 / retry 修复期）共用的确定性匹配逻辑，
从各自内联闭包提取而来，保证行为等价 + 结构性消灭漂移。

分层（对抗评审裁决 architect-high L1 设计）：
- 本文件 = L1 纯函数层：零网络、零 LLM、零 IO，全部可离线单测
- L2 编排层（resolve_dict_attr 等）放同文件，但 LLM 消歧是独立 Phase 4 增量
- 纪律（AGENTS.md 需牢记 + v0.30 retry 纪律）：
  - 字典属性绝不盲补首值：精确 → 包含 → jieba → 同义词 → 唯一值 → None
  - 9782 危险品只挑安全默认（get_safe_hazard_default）
  - 字典属性 value 中文清零（dict_id 权威）
  - is_aspect 属性创建后不可改，跳过
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.attribute_utils import (  # type: ignore
    get_safe_hazard_default,
    is_aspect_attr,
    is_hazard_attr,
    match_attr_name_synonym,
)

_logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class AttrResolution:
    """一次属性值解析的结果（三处共用）。

    status:
        matched          确定性命中（精确/包含/jieba/同义词）
        unique_hit       字典值唯一时命中
        llm_eligible     多候选待 LLM 消歧（Phase 4；当前直接降级 skipped）
        skipped          宁缺毋滥跳过（无匹配/危险品无安全值/翻译失败）
        no_source        无 1688 源值可匹配
        aspect_skipped   is_aspect 属性跳过（不可改）
    match_layer: exact|contains|jieba|synonym|unique|value_map|none
    dictionary_value_id: 0 = 未命中（字典属性 id>0 时 value 文本必须中文清零）
    value: 展示文本（字典属性 id>0 且含中文 → 空串，dict_id 权威）
    """
    status: str = "skipped"
    attr_id: int = 0
    attr_name: str = ""
    product_value: str = ""
    dictionary_value_id: int = 0
    value: str = ""
    candidates: List[dict[str, Any]] = field(default_factory=list)
    match_layer: str = "none"
    confidence: float = 0.0
    reason: str = ""


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def has_chinese(text: str | None) -> bool:
    return bool(text) and bool(_CJK_RE.search(str(text)))


def lang_route(value: str) -> str:
    """搜索词语言路由（对抗评审裁决 reasoner ②/architect ②）。

    /values/search 无 language 参数（语言无关），搜索词本身的语言决定结果。
    含中文 → ZH_HANS 优先；否则 → RU 优先。
    """
    if has_chinese(value):
        return "ZH_HANS"
    return "RU"


def clean_dict_value(dict_id: int, value: str) -> str:
    """字典属性值中文清零（dict_id 权威，中文 value 文本会被 Ozon 拒）。"""
    if dict_id > 0 and value and _CJK_RE.search(str(value)):
        return ""
    return str(value or "")


def match_attr_name(
    ozon_attr_name: str,
    product_attrs: Dict[str, str],
    synonyms: Optional[dict] = None,
) -> Optional[str]:
    """用 Ozon 属性中文名匹配 1688 产品属性值（v0.32 词汇分歧修复，逐字提取）。

    层序：精确 → 包含 → jieba 分词子串重叠 → 同义词组（双向包含，防错误值）。
    返回 1688 属性名；无命中 → None。
    """
    name_lower = normalize_text(ozon_attr_name)
    if not name_lower:
        return None
    # 精确匹配
    for pa_name in product_attrs.keys():
        if normalize_text(pa_name) == name_lower:
            return pa_name
    # 包含匹配
    for pa_name in product_attrs.keys():
        pn = normalize_text(pa_name)
        if pn and (name_lower in pn or pn in name_lower):
            return pa_name
    # jieba 分词子串重叠（对「商品材质」vs「主要材质」共享 token「材质」）
    try:
        import jieba as _jieba
        ozon_tokens = [w for w in _jieba.cut(name_lower) if len(w) >= 2]
        if ozon_tokens:
            for pa_name in product_attrs.keys():
                pa_tokens = [w for w in _jieba.cut(normalize_text(pa_name)) if len(w) >= 2]
                if pa_tokens and any(o in p or p in o for o in ozon_tokens for p in pa_tokens):
                    return pa_name
    except Exception:
        pass
    # 同义词组匹配（attr_synonyms.json）：同组双向包含才返回，防错误值
    matched_name = match_attr_name_synonym(name_lower, list(product_attrs.keys()), synonyms or {})
    if matched_name is not None:
        return matched_name
    return None


def match_dict_value(
    attr_id: int,
    product_value: str,
    cached_values: List[dict],
) -> List[dict]:
    """在缓存字典值中确定性匹配（v0.13/v0.30 纪律：绝不盲补首值）。

    层序：精确（归一化） → 包含。返回**全部**存活候选（调用方决定
    单值/多值/LLM 消歧/跳过）；无命中 → 空列表。
    """
    if not product_value:
        return []
    pv_lower = normalize_text(product_value)
    if not pv_lower:
        return []
    hits: List[dict] = []
    for v in cached_values or []:
        if not isinstance(v, dict):
            continue
        vv = normalize_text(str(v.get("value") or ""))
        if not vv:
            continue
        if vv == pv_lower or pv_lower in vv or vv in pv_lower:
            hits.append(v)
    return hits


def unique_or_none(
    attr_id: int,
    attr_name: str,
    candidates: List[dict],
    *,
    hazard_safe: bool = True,
    aspect_skip: bool = False,
) -> AttrResolution:
    """单值属性命中决策（v0.30 retry 纪律 + v0.13 宁缺毋滥）。

    规则（顺位）：
    1. 危险属性（9782）→ 只挑非危险安全默认（get_safe_hazard_default）
    2. is_aspect 且 aspect_skip → aspect_skipped（创建后不可改）
    3. 恰好 1 个候选 → matched（dict_id 权威，value 中文清零）
    4. 多个候选 → llm_eligible（Phase 4 消歧；当前无 LLM 时直接 skipped，
       绝不取第一个——v0.13「套娃」错配教训）
    5. 0 候选 → skipped
    """
    res = AttrResolution(attr_id=attr_id, attr_name=attr_name)
    if is_hazard_attr(attr_id, attr_name) and hazard_safe:
        safe = get_safe_hazard_default(candidates)
        if safe:
            res.status = "matched"
            res.match_layer = "hazard_safe"
            res.dictionary_value_id, res.value = safe[0], safe[1]
            res.confidence = 1.0
            return res
        res.status = "skipped"
        res.reason = "hazard_no_safe_default"
        return res
    if aspect_skip and is_aspect_attr(attr_id, attr_name):
        res.status = "aspect_skipped"
        res.reason = "is_aspect"
        return res
    if len(candidates) == 1:
        vid = int(candidates[0].get("id") or candidates[0].get("dictionary_value_id") or 0)
        raw = str(candidates[0].get("value") or "")
        res.status = "matched"
        res.match_layer = "unique"
        res.dictionary_value_id = vid
        res.value = clean_dict_value(vid, raw)
        res.confidence = 1.0
        return res
    if len(candidates) > 1:
        res.status = "llm_eligible"
        res.match_layer = "multi"
        res.candidates = candidates
        res.reason = f"{len(candidates)} candidates, llm disambiguation deferred"
        return res
    res.status = "skipped"
    res.reason = "no_match"
    return res


def resolve_cached(
    attr_id: int,
    attr_name: str,
    product_value: str,
    cached_values: List[dict],
    *,
    hazard_safe: bool = True,
    aspect_skip: bool = False,
) -> AttrResolution:
    """完整确定性解析（L1 主干）：match_dict_value → unique_or_none。

    无 1688 源值 → no_source。这是三处（assemble/prepare/retry）共用的
    「缓存值 → 决议」路径；API 搜索链（L2）在 Phase 2 接入。
    """
    if not product_value:
        return AttrResolution(
            status="no_source", attr_id=attr_id, attr_name=attr_name,
            reason="empty_product_value",
        )
    hits = match_dict_value(attr_id, product_value, cached_values or [])
    return unique_or_none(attr_id, attr_name, hits, hazard_safe=hazard_safe, aspect_skip=aspect_skip)


# ══════════════════════════════════════════════════════════════════════
# L2 编排层：LLM 消歧（Phase 4，安全三件套，默认关）
# ══════════════════════════════════════════════════════════════════════
# 对抗评审 reasoner-ultrabrain ①裁决（三件套，缺一不可）：
#   1. prompt 显式加「以上都不对 → 输出 -1」出口——照搬 skill
#      _llm_disambiguate_category（无 none 出口）会系统性错填
#   2. LLM 只输出候选列表内索引，绝不输出 dict_id（dict_id 一律由
#      确定性候选列表重查证，防幻觉数字 ID）
#   3. 解析失败/越界/异常 → None（abstain），宁缺毋滥
# 注意：max_tokens 从 config 读（≥200，deepseek-v4-flash reasoning
# tokens 吃配额，v0.34 教训 10/200 输出必空）。

_CFG_CACHE: dict = {}
_CFG_CACHE_PATH: str = ""


def load_disambiguation_cfg(config_path: str = "") -> dict:
    """加载 attr_disambiguation_cfg.json（模块级缓存 + APP_WORKSPACE_PATH 定位）。"""
    global _CFG_CACHE, _CFG_CACHE_PATH
    workspace = os.environ.get("APP_WORKSPACE_PATH") or os.getcwd()
    path = config_path or os.path.join(workspace, "config", "attr_disambiguation_cfg.json")
    if _CFG_CACHE and _CFG_CACHE_PATH == path:
        return _CFG_CACHE
    try:
        with open(path, "r", encoding="utf-8") as f:
            _CFG_CACHE = json.load(f)
            _CFG_CACHE_PATH = path
    except Exception:
        _CFG_CACHE = {}
        _CFG_CACHE_PATH = path
    return _CFG_CACHE


def build_disambiguation_prompt(
    attr_name: str,
    product_value: str,
    candidates: List[dict],
) -> tuple[str, str]:
    """构造消歧 prompt（sp + up），候选列表带编号+文本+id 供 LLM 选索引。"""
    cfg = load_disambiguation_cfg()
    sp = str(cfg.get("sp") or "")
    up_tpl = str(cfg.get("up") or "")
    lines = []
    for i, c in enumerate(candidates[:20]):
        val = str(c.get("value") or "")
        vid = c.get("id") or c.get("dictionary_value_id") or 0
        lines.append(f"- {i}. {val} (id={vid})")
    up = up_tpl.replace("{{attr_name}}", str(attr_name or "")[:60]) \
               .replace("{{product_value}}", str(product_value or "")[:80]) \
               .replace("{{candidates}}", "\n".join(lines))
    return sp, up


def parse_disambiguation_index(llm_text: str, n: int) -> Optional[int]:
    """解析 LLM 输出为候选索引；-1/None/越界/垃圾 → None（abstain）。

    安全三件套 ③：绝不把解析失败当成「选第一个」。
    """
    if not llm_text:
        return None
    m = re.search(r"-?\d+", str(llm_text).strip())
    if not m:
        return None
    idx = int(m.group(0))
    if idx == -1:
        return None
    if 0 <= idx < n:
        return idx
    return None


def disambiguate_candidates(
    resolution: AttrResolution,
    token: str,
    *,
    enabled: bool = False,
    llm_cfg: Optional[dict] = None,
) -> AttrResolution:
    """多候选 LLM 消歧（Phase 4，默认关 enabled=False 时原样返回）。

    enabled=True 时对 llm_eligible 状态的 resolution 调 LLM 选索引；
    命中 → dict_id 从确定性候选列表重查证（绝不信任 LLM 数字本身）；
    失败/abstain → skipped（不降级为取第一个）。
    """
    if not enabled or resolution.status != "llm_eligible" or not token:
        return resolution
    if not resolution.candidates:
        resolution.status = "skipped"
        resolution.reason = "no_candidates"
        return resolution
    try:
        from utils.mxou_api import call_mxou_chat_api  # type: ignore
        cfg = llm_cfg or load_disambiguation_cfg()
        cc = cfg.get("config") or {}
        max_tokens = int(cc.get("max_completion_tokens") or cc.get("max_tokens") or 2048)
        sp, up = build_disambiguation_prompt(
            resolution.attr_name, resolution.product_value, resolution.candidates,
        )
        out = call_mxou_chat_api(
            token=token,
            system_prompt=sp,
            user_prompt=up,
            model=str(cc.get("model") or "deepseek-v4-flash"),
            temperature=float(cc.get("temperature") or 0.0),
            max_tokens=max_tokens,
            timeout=30,
        )
        idx = parse_disambiguation_index(out, len(resolution.candidates))
        if idx is None:
            resolution.status = "skipped"
            resolution.reason = "llm_abstain"
            return resolution
        chosen = resolution.candidates[idx]
        vid = int(chosen.get("id") or chosen.get("dictionary_value_id") or 0)
        if vid <= 0:
            resolution.status = "skipped"
            resolution.reason = "llm_chosen_invalid_dict_id"
            return resolution
        resolution.status = "llm_disambiguated"
        resolution.match_layer = "llm"
        resolution.dictionary_value_id = vid
        resolution.value = clean_dict_value(vid, str(chosen.get("value") or ""))
        resolution.confidence = 0.8
        resolution.reason = f"llm_picked_idx={idx}"
        return resolution
    except Exception as e:
        resolution.status = "skipped"
        resolution.reason = "llm_error"
        _logger.debug("LLM 消歧失败（宁缺毋滥跳过）: %s", e)
        return resolution
