"""同款确认与跨平台选优 — discover 跨平台静默货源匹配 v1 批2（纯函数）。

消费批1 ``cross_source_search.SourceOffer``，给批3 discover 接线用。三个纯函数：
- ``confirm_same_product``：跨平台候选标题 vs 1688 命中标题的同款置信度 ∈ [0,1]
  （换源安全底线：静默自动模式下**错换款的代价高于少换款**——低于阈值宁保 1688）；
- ``pick_best_source``：到手价（price+freight）选优，显著更便宜才换源
  （``SWITCH_MIN_RATIO`` 默认 0.90 即便宜 ≥10%——防抖动、防关键词噪音假便宜），
  平手保 1688，销量作平级 tie-break，并产出批3 快照所需的每平台最优 + 全量确认分；
- ``extract_search_keyword``：从 1688 命中标题剥修饰词取核心词（跨平台搜索框用）。

实现纪律：
- **零第三方依赖**——skill 侧不引 jieba（``match_scoring.py`` 先例：「不引 jieba，
  编译面最小」）。中文走仓库既有轻分词口径（``ozon_discovery.verify_1688_match``
  先例：标点切块 + CJK 2-gram，无需子串桥——CJK 统一 2-gram 后包含关系即相等）；
  规格词（材质牌号/容量/型号）用窄正则**归一**（「500ml」↔「500毫升」同键）后
  **独立加权**——同款确认的本质信号是规格一致，不是泛词 overlap。
- **阈值 env 覆盖调用时读取**（``CROSS_SOURCE_SAME_MIN`` / ``CROSS_SOURCE_SWITCH_RATIO``
  ——``_guardrail_threshold``「即时生效」先例；非数值/≤0 一律回退默认，配置损坏
  不得改变护栏行为）。
- **字段缺=None 绝不 0**（批1 纪律延续）：运费未知按 0 参与比较但必须带
  ``freight_unknown`` 标记；price 缺失直接失去选优资格。
- ``_MODIFIER_WORDS``：worker ``ozon_category_query.py`` 同名表（v0.65.1）的**平台
  无关瘦身版**——那边服务 Ozon 类目搜索语境、勿改原表；这里只服务淘宝/pdd 搜索框，
  CJK 修饰词剥子串、拉丁修饰词仅整词匹配（防误伤英文品牌词）。

worker 零改动；批3 快照 ``discovery_meta.source_comparison`` 消费 ``Decision``。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from scripts.lib.cross_source_search import SourceOffer

# ────────────────────────── 阈值（env 覆盖，调用时读取）──────────────────────────

SAME_PRODUCT_MIN = 0.45   # 同款确认门槛：低于此分不视为同款 → 宁保 1688
SWITCH_MIN_RATIO = 0.90   # 换源门槛：胜者到手价 < 1688×0.90（显著便宜 ≥10%）才换

ENV_SAME_MIN = "CROSS_SOURCE_SAME_MIN"
ENV_SWITCH_RATIO = "CROSS_SOURCE_SWITCH_RATIO"


def _env_positive_float(name: str, default: float, *, maximum: float = 1.0) -> float:
    """env 阈值读取：空/非数值/≤0 回退默认；超上限 clamp 到 ``maximum``
    （Fix Round 1 Minor #2——ratio >1.0 会「换更贵的也换源」、same_min >1.0
    无意义：分数上限就是 1.0。可接受域 (0, maximum]，配置损坏不得放大风险）。
    """
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(value, maximum) if value > 0 else default


# ────────────────────────── 规格词归一（窄正则，剥出式）──────────────────────────

# 剥出顺序即优先级：材质牌号 → 容量 → 型号——先剥者不再被后者重复消费。
# ⚠️ 牌号必须最先：「316l」若先走容量正则会被「数字+l」吞成 316 升。
# 牌号独立词边界（lookbehind/lookahead [0-9a-z]）——「2019」「1500ml」绝不误命中。
_SPEC_GRADE_RE = re.compile(r"(?<![0-9a-z])(304|316l|316|430|201)(?![0-9a-z])",
                            re.IGNORECASE)
_SPEC_VOLUME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(毫升|ml|升|l)(?![0-9a-z])",
                             re.IGNORECASE)
_SPEC_MODEL_RE = re.compile(r"型号\s*[:：]?\s*([0-9a-z][0-9a-z]*(?:-[0-9a-z]+)*)",
                            re.IGNORECASE)


def _spec_ml(value: str, unit: str) -> int:
    """容量归一到 ml 整数（「1.5l」→1500、「500毫升」→500）。"""
    n = float(value)
    if unit.lower() in ("l", "升"):
        n *= 1000.0
    return int(round(n))


# 标点/空白切块（verify_1688_match 同款分隔集，含 CJK 标点）
_SPLIT_RE = re.compile(r"[\s\-_,./\\()（）\[\]【】、，。：:;；！!？?\"'`~*|+]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

# 停用切块词（verify_1688_match noise 先例——只影响切块，2-gram 不套用）
_NOISE_TOKENS = frozenset({"的", "了", "是", "在", "有", "和", "与", "及", "或", "等"})


def _tokens_and_specs(text: str) -> tuple[set[str], set[str]]:
    """标题 → (词元集, 规格集)。

    词元 = CJK 2-gram + 字母数字切块 + **归一规格词**（``vol:500`` /
    ``grade:316`` / ``model:a500``——与 2-gram 同池参与 overlap，「500ml」↔
    「500毫升」得以同键命中）；规格集单独返回供加权。规格词从原文**剥出**再切块
    （避免「500ml」残留切块造成同义不同串）；纯 CJK 切块块不再整块入集（2-gram
    已覆盖——整块会让长标题的 min 分母虚胖）。
    """
    t = str(text or "").lower().strip()
    tokens: set[str] = set()
    specs: set[str] = set()
    if not t:
        return tokens, specs

    for m in _SPEC_GRADE_RE.finditer(t):
        specs.add(f"grade:{m.group(1)}")
    t = _SPEC_GRADE_RE.sub(" ", t)
    for m in _SPEC_VOLUME_RE.finditer(t):
        specs.add(f"vol:{_spec_ml(m.group(1), m.group(2))}")
    t = _SPEC_VOLUME_RE.sub(" ", t)
    for m in _SPEC_MODEL_RE.finditer(t):
        specs.add(f"model:{m.group(1)}")
    t = _SPEC_MODEL_RE.sub(" ", t)

    for raw in _SPLIT_RE.split(t):
        raw = raw.strip()
        if len(raw) >= 2 and raw not in _NOISE_TOKENS and not _CJK_RE.fullmatch(raw):
            tokens.add(raw)
    for seg in _CJK_RE.findall(t):
        for i in range(len(seg) - 1):
            tokens.add(seg[i:i + 2])
    return tokens, specs


# ────────────────────────── confirm_same_product ──────────────────────────

# 规格权重：候选声明了规格时，final = base × (0.65 + 0.35 × 规格命中率)——
# 规格全不符压到 0.65 倍；候选未声明规格 → 中性 1.0（绝不因「没写」惩罚）。
# ⚠️ 乘法惩罚只是一档——Fix Round 1（2026-09-10 review Important #1）实证：
# 真实标题整段重叠（「不锈钢保温杯500ml」）会把 base 抬到 ≥0.69，0.65×base
# 压不住，「316不锈钢保温杯500ml」vs「304…同文」曾 0.7071 过阈。所以**同类型
# 规格矛盾一票否决**（见 SPEC_CONTRADICTION_CAP）——乘法只兜「缺声明」场景。
_SPEC_FACTOR_BASE = 0.65
_SPEC_FACTOR_SPAN = 0.35

# 同类型规格矛盾封顶（fix round 1 Important #1）：牌号↔牌号 / 容量↔容量 /
# 型号↔型号 **双方都显式声明**且无交集 → 一票否决封顶 0.30（深掉阈下，不是乘
# 0.65——同款确认是换源安全底线，错换款的代价高于少换款）。矛盾判定复用上方
# 三个规格提取器（归一已处理 316l/毫升↔ml——不归一的不算矛盾），绝不另造一套；
# 不要用调 SAME_PRODUCT_MIN 修这类错配（治标且伤真同款）。
SPEC_CONTRADICTION_CAP = 0.30

_SPEC_TYPE_PREFIXES = ("vol:", "grade:", "model:")


def _has_same_type_contradiction(a_specs: set[str], b_specs: set[str]) -> bool:
    """双方对同一类型规格都显式声明且无交集 → 矛盾（316 vs 304、500ml vs 1500ml）。

    候选声明多个值（「304/316」）时 offer 命中其一即不算矛盾（不误伤宽声明）。
    """
    for prefix in _SPEC_TYPE_PREFIXES:
        a_t = {s for s in a_specs if s.startswith(prefix)}
        b_t = {s for s in b_specs if s.startswith(prefix)}
        if a_t and b_t and not (a_t & b_t):
            return True
    return False


def confirm_same_product(candidate_title_zh: str, offer_title: str | None) -> float:
    """跨平台候选标题 vs 1688 命中标题的同款置信度 ∈ [0,1]。

    - 任一标题空/None → 0.0（无证据 ≠ 同款）；
    - base = |词元交集| / min(|a|,|b|)（verify_1688_match 口径，cap 1.0）——词元含
      归一规格词，规格一致直接抬 base；
    - 候选声明了规格时按规格命中率加权（offer 缺声明 → ×0.65 保守压分）；
    - **同类型规格矛盾一票否决**：双方都声明且无交集（316 vs 304 / 500ml vs
      1500ml）→ 封顶 ``SPEC_CONTRADICTION_CAP``(0.30)——base 被整段重叠抬高时
      乘法惩罚压不住（fix round 1 实证 0.7071 过阈），矛盾必须有否决力。
    """
    offer = str(offer_title or "").strip()
    if not offer or not str(candidate_title_zh or "").strip():
        return 0.0
    a_tokens, a_specs = _tokens_and_specs(candidate_title_zh)
    b_tokens, b_specs = _tokens_and_specs(offer)
    a_all = a_tokens | a_specs
    b_all = b_tokens | b_specs
    if not a_all or not b_all:
        return 0.0
    base = min(1.0, len(a_all & b_all) / max(1, min(len(a_all), len(b_all))))
    if a_specs:
        spec_ratio = len(a_specs & b_specs) / len(a_specs)
        base *= _SPEC_FACTOR_BASE + _SPEC_FACTOR_SPAN * spec_ratio
    if _has_same_type_contradiction(a_specs, b_specs):
        base = min(base, SPEC_CONTRADICTION_CAP)
    return round(max(0.0, min(1.0, base)), 4)


# ────────────────────────── pick_best_source ──────────────────────────


@dataclass(frozen=True)
class PlatformBest:
    """单平台最优合格报价（批3 快照 ``source_comparison`` 的原料）。"""

    platform: str
    offer: SourceOffer
    confirm_score: float
    landed_cost: float        # price + freight（未知运费按 0，见 freight_unknown）
    freight_unknown: bool     # 运费缺失标记（比较按 0，但快照必须如实透出）
    sold: int | None


@dataclass(frozen=True)
class Decision:
    """选优决策（批3 消费：winner 换槽位写 match_1688_*，快照带全量证据）。"""

    winner: SourceOffer | None            # None = 保持 1688（安全底线）
    switched: bool
    reason: str                           # 人话中文（采集箱可见）
    baseline_1688_cost: float
    same_min: float                       # 本次实际生效阈值（env 覆盖后）
    switch_ratio: float
    platform_bests: dict[str, PlatformBest] = field(default_factory=dict)
    confirm_scores: dict[str, float] = field(default_factory=dict)  # 按 offer.url


def _fmt(v: float) -> str:
    """价格展示去尾零（9.90→9.9、16.65→16.65）。"""
    return f"{round(v, 2):g}"


def _sort_key(offer: SourceOffer, confirm: float, landed: float) -> tuple:
    """选优排序键：到手价 asc → 销量 desc（None 最低）→ 确认分 desc（确定性）。"""
    sold = offer.sold if offer.sold is not None else -1
    return (landed, -sold, -confirm)


def pick_best_source(offers_by_platform: dict[str, list[SourceOffer]],
                     baseline_1688_cost: float,
                     candidate_title: str = "") -> Decision:
    """跨平台货源选优：同款确认过闸 → 到手价显著更低才换源（平手保 1688）。

    Args:
        offers_by_platform: 批1 搜索产出（platform → ``list[SourceOffer]``）。
        baseline_1688_cost: 1688 命中的到手基线（批3 传 candidate.match_1688_price，
            含运费口径由调用方统一）。
        candidate_title: 1688 命中标题（同款确认的参照真值；空 = 无从确认 →
            恒保 1688）。

    纪律：
    - 到手价 = price + freight；freight 为 None 按 0 参与比较并标
      ``freight_unknown``（未知 ≠ 0，快照如实透出）；price 为 None 失去资格；
    - 只有 ``confirm_same_product ≥ same_min`` 的报价有换源资格；未过闸报价的
      确认分仍记入 ``confirm_scores``（「为什么不换」要有证据）；
    - 换源门槛：胜者到手价**严格** ``< baseline × switch_ratio``（平手保 1688）；
    - ``baseline ≤ 0``：基线无效无法安全算比值 → 恒保 1688（确认分/平台最优
      照常产出，快照不缺证据）；
    - 平台间平价 tie-break 取销量高者（None 最低），再取确认分高者（确定性）。
    """
    same_min = _env_positive_float(ENV_SAME_MIN, SAME_PRODUCT_MIN)
    ratio = _env_positive_float(ENV_SWITCH_RATIO, SWITCH_MIN_RATIO)

    confirm_scores: dict[str, float] = {}
    platform_bests: dict[str, PlatformBest] = {}
    for platform in sorted(offers_by_platform or {}):
        best: PlatformBest | None = None
        for offer in (offers_by_platform[platform] or []):
            score = confirm_same_product(candidate_title, offer.title)
            confirm_scores[offer.url] = score
            if offer.price is None or score < same_min:
                continue
            landed = float(offer.price) + (float(offer.freight)
                                           if offer.freight is not None else 0.0)
            cand = PlatformBest(
                platform=platform, offer=offer, confirm_score=score,
                landed_cost=landed, freight_unknown=offer.freight is None,
                sold=offer.sold)
            if best is None or _sort_key(offer, score, landed) < \
                    _sort_key(best.offer, best.confirm_score, best.landed_cost):
                best = cand
        if best is not None:
            platform_bests[platform] = best

    def _keep(reason: str) -> Decision:
        return Decision(winner=None, switched=False, reason=reason,
                        baseline_1688_cost=baseline_1688_cost, same_min=same_min,
                        switch_ratio=ratio, platform_bests=platform_bests,
                        confirm_scores=confirm_scores)

    if baseline_1688_cost <= 0:
        return _keep(f"1688 保持：基线成本无效（{_fmt(baseline_1688_cost)}），"
                     "不执行换源比较")
    if not platform_bests:
        if confirm_scores:
            if not str(candidate_title or "").strip():
                # Fix Round 1（Minor #1）：与「没过同款确认」区分——批4 gate 调试
                # 要分得清是没参照还是没过闸。
                return _keep("1688 保持：无参照标题（candidate_title 为空），恒保 1688")
            return _keep(f"1688 保持：无候选过同款确认（阈值 {same_min:.2f}）")
        return _keep("1688 保持：无跨平台候选")

    winner_pb = min(platform_bests.values(),
                    key=lambda pb: _sort_key(pb.offer, pb.confirm_score,
                                             pb.landed_cost))
    threshold = baseline_1688_cost * ratio
    # FP 容差：18.5×0.90=16.650000000000002 这类尾差不得把「恰好等价」翻成换源
    # （安全底线：平手保 1688——宁少换款，不错换款）。
    if winner_pb.landed_cost >= threshold - 1e-9:
        return _keep(f"1688 保持：最低到手价 {_fmt(winner_pb.landed_cost)} "
                     f"未达换源线 {_fmt(threshold)}"
                     f"（1688 {_fmt(baseline_1688_cost)}×{ratio:.2f}）")
    return Decision(
        winner=winner_pb.offer, switched=True,
        reason=(f"{winner_pb.platform} 换源：到手价 {_fmt(winner_pb.landed_cost)} < "
                f"1688 {_fmt(baseline_1688_cost)}×{ratio:.2f}="
                f"{_fmt(threshold)}（同款确认 {winner_pb.confirm_score:.2f}"
                + ("，运费未知按 0 计）" if winner_pb.freight_unknown else "）")),
        baseline_1688_cost=baseline_1688_cost, same_min=same_min,
        switch_ratio=ratio, platform_bests=platform_bests,
        confirm_scores=confirm_scores)


# ────────────────────────── extract_search_keyword ──────────────────────────

# ⚠️ worker/src/utils/ozon_category_query.py `_MODIFIER_WORDS`（v0.65.1）先例的
# 平台无关瘦身版——勿改原表（那边是 Ozon 类目搜索语境）；这里只服务淘宝/pdd
# 搜索框：受众/性别/季节/营销词一律剥掉（剥法同 worker `_strip_modifier_substrings`：
# CJK 修饰词剥子串，拉丁词仅整词匹配防误伤英文品牌词）。
_MODIFIER_WORDS = frozenset({
    # 受众/性别
    "成人", "儿童", "宝宝", "婴幼儿", "婴儿", "男款", "女款", "男式", "女式",
    "女士", "男士", "男性", "女性", "学生", "情侣", "亲子", "大人", "小孩",
    # 季节/厚度/风格/新旧
    "加厚", "保暖", "冬季", "夏季", "春季", "秋季", "春秋", "新款", "韩版",
    "网红", "北欧", "复古", "创意", "简约",
    # 营销/场景泛词（1688 标题高频但无跨平台检索价值）
    "热门", "热卖", "促销", "爆款", "跨境", "家用", "日用", "通用", "定制",
    "便携", "小巧", "大容量",
    # 拉丁修饰词（仅整词匹配）
    "ins", "hot", "new",
})

_KEYWORD_MAX_PIECES = 2    # 搜索框取前 2 个核心词——再多引入平台噪音
_KEYWORD_MAX_CHARS = 12    # 拼接总长上限（淘宝/pdd 搜索框友好长度）
# 回退路径截断上限（Gate Fix，批4 实机 gate 揪出）：1688 标题常态是**无空格
# CJK 连写**（如「卡通可爱双饮保温杯高颜值萌趣儿童水杯吸管杯316不锈钢便携」），
# 主路径单 token 超 12 字上限 → 空手 → 曾返回 None → 快照 {"skipped":"无参照
# 标题"}、跨源比价整段跳过（3 个 profitable 候选全中）。回退=清洗后的标题前缀
# 截断——淘宝/pdd 对长自然中文 query 召回良好，**精度由 confirm_same_product
# 把关（分层职责，不在关键词层追求精准）**。
_KEYWORD_FALLBACK_MAX_CHARS = 16


def _strip_modifier_words(s: str) -> str:
    """剥修饰词（两遍序，Gate Fix 顺带修）：先 CJK 修饰词按子串剥
    （**长词优先**——「婴幼儿」必须先于「婴儿」，否则剥出「幼儿」垃圾词），
    再拉丁修饰词整词等价（放最后——「新款ins网红爆款」剥完 CJK 后剩
    「ins」，此时整词等价才命中；两遍序前会漏）。"""
    core = str(s or "")
    for w in sorted((w for w in _MODIFIER_WORDS if not w.isascii()),
                    key=len, reverse=True):
        if w in core:
            core = core.replace(w, "")
    core = core.strip()  # 回退路径剥完 CJK 可能剩「 ins 」——整词判定前先去边
    for w in _MODIFIER_WORDS:
        if w.isascii() and core == w:
            return ""
    return core


def _fallback_keyword(text: str) -> str | None:
    """主路径剥不出 ≤12 字核心块时的回退：清洗标题前缀截断（Gate Fix）。

    清洗 = 剥修饰词（同主路径两遍序）+ 剥纯规格 token（牌号/容量/型号开头
    召回差，品类词前置）+ 空白折叠。剥完全空 → None（全修饰词/纯规格标题
    不回退出垃圾——「有标题」≠「可搜」）。
    """
    cleaned = _strip_modifier_words(str(text or "").lower())
    cleaned = _SPEC_GRADE_RE.sub(" ", cleaned)
    cleaned = _SPEC_VOLUME_RE.sub(" ", cleaned)
    cleaned = _SPEC_MODEL_RE.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    if len(cleaned) < 2:
        return None
    return cleaned[:_KEYWORD_FALLBACK_MAX_CHARS]


def extract_search_keyword(match_1688_title: str | None) -> str | None:
    """1688 命中标题 → 跨平台搜索关键词（None = 标题空/清洗后无可搜内容）。

    口径：标点/空白切块 → 逐块剥修饰词 → 剩 ≥2 字符的核心块保序去重 →
    取前 ``_KEYWORD_MAX_PIECES`` 块、总长 ≤``_KEYWORD_MAX_CHARS``。
    规格词（500ml/316）保留——跨平台搜「保温杯500ml」比光「保温杯」准得多。

    Gate Fix（批4 实机 gate）：1688 标题常态是**无空格 CJK 连写**，主路径单
    token 超 12 字上限时曾直接返回 None（3 个 profitable 候选跨源比价整段
    跳过的根因）——现回退 ``_fallback_keyword``（清洗标题前缀截断 ≤16 字，
    精度由 confirm_same_product 把关）。
    """
    text = str(match_1688_title or "").strip()
    if not text:
        return None
    pieces: list[str] = []
    for raw in _SPLIT_RE.split(text.lower()):
        core = _strip_modifier_words(raw)
        if len(core) >= 2 and core not in pieces:
            pieces.append(core)
    keyword = ""
    for piece in pieces[:_KEYWORD_MAX_PIECES]:
        candidate = f"{keyword} {piece}".strip() if keyword else piece
        if len(candidate) > _KEYWORD_MAX_CHARS:
            break
        keyword = candidate
    return keyword or _fallback_keyword(text)
