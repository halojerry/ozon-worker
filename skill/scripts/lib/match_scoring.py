#!/usr/bin/env python3
"""货源匹配置信度复合评分（v0.72.1 P0-4 评分信号换轨）。

背景（2026-09-09 审计靶点二，docs/PLAN-race-duplication-audit-v1.md §2.4）：
discover 货源匹配置信度长期 0.11~0.38 → worker 低置信宁入箱不自动上。根因是
ozon_discovery `_title_conf` 纯标题文本相关性独占 confidence——aibuy 视觉官方
信号（normalizationScore）只微调排序分（:2322），1688 类目 ↔ Ozon 类目一致性
完全不参与评分。类目维度抓到了却没变成分数。

本模块把评分信号换轨：**类目一致性 + 图搜官方信号为主，标题文本降为辅助**。
纯函数零第三方依赖（不引 jieba，编译面最小）。

集成点（ozon_discovery `_conf_of_best` 替换；因目标文件有他会话 WIP，接线
步骤见 plan P0-4，落地时同步核对 `_attach_match_meta` 的 conf 消费方）：

    from scripts.lib.match_scoring import score_match
    meta = score_match(
        title_conf=_title_conf(ozon_title, cand, is_ru),
        candidate=cand,                    # aibuy/AK/CDP 归一候选
        ozon_category_path=category_path,  # Ozon 类目路径（页面真值优先）
    )
"""
from __future__ import annotations

import re
from typing import Any

# 复合权重：类目一致性为主（用户口径：按 1688/Ozon 类目信息判定），图搜官方
# 信号次之，标题文本仅辅助。类目上下文缺失时按比例回落到 visual/title。
DEFAULT_WEIGHTS: dict[str, float] = {"category": 0.45, "visual": 0.35, "title": 0.20}

_TOKEN_SPLIT = re.compile(r"[^0-9a-zа-яё\u4e00-\u9fff]+")


def _tokens(text: Any) -> list[str]:
    """轻分词：小写 + 按非字母数字/中西里尔/汉字切块。零依赖。"""
    return [t for t in _TOKEN_SPLIT.split(str(text or "").lower()) if t]


def category_consistency(candidate_category: Any, ozon_category_path: Any) -> float:
    """1688 候选类目 vs Ozon 类目路径一致性 ∈ [0,1]。

    重叠 = |共享 token| / min(|a|,|b|)：候选类目名短，用 min 惩罚只蹭到长路径
    泛词（家居用品）的情况。任一缺失/零重叠 → 0。中俄文路径均可（Термос↔термос）。
    """
    a = set(_tokens(candidate_category))
    b = set(_tokens(ozon_category_path))
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def visual_signal(badge_eff: Any, normalization_score: Any) -> float:
    """图搜官方信号 ∈ [0,1]：matchBadgeFull（badge_eff>=1）官方「全部符合」
    直通 1.0；否则取归一化相似度（aibuy normalizationScore，缺失/非法 → 0）。"""
    try:
        if float(badge_eff or 0) >= 1.0:
            return 1.0
    except (TypeError, ValueError):
        pass
    try:
        return max(0.0, min(1.0, float(normalization_score or 0)))
    except (TypeError, ValueError):
        return 0.0


def _badge_eff(candidate: dict[str, Any]) -> Any:
    if "badge_eff" in candidate:
        return candidate.get("badge_eff")
    return 1.0 if candidate.get("badge") == "matchBadgeFull" else 0.0


def score_match(
    *,
    title_conf: float,
    candidate: dict[str, Any],
    ozon_category_path: str = "",
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """复合置信度：{"confidence", "components", "trusted"}。

    - category：candidate["category_name"]（aibuy cate_level2 名 / AK 类目名）
      vs ozon_category_path（Ozon 类目路径，页面真值优先）的一致性；
    - visual：badge_eff + normalization_score（官方图搜信号）；
    - title：调用方传入的 _title_conf 结果（纯文本，仅辅助）。

    类目上下文缺失（路径或候选类目名皆空）→ 类目权重按比例并回 visual/title，
    行为退化为「官方信号+文本」，不低于旧纯文本口径。confidence 恒 [0,1]。
    trusted 仅由 matchBadgeFull 直通（与 cloud_probe trusted 语义一致）。
    """
    w = dict(weights or DEFAULT_WEIGHTS)
    components: dict[str, float] = {}
    trusted = False

    # 在场判定：CDP 通道候选往往没有任何官方视觉信号——此时必须整权重回落
    # 标题文本（conf==title，与旧 _title_conf 口径逐字一致），否则纯文本匹配
    # 被无信号压分（回归风险）。只有信号真实在场才参与分摊。
    visual_present = any(
        k in candidate for k in ("normalization_score", "similarity_score", "badge_eff")
    ) or candidate.get("badge") == "matchBadgeFull"

    cand_category = str(candidate.get("category_name") or "").strip()
    category_present = bool(cand_category) and bool(str(ozon_category_path or "").strip())
    if category_present:
        _cat = category_consistency(cand_category, ozon_category_path)
        # 零重叠（典型：RU 面包屑 vs ZH 类目名跨语言）视为不在场——类目信息
        # 只能加分不能稀释（在场但零分会把标题/视觉权重挤水，误伤真匹配）。
        category_present = _cat > 0.0
        if category_present:
            components["category"] = _cat

    vis = visual_signal(_badge_eff(candidate), candidate.get("normalization_score"))
    components["visual"] = vis
    try:
        if float(_badge_eff(candidate) or 0) >= 1.0:
            trusted = True
    except (TypeError, ValueError):
        pass

    title = max(0.0, min(1.0, float(title_conf or 0)))
    components["title"] = title

    active: dict[str, float] = {}
    if category_present:
        active["category"] = w["category"]
    if visual_present:
        active["visual"] = w["visual"]
    active["title"] = w["title"]
    denom = sum(active.values())
    total = sum(active.get(k, 0.0) * v for k, v in components.items() if k in active)
    confidence = max(0.0, min(1.0, total / denom if denom else 0.0))
    return {"confidence": confidence, "components": components, "trusted": trusted}
