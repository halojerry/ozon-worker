"""属性缺口量化工具（v0.40 Phase 0）。

从「34 属性只填 15-16」的模糊感知，变为可量化的缺口报告。
核心：过滤「本就不该填/由系统生成」的属性后，计算真实缺口及来源分布。

纪律对齐（改本文件前必读 AGENTS.md 需牢记的约定）：
- 字典属性 value 文本以 dictionary_value_id 权威（中文值清零）
- 9782 危险品只填安全默认；23536 标记码 Ozon 自动设置跳过
- 品牌 85/31/5076 强制 Нет бренда（126745801）
- 原产国 4389 硬编码 Китай
- 海关编码 22604 is_customs_attr 跳过
- 9048/4191/4180/23171 由系统生成（型号/富文本描述/关键字/hashtag），非 1688 源
- 自由文本 23487(制造商)=supplier、22390(型号)=itemId，非 1688 属性源
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.attribute_utils import is_customs_attr  # type: ignore

# ── 系统生成/强制默认属性（不应计入「应填」） ──
_SYSTEM_GENERATED_IDS = {
    9048,   # 型号名称（合并卡，用 itemId 补）
    4191,   # 简介（富文本 HTML，由描述管线生成）
    4180,   # 名称（商品标题）
    23171,  # #主题标签（hashtag 由标签管线生成）
    23536,  # 标记码（Ozon 自动设置）
    9782,   # 产品危险等级（只填安全默认，非 1688 源）
}
_BRAND_IDS = {85, 31, 5076}      # 品牌强制 Нет бренда
_COUNTRY_IDS = {4389}            # 原产国硬编码 Китай
_FREE_TEXT_NON_SOURCE_IDS = {    # 自由文本但非 1688 属性源（有专用填充路径）
    23487,  # 制造商 = draft.supplier
    22390,  # 型号 = itemId
}

# 排除后仍可能被 is_customs_attr 拦掉的（名称关键词类）
_SKIP_ATTR_IDS = frozenset({23536})


def is_system_generated(schema_attr: dict[str, Any]) -> bool:
    """判断属性是否为「系统生成/强制默认」，不应计入应填缺口。

    命中以下任一即 True（该属性不需要 1688 源）：
    - 海关编码（is_customs_attr，ID 或名称关键词）
    - _SYSTEM_GENERATED_IDS / _BRAND_IDS / _COUNTRY_IDS / _FREE_TEXT_NON_SOURCE_IDS
    """
    attr_id = int(schema_attr.get("id") or 0)
    attr_name = str(schema_attr.get("name") or "")
    if is_customs_attr(attr_id, attr_name):
        return True
    if attr_id in _SYSTEM_GENERATED_IDS:
        return True
    if attr_id in _BRAND_IDS:
        return True
    if attr_id in _COUNTRY_IDS:
        return True
    if attr_id in _FREE_TEXT_NON_SOURCE_IDS:
        return True
    return False


def should_fill(schema_attr: dict[str, Any]) -> bool:
    """该属性是否「应填」——即计入填满率分母。

    False = 系统生成/强制默认/海关/标记码（本就不该从 1688 源填）。
    True = 需要 worker 尽力填充（字典或自由文本），评分提升对象。
    """
    return not is_system_generated(schema_attr)


# ── 来源提示推导（弱源） ──

def _extract_tokens(text: str, min_len: int = 2) -> set[str]:
    """简单分词（中文按字符组、其他按空白）——不依赖 jieba 保持零 I/O 纯函数。

    说明：Phase 0 只做来源分类统计，不追求精确分词；Phase 6 弱源推导
    会接入 attr_defaults.dict_search_terms 的成熟分词。
    """
    tokens: set[str] = set()
    if not text:
        return tokens
    for seg in str(text).replace(",", " ").replace("，", " ").replace("/", " ").split():
        seg = seg.strip()
        if len(seg) >= min_len:
            tokens.add(seg)
    return tokens


def _source_hint(attr: dict[str, Any], draft: dict[str, Any]) -> str:
    """判断属性的可用来源：from_1688_attr / from_title / from_variant / from_ozon_attrs / no_source。

    弱源推导（对抗评审裁决 A）：属性名 token 在标题/变体/竞品属性中出现，
    说明可以从弱源推导填值，缺口不是「完全无源」。
    """
    attr_id = int(attr.get("id") or 0)
    attr_name = str(attr.get("name") or "").strip()

    # 1688 属性直接匹配（draft.attributes 中文名精确/包含）
    attrs_1688 = draft.get("attributes") or {}
    if isinstance(attrs_1688, dict):
        for k in attrs_1688.keys():
            k = str(k or "").strip()
            if k and (k == attr_name or k in attr_name or attr_name in k):
                return "from_1688_attr"

    # 竞品属性（follow 透传的 ozon_attributes，RU 名→值；存在即视为弱源，
    # 精确键匹配由 build_follow_attr_merge 语义解析负责——此处只做来源分类）
    ozon_attrs = draft.get("ozon_attributes")
    if isinstance(ozon_attrs, dict) and ozon_attrs:
        return "from_ozon_attrs"

    # 标题弱源（中文无空格：子串包含匹配，非 token 集合交集）
    title = str(draft.get("title") or "")
    if title:
        name_tokens = _extract_tokens(attr_name)
        if any(tok and tok in title for tok in name_tokens):
            return "from_title"

    # 变体弱源（颜色/尺寸等 option 值）
    variants = draft.get("variants") or []
    if isinstance(variants, list) and variants:
        for v in variants:
            vname = str(v.get("name") or "")
            if vname:
                name_tokens = _extract_tokens(attr_name)
                if any(tok and tok in vname for tok in name_tokens):
                    return "from_variant"

    return "no_source"


@dataclass
class GapReport:
    """缺口报告：过滤系统生成后的真实应填/已填统计。"""
    total_schema: int = 0
    system_generated: int = 0          # 不应填的
    should_fill: int = 0               # 应填总数
    filled: int = 0                    # 已填（成功映射）
    attempted: int = 0                 # 尝试过（含跳过）
    gap_list: List[dict[str, Any]] = field(default_factory=list)  # 未填明细 + source_hint
    # 来源分布
    source_distribution: Dict[str, int] = field(default_factory=dict)

    @property
    def attempted_fill_rate(self) -> float:
        """attempted / should_fill（分母>0 时）；即「尽力填满」的覆盖率。"""
        if self.should_fill <= 0:
            return 0.0
        return round(self.filled / self.should_fill, 4)


def compute_gap(
    schema: list[dict[str, Any]],
    draft: dict[str, Any],
    filled_ids: list[int] | set[int],
) -> GapReport:
    """计算单个产品的属性缺口。

    Args:
        schema: Ozon 属性 schema（/description-category/attribute 的 result）
        draft: 信封 draft（attributes/title/variants/ozon_attributes）
        filled_ids: 已成功映射进 payload 的属性 ID 集合

    Returns:
        GapReport（attempted_fill_rate = filled/should_fill）
    """
    report = GapReport()
    filled_set = {int(i) for i in (filled_ids or [])}
    for attr in schema or []:
        if not isinstance(attr, dict) or not attr.get("id"):
            continue
        report.total_schema += 1
        attr_id = int(attr["id"])
        if not should_fill(attr):
            report.system_generated += 1
            continue
        report.should_fill += 1
        if attr_id in filled_set:
            report.filled += 1
            continue
        # 未填：记录缺口 + 来源提示
        hint = _source_hint(attr, draft)
        report.source_distribution[hint] = report.source_distribution.get(hint, 0) + 1
        report.gap_list.append({
            "attr_id": attr_id,
            "name": str(attr.get("name") or ""),
            "type": str(attr.get("type") or ""),
            "dictionary_id": int(attr.get("dictionary_id") or 0),
            "is_required": bool(attr.get("is_required")),
            "source_hint": hint,
        })
    return report


def summarize_gaps(reports: list[GapReport]) -> dict[str, Any]:
    """多产品汇总：应填率均值 + 缺口来源分布 + Top 缺口属性。"""
    total_should = sum(r.should_fill for r in reports)
    total_filled = sum(r.filled for r in reports)
    agg: Dict[str, Any] = {
        "products": len(reports),
        "avg_schema": round(sum(r.total_schema for r in reports) / max(len(reports), 1), 1),
        "avg_should_fill": round(total_should / max(len(reports), 1), 1),
        "avg_filled": round(total_filled / max(len(reports), 1), 1),
        "attempted_fill_rate": round(total_filled / total_should, 4) if total_should else 0.0,
        "system_generated_per_product": round(sum(r.system_generated for r in reports) / max(len(reports), 1), 1),
        "source_distribution": {},
        "top_gap_attrs": {},
    }
    src_count: Dict[str, int] = {}
    attr_count: Dict[int, Dict[str, Any]] = {}
    for r in reports:
        for hint, n in r.source_distribution.items():
            src_count[hint] = src_count.get(hint, 0) + n
        for g in r.gap_list:
            d = attr_count.setdefault(g["attr_id"], {
                "name": g["name"], "count": 0, "required": g["is_required"],
                "dictionary_id": g["dictionary_id"],
            })
            d["count"] += 1
    agg["source_distribution"] = src_count
    agg["top_gap_attrs"] = {
        str(aid): d for aid, d in sorted(attr_count.items(), key=lambda kv: -kv[1]["count"])[:20]
    }
    return agg
