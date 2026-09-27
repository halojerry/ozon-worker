"""标题↔类目一致性检查的西里尔同义豁免词表——两闸共用唯一加载器。

背景（fix/category-root-cause-v1，2026-09-26 实机 gate 取证）：
    - 任务 37ee72d9：女衬衫 «Блузка…» 被配到同父隔壁叶 «Рубашка»(93209)，正确叶是
      «Блузка»(93048)——Ozon 官方 ZH 名把 Блузка 译成「短衫」，中文源词永远召回不到，
      译词 «Блузка» × 类目词 «Рубашка» 零词面交集 → 被一致性闸误杀。
    - 消费方（必须两处共用本加载器，严禁各自实现造成算法再漂移）：
      1. graphs/nodes/ozon_validate_node.py  common_cyr_words（词级判等/前缀处）
      2. graphs/nodes/assemble_ozon_product_node.py  _check_category_consistency（类目词集构建处）

语义（保守边界）：
    - 词根命中：token 与组内词条任一方向 startswith 且较短方 ≥4 字符 → 视为同组
      （西里尔词形变体 блузка/блузки 由前缀天然覆盖）。
    - <4 字符的 token 恒不参与（与 validate 的 _MIN_COMMON_WORD_LEN 对齐）。
    - 词表只做「放行面」的扩张，绝不制造新的拦截；加载失败静默退化为空表（宁严勿松）。

红线（改词条前必读）： держатель↔полка 这类「非同义对」严禁入表——它们零交集被拦
是防线在干活（2026-09-26 主店铺 gate 8 单真错配即证据）。
"""
from __future__ import annotations

import json
import os
import threading
from functools import lru_cache

_LEXICON_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "config", "category_consistency_lexicon.json"))
_MIN_STEM_LEN = 4

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_groups() -> tuple[tuple[str, ...], ...]:
    """读 config/category_consistency_lexicon.json 的 groups；缺失/损坏 → 空表（宁严勿松）。"""
    try:
        with open(_LEXICON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        groups = data.get("groups") or []
        out = []
        for g in groups:
            stems = tuple(str(w).lower().strip() for w in g if str(w).strip())
            if len(stems) >= 2:
                out.append(stems)
        return tuple(out)
    except Exception:
        return ()


def _is_stem_match(token: str, stem: str) -> bool:
    """token 与词根的共同前缀 ≥4 字符即视为同组（与 validate 的前缀≥4 约定一致，天然覆盖词形变体 блузка/блузки）。"""
    if not token or not stem:
        return False
    common = os.path.commonprefix([token, stem])
    return len(common) >= _MIN_STEM_LEN


def related_words(word: str) -> frozenset[str]:
    """返回与 word 同组的全部词根（含自身小写）；无组则只含自身。线程安全。"""
    w = (word or "").lower().strip()
    if len(w) < _MIN_STEM_LEN:
        return frozenset({w}) if w else frozenset()
    with _lock:
        groups = _load_groups()
    for group in groups:
        if any(_is_stem_match(w, stem) for stem in group):
            return frozenset(group)
    return frozenset({w})


def sets_overlap(words_a, words_b) -> bool:
    """两组词经词表扩展后是否存在交集——一致性闸的放行面判定入口。

    用法（validate 侧伪码）：
        if common_cyr_words(title, path): return True          # 原判等先走
        if sets_overlap(_cyr_words(title), _path_words(path)): return True  # 词表豁免
    """
    exp_a: set[str] = set()
    for w in words_a or ():
        exp_a.update(related_words(w))
    exp_b: set[str] = set()
    for w in words_b or ():
        exp_b.update(related_words(w))
    return bool(exp_a & exp_b)
