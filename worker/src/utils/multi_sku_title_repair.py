"""multi_sku 标题修复变体盲守卫（fix/multi-sku-9048-fixes，纯函数层）。

背景（2026-10 9048-only 合卡终验实录）：7 变体 9048-only 合卡（变体区分 =
prepare 展开写入的 per-item 标题色尾缀「，俄语色名」，见
utils/multi_sku_expand.expand_items_9048_only）经 retry-loop
error_repair_llm_node 的「LLM 重生成标题」修复路径后，**所有 items 的 name
被整体抹平成同一标题**——尾缀丢失 → items 同质 → Ozon 不并卡
（终验实录：7 变体「✅ 标题已修复（所有变体）」→ 6 张独立卡）。

本模块 = retry-loop 两处标题写回点（LLM 修复 / 强制俄语标题生成）共用的
multi_sku 守卫（调用方仅 items>1 进入；单 SKU 路径零变化，回归锁）：
- ``targeted``：报错 item 可定位（错误文本的 ``item[N]`` / offer_id 引用）
  且变体尾缀可程序化还原（全 items 公共前缀后的独立尾段）→ 只修报错 item，
  修复产物保留该 item 原尾缀：``f"{新标题}, {原尾缀}"``；
- ``deterministic``（宁缺毋滥降级）：报错 item 无法定位 / 尾缀无法还原 →
  **绝不抹平重生成**，只做确定性清理（删拉丁字符 + 归并残留分隔 + 去首尾
  空白），每 item 独立处理（各自尾缀原样保留），清空结果不写。

分层纪律（对齐 multi_sku_expand）：纯函数，零网络/零 LLM/零 IO，不 import
graphs/api（W3a 依赖方向），全部可离线单测。
"""
from __future__ import annotations

import logging
import os.path
import re
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)

# 动作码（apply_title_repair 返回，调用方据此 log）
ACTION_TARGETED = "targeted"            # 只修报错 item + 尾缀保留
ACTION_DETERMINISTIC = "deterministic"  # 降级确定性清理（绝不抹平）
ACTION_UNCHANGED = "unchanged"          # 确定性清理无实质变化

# Ozon 错误文本的 item 下标引用（惯用形态：item[0].attributes[...] / item[2].name）
_ITEM_REF_RE = re.compile(r"item\[(\d+)\]")
# offer_id 引用边界（防 base_offer_id 子串误配：1083073125898 vs 1083073125898_1）
_OFFER_BOUNDARY_L = re.compile(r"[0-9A-Za-z_\-]")
# 确定性清理：删拉丁字符（连词整词删，避免字母碎片）
_LATIN_RUN_RE = re.compile(r"[A-Za-z]+")
_SPACE_RUN_RE = re.compile(r"\s{2,}")
# 删拉丁词后残留的重复分隔（「Набор,  , хвост」→「Набор, хвост」）
_COMMA_RUN_RE = re.compile(r"(?:,\s*){2,}")
# 删拉丁词后残留的逗号前空格（「Набор , хвост」→「Набор, хвост」）
_SPACE_BEFORE_COMMA_RE = re.compile(r"\s+,")
_EDGE_JUNK = " \t\r\n,-–—;；、"
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")


def _iter_texts(*sources: Any) -> List[str]:
    """递归抽取错误结构里的全部字符串（error_message 字符串 / errors 条目
    dict（texts.message 等）/ decline_errors 混合形态）。"""
    out: List[str] = []

    def _walk(v: Any) -> None:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                _walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                _walk(x)

    for s in sources:
        _walk(s)
    return out


def find_errored_item_indexes(
    items: List[Dict[str, Any]],
    error_texts: Iterable[Any],
) -> List[int]:
    """从错误文本 best-effort 定位报错 item 下标：
    ① Ozon 文本 ``item[N]`` 引用；
    ② 错误文本含某 item 的 offer_id（多 item import 的常见定位形态，
       词边界匹配防 base_offer_id 子串误配）。
    返回范围内下标升序表；无法定位 → 空表（调用方降级确定性清理）。
    """
    blob = "\n".join(t for t in error_texts if t)
    if not blob:
        return []
    idxs: set = set()
    for m in _ITEM_REF_RE.finditer(blob):
        try:
            idxs.add(int(m.group(1)))
        except ValueError:
            continue
    for i, it in enumerate(items or []):
        offer = str((it or {}).get("offer_id") or "").strip()
        if len(offer) < 3:
            continue  # 过短 offer 子串误配风险高，不采信
        pat = re.compile(
            r"(?<![0-9A-Za-z_\-])" + re.escape(offer) + r"(?![0-9A-Za-z_\-])"
        )
        if pat.search(blob):
            idxs.add(i)
    return sorted(i for i in idxs if 0 <= i < len(items or []))


def derive_variant_suffixes(names: List[str]) -> Dict[int, str]:
    """从 items 的 name 列表还原 per-item 变体尾缀（纯确定性）。

    9048-only 展开形态 name = ``f"{base}, {俄语色名}"``——色名尾段是 items
    间唯一差异。还原算法（两级）：
    ① 全 names 最长公共前缀收口到最后一个「, 」分隔边界 → base；item 尾缀 =
       name 去 base 与前导分隔后的剩余段（base 自身含「, 」也正确）；
    ① 失败（混合基名/历史修复后公共前缀不可辨）→ 逐 item 末段兜底：
       ``name.rsplit(", ", 1)``，全部 items 都有非空尾段才可信
       （9048-only 展开恒以「, 色名」结尾）。

    返回空 dict = 无法程序化区分尾缀（items 全同名（颜色字典模式同名展开或
    已被抹平）/ 无分隔边界 / 任一 item 无尾段）——调用方必须宁缺毋滥降级
    确定性清理，**绝不猜尾缀、绝不抹平**。
    """
    clean = [str(n or "").strip() for n in names]
    if len(clean) < 2 or len(set(clean)) < 2:
        return {}
    cp = os.path.commonprefix(clean)
    cut = cp.rfind(", ")
    if cut > 0:
        base = cp[:cut]
        suffixes: Dict[int, str] = {}
        for i, n in enumerate(clean):
            tail = n[len(base):].lstrip(_EDGE_JUNK).strip()
            if not tail:
                suffixes = {}
                break
            suffixes[i] = tail
        if suffixes:
            return suffixes
    # 兜底：逐 item 末段（全有尾段才可信，任一缺失 → 整体不可辨）
    fallback: Dict[int, str] = {}
    for i, n in enumerate(clean):
        tail = n.rsplit(", ", 1)[1].strip() if ", " in n else ""
        if not tail:
            return {}
        fallback[i] = tail
    return fallback


def deterministic_title_clean(name: str) -> str:
    """确定性标题清理（降级修复产物，per-item 独立）：删拉丁字符 → 归并残留
    空白/重复分隔 → 去首尾空白。结果为空或无西里尔 → 返回原值（绝不把
    name 清成空/非俄语残壳）。"""
    if not name:
        return name
    cleaned = _LATIN_RUN_RE.sub("", str(name))
    cleaned = _SPACE_RUN_RE.sub(" ", cleaned)
    cleaned = _COMMA_RUN_RE.sub(", ", cleaned)
    cleaned = _SPACE_BEFORE_COMMA_RE.sub(",", cleaned)
    cleaned = cleaned.strip(_EDGE_JUNK)
    if not cleaned or not _CYRILLIC_RE.search(cleaned):
        return name
    return cleaned


def apply_title_repair(
    items: List[Dict[str, Any]],
    repaired_title: str,
    *,
    error_message: Any = "",
    errors: Iterable[Any] = (),
    decline_errors: Iterable[Any] = (),
) -> str:
    """multi_sku（items>1）标题修复守卫入口（retry-loop 两处写回点共用）。

    - ``targeted``：报错 item 可定位 + 尾缀可还原 → 只写报错 item 的
      name = ``f"{repaired_title}, {原尾缀}"``（repaired_title 已含该尾缀则
      不重复追加），其余 items 原名不动；
    - ``deterministic``：无法定位报错 item / 尾缀不可还原 → LLM 重生成标题
      整体弃用（绝不抹平），每 item 独立确定性清理（各自尾缀原样保留）；
    - ``unchanged``：确定性清理无实质变化。

    返回动作码供调用方 log 与 4180 同步闸（仅 targeted/单 SKU 采纳的标题
    才同步 final_attributes 4180，降级路径弃用的残标题不写）。
    """
    if not items:
        return ACTION_UNCHANGED
    targets = find_errored_item_indexes(
        items, _iter_texts(error_message, errors, decline_errors))
    suffixes = derive_variant_suffixes(
        [it.get("name") if isinstance(it, dict) else "" for it in items])
    repaired = str(repaired_title or "").strip()

    if targets and suffixes:
        for i in targets:
            sfx = suffixes.get(i, "")
            if repaired and sfx and not repaired.endswith(f", {sfx}") and repaired != sfx:
                items[i]["name"] = f"{repaired}, {sfx}"
            elif repaired:
                items[i]["name"] = repaired
        logger.info(
            "[multi_sku_title_repair] targeted: 报错 item=%s 修复并保留原尾缀（其余 %d 个 item 不动）",
            targets, len(items) - len(targets),
        )
        return ACTION_TARGETED

    # 宁缺毋滥降级：绝不抹平重生成——LLM 标题整体弃用，只做确定性清理
    changed = False
    for it in items:
        if not isinstance(it, dict):
            continue
        old = str(it.get("name") or "")
        new = deterministic_title_clean(old)
        if new != old:
            it["name"] = new
            changed = True
    action = ACTION_DETERMINISTIC if changed else ACTION_UNCHANGED
    logger.warning(
        "[multi_sku_title_repair] %s: 报错 item=%s 尾缀可还原=%s → 拒绝抹平重生成"
        "（LLM 标题弃用），per-item 确定性清理（删拉丁/去尾空白）",
        action, targets or "不可定位", bool(suffixes),
    )
    return action
