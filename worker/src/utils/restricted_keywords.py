"""v0.69 Wave4 T2.4: 受限/需资质品类关键词闸（纯函数 + 热加载配置）。

背景（店铺健康扫描）：BR_hazard_class1（易燃）5 例——汽柴油容器类商品在自动
匹配里打转 8 分钟才被 Ozon 拒。本闸在 assemble 类目匹配开始前/类目定稿后做
本地双命中预检：商品标题/货源 与 定稿类目 full_path **都**命中词表才拦
（单侧命中放行防误伤），拦截走 T0.3 入箱机制转人工确认。

职责边界（改前必读）：
- 与 R1（utils/ozon_category_query 的敏感子树闸）完全独立：R1 是成人内容防护线，
  本闸是危险品/需资质品类闸。本闸不触碰 _r1_veto/_SENSITIVE_SOURCE_SIGNALS，
  R1 语义零改动；两闸判定互不依赖（词表也不共享）。
- 词表运营可维护（worker/config/restricted_keywords.json），只拦「标题和类目
  双命中」——单侧命中绝不拦。
- 命中逻辑纯函数化（match_restricted_keywords / is_restricted_double_hit 可单测）：
  大小写不敏感，俄文按小写子串、中文按包含。

热加载语义对齐 utils/image_prompts.py：每次调用现读磁盘（无进程内缓存），
改 config/restricted_keywords.json 下一次调用生效；文件缺失/损坏/格式错 →
回退内置默认词表（本模块 _DEFAULT_*，与 config 逐字一致），绝不抛异常阻断主链路。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

# 内置默认词表（与 config/restricted_keywords.json 默认内容逐字一致，缺失/损坏时兜底）
_DEFAULT_KEYWORDS = [
    "бензин", "бензиновый", "канистра", "зажигалка", "спирт", "спиртовой",
    "аэрозоль", "газовый баллон", "моторное масло",
    "锂电", "汽油", "打火机", "酒精", "煤油", "打火机燃料",
]
_DEFAULT_NOTICE = "该品类可能需要资质/危险品认证，已转入人工确认"


def load_restricted_config() -> dict:
    """现读 config/restricted_keywords.json（每次调用读盘 → 热加载）。

    失败返回空 dict（由 restricted_keywords/restricted_notice 回退内置默认）。
    """
    workspace = os.getenv("APP_WORKSPACE_PATH") or os.getcwd()
    cfg_path = os.path.join(workspace, "config", "restricted_keywords.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as fd:
            data = json.load(fd)
        if isinstance(data, dict):
            return data
        logger.warning("受限品类词表配置格式错误(%s): 期望 dict，实际 %s",
                       cfg_path, type(data).__name__)
    except FileNotFoundError:
        logger.info("受限品类词表配置不存在(%s)，使用内置默认词表", cfg_path)
    except Exception as e:
        logger.warning("受限品类词表加载失败(%s): %s，使用内置默认词表", cfg_path, e)
    return {}


def restricted_keywords() -> list:
    """生效词表（小写规范化；配置为空/非法时回退内置默认，保证闸始终有效）。"""
    kws = load_restricted_config().get("keywords")
    if isinstance(kws, list):
        out = []
        for k in kws:
            if isinstance(k, str) and k.strip():
                low = k.strip().lower()
                if low not in out:
                    out.append(low)
        if out:
            return out
    return list(_DEFAULT_KEYWORDS)


def restricted_notice() -> str:
    """人读提示文案（入箱 notice / 错误信息拼用）。"""
    notice = load_restricted_config().get("notice")
    if isinstance(notice, str) and notice.strip():
        return notice.strip()
    return _DEFAULT_NOTICE


def match_restricted_keywords(text) -> list:
    """词表命中（纯函数，可单测）：大小写不敏感，俄文按小写子串、中文按包含。

    Returns:
        命中的词表词（小写、去重、按词表顺序）；未命中/空文本 → []。
    """
    low = str(text or "").lower()
    if not low:
        return []
    hits = []
    for kw in restricted_keywords():
        if kw in low and kw not in hits:
            hits.append(kw)
    return hits


def is_restricted_double_hit(source_text, category_text) -> bool:
    """双命中判定（纯函数，可单测）——受限闸唯一拦截条件。

    货源侧（商品标题+source_category）与类目侧（定稿 full_path，ZH+RU）都命中
    才 True；任一侧未命中一律 False（宁放勿拦：卖汽油桶配件的标题未必命中，
    普通水桶类目名带 канистра 也不误伤）。
    """
    return bool(match_restricted_keywords(source_text)) and \
        bool(match_restricted_keywords(category_text))
