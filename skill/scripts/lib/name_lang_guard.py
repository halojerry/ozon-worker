# -*- coding: utf-8 -*-
"""克隆选品源名语言前置守卫（feat/multi-sku-9048-only 任务 2）。

双 gate 铁证 2：克隆模式（follow --clone，import-by-sku 官方复制竞品卡、零 LLM）
源卡名为拉丁字符 → worker LOCAL_NAME_LATIN 秒拒终态（unfixable：克隆零 LLM
不可译，禁中文属性翻译空转，validation_retry_loop 错误映射表在案）；正对照
证明西里尔名竞品复制链路 happy path 存在。缺的是选品边界前置过滤——本模块
把判定前移到 skill：CDP 抓到竞品卡名后即可判，提交前默认阻断，
``--allow-latin-name`` 显式豁免（豁免不改结果，仍走必拒路径，仅用户知情）。

职责边界（cloud_probe 冻结增长纪律——判定/文案全在本模块，follow 腿只留
薄调用点）：纯函数层，零网络/零 IO/零 LLM，全部可离线单测。

判定口径（对齐任务书）：字母字符里拉丁占比 > 50% **且** 无西里尔 → 拦截。
数字/标点/空白不计入占比基数；无字母或无证据 → fail-open 放行（worker 侧
LOCAL_NAME_LATIN 终态守卫仍在，双保险不松）。
"""
from __future__ import annotations

import unicodedata
from typing import Any

# 拉丁占比阈值（>50% 才拦；恰好 50% 不拦）
LATIN_RATIO_THRESHOLD = 0.5


def _script_of(ch: str) -> str:
    """单字符 Unicode script 名（LATIN/CYRILLIC/…；非字母返回空串）。"""
    if not ch.isalpha():
        return ""
    return unicodedata.name(ch, "").split(" ")[0]


def name_language_verdict(name: str) -> dict[str, Any]:
    """竞品卡名语言形态判定。

    返回::

        {"latin_only": bool,      # 拉丁占比>阈值 且 无西里尔（拦截判据）
         "latin_ratio": float,    # 拉丁字母 / 全部字母（无字母=0.0）
         "latin_count": int, "cyrillic_count": int, "letter_count": int}
    """
    text = str(name or "")
    scripts = [_script_of(c) for c in text]
    letters = [s for s in scripts if s]
    latin = sum(1 for s in letters if s == "LATIN")
    cyrillic = sum(1 for s in letters if s == "CYRILLIC")
    ratio = (latin / len(letters)) if letters else 0.0
    return {
        "latin_only": bool(letters) and cyrillic == 0 and ratio > LATIN_RATIO_THRESHOLD,
        "latin_ratio": round(ratio, 4),
        "latin_count": latin,
        "cyrillic_count": cyrillic,
        "letter_count": len(letters),
    }


def latin_name_block_reason(name: str) -> str:
    """克隆源名拉丁阻断文案（可行动指引；非拉丁名返回空串=放行）。"""
    v = name_language_verdict(name)
    if not v["latin_only"]:
        return ""
    return (
        f"竞品卡名为拉丁字符（latin={v['latin_ratio']:.0%}，无西里尔）→ "
        "worker LOCAL_NAME_LATIN 必拒终态（克隆零 LLM 不可译）。"
        "请换西里尔（俄语）名竞品重跑；确要强行提交可加 --allow-latin-name "
        "显式豁免（仍必拒，仅用户知情留痕）"
    )
