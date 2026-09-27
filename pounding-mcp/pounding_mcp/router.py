"""意图路由层 v2 —— 把用户自由中文输入路由到 skill 命令。

规则表优先：按 skill/SKILL.md §1 决策树 + references/commands-*.md 固化
（有 URL 先判类型 A/B/C/F；无 URL 按意图词 E/C/D；指代不清必须追问）。
本版纯规则，LLM 只留 needs_clarification 追问出口（后续 LLM 消歧层同接口，防漂移）。

v2 变更（handover-batch-v1）：
    ① A/B 强意图直提腿补 --wait（SKILL.md §1「graph --url --wait」/
    「follow --ozon-url --auto-submit --wait」逐行对齐；--wait=闸排队+提交后
    轮询终态，failed exit 3，符合 §3「明确上架意图→直提带 --wait」口径）；
    ② 新增 query 意图类（查任务进度，SKILL.md §1「查任务进度/完成了吗→
    query <task_id>」行）；③ v1 的「上传」词评估结论以测试锁定（见
    test_router_upload_image_locked_to_d1——图片意图分支先于 D 判定，无需改词表）。

输出 schema（见 docs/PLAN-conversation-entry-v1.md L86-88）：
    {
        "pipeline": "A"|"B"|"C"|"C2"|"D"|"D1"|"E"|"F"|"query"|"category"|"check"|"search"|"unknown",
        "command": str,
        "args": list[str],
        "needs_confirmation": bool,     # 写类命令（graph 提交/discover --auto-submit/批量）必须二次确认
        "needs_clarification": bool,    # 歧义/缺对象 → 不执行，用 questions 追问
        "questions": list[str],
    }
"""

from __future__ import annotations

import re

ROUTER_VERSION = "v2"

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

# ── 任务 ID 形态（v2 query 意图用）：worker 云任务=带连字符 uuid；本地采集箱短
# 任务=uuid4().hex[:12]（12 位 hex）；「≥6 位纯数字串」按口径兜底（用户手抄 ID）。
_RE_TASK_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_RE_TASK_HEX12 = re.compile(r"\b[0-9a-f]{12}\b", re.IGNORECASE)
_RE_TASK_DIGITS = re.compile(r"\b\d{6,}\b")

# ── 意图词表（规则表优先；长词在前避免子串吞词，如「搜一下」先于「搜」）
_IMAGE_WORDS = ("以图搜款", "图搜", "以图", "找款", "找同款", "同款", "图片", "照片")  # D1
_TREND_WORDS = ("有什么好卖的", "热卖", "爆款", "新品风向", "趋势", "卖得动")  # E
_LIST_WORDS = ("上架", "上货", "上点", "整一批", "发布", "上传")  # D
_AUTO_TASK_WORDS = ("自动采集", "自动选品", "全自动选品", "无人值守", "任务式", "自动跑一批")  # C2（漏斗 v2 discover-task）
_FOLLOW_WORDS = ("跟卖", "蓝海")  # C
_SELECT_WORDS = ("选品",)  # C
_COLLECT_WORDS = ("采集",)  # C
_CATEGORY_WORDS = ("查类目", "类目")
_CHECK_WORDS = ("检查", "诊断", "环境", "凭证")
_SEARCH_WORDS = ("搜索", "搜一下", "查一下", "找货源", "查找", "搜")
# v2 查进度意图（SKILL.md §1「查任务进度/完成了吗→query <task_id>」行）。
# 长词在前（防 _extract_keyword 子串吞词）；裸「进度」兜底放最后。
_QUERY_WORDS = (
    "查进度", "任务进度", "任务状态", "查任务", "查询任务",
    "跑到哪", "跑哪了", "处理完了吗", "处理好了吗",
    "完成了吗", "完成了没", "好了吗", "好了没", "怎么样了",
    "进度",
)
# v0.79 口径统一（PLAN-agent-ergonomics-v1 B2 / SKILL.md §1⑨）：URL + 弱化词 →
# 展示态（graph --no-submit / follow 不带 --auto-submit），不上架。
_WEAK_INTENT_WORDS = ("看看", "能不能上", "能上吗", "多少钱", "怎么样", "评估", "分析一下", "值不值得")

_ALL_INTENT_WORDS = (
    _IMAGE_WORDS
    + _TREND_WORDS
    + _LIST_WORDS
    + _AUTO_TASK_WORDS
    + _FOLLOW_WORDS
    + _SELECT_WORDS
    + _COLLECT_WORDS
    + _CATEGORY_WORDS
    + _CHECK_WORDS
    + _SEARCH_WORDS
    + _QUERY_WORDS
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
_QUESTION_TASK_ID = "请提供要查询的任务 ID（submit/graph --wait/job_status 输出里的 task_id，如 550e8400-e29b-… 或 12 位短 ID）"
_QUESTION_TARGET_COUNT = "要多少个符合要求的产品？（控制采集/达标数量，缺省 50；也可直接说「选品 30个 宠物用品」）"


def normalize_intent(text: str) -> str:
    """输入自由中文 → 规整：strip + 中文标点（，；、。）归一边界 + 空白折叠。"""
    t = (text or "").strip()
    for a, b in (("，", " "), ("；", " "), ("、", " "), ("。", " ")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _route(pipeline: str, command: str, args: list[str], *,
           needs_confirmation: bool = False, needs_clarification: bool = False,
           questions: list[str] | None = None, note: str = "") -> dict:
    return {
        "pipeline": pipeline,
        "command": command,
        "args": list(args),
        "needs_confirmation": needs_confirmation,
        "needs_clarification": needs_clarification,
        "questions": list(questions or []),
        "note": note,
    }


def _extract_keyword(text: str) -> str:
    """去掉所有意图词 + 衬词，剩余部分拼接为品类/搜索关键词。"""
    kw = text
    for w in _ALL_INTENT_WORDS:
        kw = kw.replace(w, " ")
    for s in _STOPWORDS:
        kw = kw.replace(s, " ")
    return "".join(kw.split())


def _extract_count(text: str) -> tuple[str, str]:
    """提取「N个」数量词，返回 (剩余文本, 数量字符串或空)。

    数量词不进关键词（否则「选品 30个 宠物用品」的关键词会带 30个）。
    """
    m = re.search(r"(\d+)\s*个", text)
    if not m:
        return text, ""
    return text[:m.start()] + " " + text[m.end():], m.group(1)


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


def _extract_task_id(text: str) -> str:
    """从原文提取任务 ID：带连字符 uuid → 12 位 hex（本地采集任务）→ ≥6 位纯数字串。

    12 位 hex 必须 ≥1 个 a-f 字母才算任务 ID——纯数字 12 位可能是 1688 item_id
    （如 980815374096），不能单独当任务凭证认领；纯数字串同理只在伴随进度词时
    才被采用（本函数的 digits 兜底仅由 query 词命中的分支消费）。
    """
    m = _RE_TASK_UUID.search(text)
    if m:
        return m.group(0)
    for m in _RE_TASK_HEX12.finditer(text):
        tok = m.group(0)
        if any(c in "abcdefABCDEF" for c in tok):
            return tok
    m = _RE_TASK_DIGITS.search(text)
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
        # v0.79 口径统一：URL + 弱化词 → 展示态（§1⑨）；明确意图 → 直接提交腿
        _weak = any(w in raw for w in _WEAK_INTENT_WORDS)
        if kind == "1688":
            if _weak:
                return _route("A", "graph", ["--url", url, "--no-submit"],
                              note="弱意图——先展示信封与预估，用户确认后再提交")
            # v2 对齐 SKILL.md §1「发 1688 链接 + 上架/整一批 → graph --url --wait」：
            # 直提腿带 --wait（闸排队+提交后轮询终态，failed exit 3），§3 二分法口径。
            return _route("A", "graph", ["--url", url, "--wait"])
        if kind == "ozon_product":
            if _weak:
                return _route("B", "follow", ["--ozon-url", url],
                              note="弱意图——follow 缺省即展示 1688 候选，用户确认后加 --auto-submit")
            # arch-findings 修复：强意图跟卖必须带 --auto-submit（follow 缺省只展示不提交，
            # 旧返回会导致 agent 照令牌执行后零动作，与 SKILL.md §1 二分法「明确意图→直提」相悖）
            # v2 再补 --wait（§1「follow --ozon-url <URL> --auto-submit --wait」逐字口径）。
            return _route("B", "follow", ["--ozon-url", url, "--auto-submit", "--wait"])
        return _route("C", "discover", ["--url", url])

    # ② 查进度意图（v2 新增，SKILL.md §1「查任务进度/完成了吗→query <task_id>」）。
    # 插在 URL 判定之后：进度问句贴的是 task_id 不是 URL，URL 优先级不变；插在
    # 图片意图之前：「…好了吗/进度」是问既有任务状态而非发起新图搜，缺 id 时
    # 追问 task_id（指代不清必须追问，禁止猜测执行）。已知取舍：极少数「环境
    # 好了吗」类问句会被本分支截住追问 task_id（check 词不占优）——宁可追问
    # 不猜跑，与模块「歧义→追问」原则一致。
    _task_id = _extract_task_id(raw)
    if any(w in raw for w in _QUERY_WORDS) or (_task_id and not _task_id.isdigit()):
        # 裸 ID 兜底：无进度词但出现 uuid/12 位 hex 任务凭证（含字母），按查进度
        # 处理（纯数字串不放行——可能只是 1688 item_id，让位给 search/unknown）。
        if _task_id:
            return _route("query", "query", [_task_id])
        return _route("query", "query", [], needs_clarification=True,
                      questions=[_QUESTION_TASK_ID])

    # ③ 图片意图（无 URL）→ D1：图搜结果须用户确认再 graph，绝不直接上架
    if any(w in raw for w in _IMAGE_WORDS):
        img = _find_image_url(raw)
        args = ["--image", img] if img else ["--image"]
        return _route("D1", "image_search", args, needs_confirmation=True,
                      questions=[] if img else [_QUESTION_IMAGE])

    # ④ 无 URL 按意图词优先级（SKILL.md ②）
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

    # 任务式全自动选品（漏斗 v2 discover-task）：缺省 dry_run 零副作用，
    # 无需确认；真实入箱由 agent 二次调用 to_box=True（触发 dsh 审批）。
    # v0.70 目标驱动：用户没说数量必须先问（--target-count=达标数），说了直接带参。
    if any(w in raw for w in _AUTO_TASK_WORDS):
        rest, cnt = _extract_count(raw)
        kw = _extract_keyword(rest)
        args = (["--keyword", kw] if kw else []) + \
               (["--target-count", cnt] if cnt else [])
        if cnt:
            return _route("C2", "discover_task", args)
        questions = [_QUESTION_TARGET_COUNT] + ([] if kw else [_QUESTION_CATEGORY])
        return _route("C2", "discover_task", args, needs_clarification=True,
                      questions=questions)

    # 跟卖/蓝海/选品/采集 → C（discover 跟卖选品，仅采集不提交，无需确认）。
    # v0.70：「N个」数量词 → --max-products；未说数量先追问（目标不明确宁可问）。
    if any(w in raw for w in _FOLLOW_WORDS + _SELECT_WORDS + _COLLECT_WORDS):
        rest, cnt = _extract_count(raw)
        kw = _extract_keyword(rest)
        args = (["--keyword", kw] if kw else []) + \
               (["--max-products", cnt] if cnt else [])
        if cnt:
            return _route("C", "discover", args)
        questions = [_QUESTION_TARGET_COUNT] + ([] if kw else [_QUESTION_CATEGORY])
        return _route("C", "discover", args, needs_clarification=True,
                      questions=questions)

    if any(w in raw for w in _SEARCH_WORDS):
        kw = _extract_keyword(raw)
        if kw:
            return _route("search", "search", [kw])
        return _route("search", "search", [], needs_clarification=True,
                      questions=[_QUESTION_CATEGORY])

    # ⑤ 指代不清 / 无对象 → 追问核对，禁止猜测执行（SKILL.md ③）
    return _route("unknown", "", [], needs_clarification=True,
                  questions=[_QUESTION_NEED_OBJECT])
