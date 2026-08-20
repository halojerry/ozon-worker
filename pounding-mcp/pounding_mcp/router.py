"""意图路由层 v1 —— 把用户自由中文输入路由到 skill 命令。

规则表优先：按 skill/SKILL.md §1 决策树 + references/command-reference.md 固化
（有 URL 先判类型 A/B/C/F；无 URL 按意图词 E/C/D；指代不清必须追问）。
本版纯规则，LLM 只留 needs_clarification 追问出口（后续 LLM 消歧层同接口，防漂移）。

输出 schema（见 docs/PLAN-conversation-entry-v1.md L86-88）：
    {
        "pipeline": "A"|"B"|"C"|"D"|"D1"|"E"|"F"|"category"|"check"|"search"|"unknown",
        "command": str,
        "args": list[str],
        "needs_confirmation": bool,     # 写类命令（graph 提交/discover --auto-submit/批量）必须二次确认
        "needs_clarification": bool,    # 歧义/缺对象 → 不执行，用 questions 追问
        "questions": list[str],
    }
"""

from __future__ import annotations

import re

ROUTER_VERSION = "v1"

# ── URL 正则（① 有 URL 先判类型：1688 商品页 → A / Ozon 商品页 → B / Ozon 搜索类目页 → C）
_RE_1688 = re.compile(
    r"(?:https?://)?(?:www\.)?detail\.1688\.com/offer/\d+[A-Za-z0-9._~:/?#@!$&'()*+;=%\-]*"
)
_RE_OZON_PRODUCT = re.compile(
    r"(?:https?://)?(?:www\.)?ozon\.ru/product/\d+[A-Za-z0-9._~:/?#@!$&'()*+;=%\-]*"
)
_RE_OZON_LIST = re.compile(
    r"(?:https?://)?(?:www\.)?ozon\.ru/(?:search|category)[A-Za-z0-9._~:/?#@!$&'()*+;=%\-]*"
)
_RE_IMAGE_URL = re.compile(
    r"(?:https?://)[^\s，。；、]+\.(?:png|jpe?g|webp)[A-Za-z0-9._~:/?#@!$&'()*+;=%\-]*",
    re.IGNORECASE,
)

# ── 意图词表（规则表优先；长词在前避免子串吞词，如「搜一下」先于「搜」）
_IMAGE_WORDS = ("以图搜款", "图搜", "以图", "找款", "找同款", "同款", "图片", "照片")  # D1
_TREND_WORDS = ("有什么好卖的", "热卖", "爆款", "新品风向", "趋势", "卖得动")  # E
_LIST_WORDS = ("上架", "上货", "上点", "整一批", "发布", "上传")  # D
_FOLLOW_WORDS = ("跟卖", "蓝海")  # C
_SELECT_WORDS = ("选品",)  # C
_COLLECT_WORDS = ("采集",)  # C
_CATEGORY_WORDS = ("查类目", "类目")
_CHECK_WORDS = ("检查", "诊断", "环境", "凭证")
_SEARCH_WORDS = ("搜索", "搜一下", "查一下", "找货源", "查找", "搜")

_ALL_INTENT_WORDS = (
    _IMAGE_WORDS
    + _TREND_WORDS
    + _LIST_WORDS
    + _FOLLOW_WORDS
    + _SELECT_WORDS
    + _COLLECT_WORDS
    + _CATEGORY_WORDS
    + _CHECK_WORDS
    + _SEARCH_WORDS
)

# 关键词提取时剔除的口语/衬词（⚠️ 单字衬词须谨慎：不能含「用」（"用品"类目后缀））
_STOPWORDS = (
    "帮我", "请", "一下", "把", "这个", "这些", "那个", "的", "要", "想", "给我",
    "看看", "查", "找", "弄", "搞", "做", "就", "来", "去", "吧", "呢", "吗",
    "了", "你好", "您好",
)

_QUESTION_NEED_OBJECT = "请提供 1688 链接 / Ozon 链接 / 商品图片 / 品类关键词"
_QUESTION_TREND = "请提供品类，我先 web_search 分析趋势再 discover"
_QUESTION_CATEGORY = "请提供品类关键词（如：宠物用品）"
_QUESTION_IMAGE = "请提供商品图片（URL 或本地路径）"


def normalize_intent(text: str) -> str:
    """输入自由中文 → 规整：strip + 中文标点（，；、。）归一边界 + 空白折叠。"""
    t = (text or "").strip()
    for a, b in (("，", " "), ("；", " "), ("、", " "), ("。", " ")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _route(pipeline: str, command: str, args: list[str], *,
           needs_confirmation: bool = False, needs_clarification: bool = False,
           questions: list[str] | None = None) -> dict:
    return {
        "pipeline": pipeline,
        "command": command,
        "args": list(args),
        "needs_confirmation": needs_confirmation,
        "needs_clarification": needs_clarification,
        "questions": list(questions or []),
    }


def _extract_keyword(text: str) -> str:
    """去掉所有意图词 + 衬词，剩余部分拼接为品类/搜索关键词。"""
    kw = text
    for w in _ALL_INTENT_WORDS:
        kw = kw.replace(w, " ")
    for s in _STOPWORDS:
        kw = kw.replace(s, " ")
    return "".join(kw.split())


def _extract_urls(text: str) -> list[tuple[str, str]]:
    """扫描文本中的 1688/Ozon URL，返回 [(type, url)]。"""
    hits: list[tuple[str, str]] = []
    for m in _RE_1688.finditer(text):
        hits.append(("1688", m.group(0)))
    for m in _RE_OZON_PRODUCT.finditer(text):
        hits.append(("ozon_product", m.group(0)))
    for m in _RE_OZON_LIST.finditer(text):
        hits.append(("ozon_list", m.group(0)))
    return hits


def _find_image_url(text: str) -> str:
    m = _RE_IMAGE_URL.search(text)
    return m.group(0) if m else ""


def route_intent(text: str) -> dict:
    """自由中文输入 → 路由结果 dict（schema 见模块 docstring）。"""
    raw = normalize_intent(text)
    if not raw:
        return _route("unknown", "", [], needs_clarification=True,
                      questions=[_QUESTION_NEED_OBJECT])

    hits = _extract_urls(raw)
    # ① 有 URL 先判类型；≥2 个 → 批量 F（需确认写 urls 文件）
    if len(hits) >= 2:
        return _route("F", "batch_test.py", ["--urls-file", "urls.txt"],
                      needs_confirmation=True)
    if hits:
        kind, url = hits[0]
        if kind == "1688":
            return _route("A", "graph", ["--url", url])
        if kind == "ozon_product":
            return _route("B", "follow", ["--ozon-url", url])
        return _route("C", "discover", ["--url", url])

    # ② 图片意图（无 URL）→ D1：图搜结果须用户确认再 graph，绝不直接上架
    if any(w in raw for w in _IMAGE_WORDS):
        img = _find_image_url(raw)
        args = ["--image", img] if img else ["--image"]
        return _route("D1", "image_search", args, needs_confirmation=True,
                      questions=[] if img else [_QUESTION_IMAGE])

    # ③ 无 URL 按意图词优先级（SKILL.md ②）
    # 趋势选品：命令层无 trend，须先 web_search + LLM 提炼 → 本层纯规则只能追问
    if any(w in raw for w in _TREND_WORDS):
        return _route("E", "discover", [], needs_clarification=True,
                      questions=[_QUESTION_TREND])

    if any(w in raw for w in _CATEGORY_WORDS):
        kw = _extract_keyword(raw)
        if kw:
            return _route("category", "category", [kw])
        return _route("category", "category", [], needs_clarification=True,
                      questions=[_QUESTION_CATEGORY])

    if any(w in raw for w in _CHECK_WORDS):
        return _route("check", "check", [])

    # 上架 → D：写类命令，必须确认后才真正提交
    if any(w in raw for w in _LIST_WORDS):
        kw = _extract_keyword(raw)
        args = ["--keyword", kw, "--auto-submit"] if kw else ["--auto-submit"]
        return _route("D", "discover", args, needs_confirmation=True,
                      questions=[] if kw else [_QUESTION_CATEGORY])

    # 跟卖/蓝海/选品/采集 → C（discover 跟卖选品，仅采集不提交，无需确认）
    if any(w in raw for w in _FOLLOW_WORDS + _SELECT_WORDS + _COLLECT_WORDS):
        kw = _extract_keyword(raw)
        if kw:
            return _route("C", "discover", ["--keyword", kw])
        return _route("C", "discover", [], needs_clarification=True,
                      questions=[_QUESTION_CATEGORY])

    if any(w in raw for w in _SEARCH_WORDS):
        kw = _extract_keyword(raw)
        if kw:
            return _route("search", "search", [kw])
        return _route("search", "search", [], needs_clarification=True,
                      questions=[_QUESTION_CATEGORY])

    # ⑤ 指代不清 / 无对象 → 追问核对，禁止猜测执行（SKILL.md ③）
    return _route("unknown", "", [], needs_clarification=True,
                  questions=[_QUESTION_NEED_OBJECT])
