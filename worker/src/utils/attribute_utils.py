"""属性填充辅助工具（v0.16 / v0.21）

- 海关编码（ТН ВЭД）属性识别：Ozon 部分类目 schema 会返回海关编码属性（如 22604），
  由平台/税费系统自动关联，手动乱填会被拒或误导审核 → 一律不填（可空）。
- 危险品等级（Класс опасности товара，9782）识别：乱填（尤其"爆炸物 Category 1"）
  会被 Ozon 判 BR_hazard_class1 → 只允许填「非危险」安全默认，否则跳过。
- 必填字典属性兜底规则（v0.21）：危险属性只挑安全默认；其他属性仅当字典值唯一才填。
- 中文/俄语判定复用。
"""
from __future__ import annotations

import re

# 已知海关编码属性 ID（Ozon: ТН ВЭД / таможенный код）
CUSTOMS_ATTR_IDS = (22604,)

# 海关属性名关键词（属性名来自 Ozon API，可能是 RU / ZH_HANS / EN）
CUSTOMS_ATTR_NAME_KEYWORDS = (
    "тн вэд", "тнвэд", "таможен", "таможенный код",
    "海关", "海关编码", "hs code", "hs-code", "hscode",
)

# 危险品等级属性（Класс опасности товара）— 填"爆炸物 Category 1"会被 Ozon 整包拒绝
HAZARD_DICT_ATTR_IDS = (9782,)

# 方面属性（is_aspect=true）：用于区分同类商品不同特征的属性，部分在创建/出仓后不可改。
# retry 阶段对已上架商品修改 aspect 属性会被 Ozon 拒绝 → revalidate 应跳过（与 hazard 同理）。
# ⚠️ v0.75 收窄（审计 A4 F-P1-2）：名称关键词只是兜底信号且形态受限——
# schema 行显式 is_aspect=False 时绝不落关键词翻案；schema 缺键行仅「字典形态
# （dictionary_id>0）+ 关键词」判 aspect（自由文本如 «вид деятельности» 不误伤）。
# 分支序详见 is_aspect_attr docstring。
ASPECT_ATTR_NAME_KEYWORDS = (
    "тип", "типа", "вид", "модель", "модели", "размер", "размера", "цвет", "цвета",
    "类型", "型号", "尺寸", "颜色",
)

# 已知方面属性 ID 硬编码（override 优先于一切，含 schema 显式 False；实测确认后逐步补充）
ASPECT_ATTR_ID_OVERRIDES: tuple = ()

# 「非危险」字典值关键词（RU + ZH_HANS，属性名/值可能来自两种语言）
# ⚠️ v0.29.x: 字典值缓存同时存 RU/ZH_HANS, 旧代码只有 RU 关键词 →
# 中文值(如"非危险货物")匹配不到 → 9782 必填缺失(58 次错误根因之一)。
HAZARD_SAFE_VALUE_KEYWORDS = (
    "не опас", "неопас", "без класса", "нет класса",
    "не классифицир", "не относится к опас",
    "非危险", "不危险", "无危险", "非易燃", "普通货物", "一般货物", "普通商品",
)

# ✅ v0.73: CJK 判定扩展——假名 U+3040-30FF（Ozon 拒单语义是「中文/日文字符」，
# 纯假名值此前漏检）+ CJK 扩展 A U+3400-4DBF（罕见汉字）。改中文检测前必读：
# 本模块 has_chinese 是 retry/prepare 链唯一事实源（attr_value_matcher.py:67 的
# 同名函数尚未统一，勿在其处复制本正则）。
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf]")
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")


def is_customs_attr(attr_id: int | None, attr_name: str = "") -> bool:
    """判断是否为海关编码相关属性（ID 命中或属性名含关键词）。

    attr_name 匹配时不区分大小写，忽略首尾空白。
    """
    if attr_id is not None and attr_id in CUSTOMS_ATTR_IDS:
        return True
    if attr_name:
        name_lower = str(attr_name).strip().lower()
        if any(kw in name_lower for kw in CUSTOMS_ATTR_NAME_KEYWORDS):
            return True
    return False


def is_hazard_attr(attr_id: int | None, attr_name: str = "") -> bool:
    """判断是否为危险品等级属性（9782，或属性名含危险品等级关键词）。"""
    if attr_id is not None and int(attr_id) in HAZARD_DICT_ATTR_IDS:
        return True
    if attr_name:
        name_lower = str(attr_name).strip().lower()
        if any(kw in name_lower for kw in ("опасности", "класс опас", "危险品等级", "危险等级", "hazard")):
            return True
    return False


def _name_hits_aspect_keyword(attr_name: str) -> bool:
    """属性名命中 ASPECT_ATTR_NAME_KEYWORDS（唯一关键词判定口，勿在他处内联）。"""
    if not attr_name:
        return False
    name_lower = str(attr_name).strip().lower()
    return any(kw in name_lower for kw in ASPECT_ATTR_NAME_KEYWORDS)


def is_aspect_attr(attr_id: int | None, attr_name: str = "", schema_entries: list | None = None) -> bool:
    """判断是否为方面属性（is_aspect=true，部分类目创建后不可改）。

    ⚠️ v0.75 收窄口径（审计 A4 F-P1-2，改分支序前必读）——判定优先级：
      ① ID override（ASPECT_ATTR_ID_OVERRIDES）命中 → True，优先于一切（含 schema 显式 False）；
      ② schema 行命中且显式含 ``is_aspect`` 键 → bool(值)——显式 False 绝对尊重，
        绝不落名称关键词兜底翻案；
      ③ schema 行命中但缺 ``is_aspect`` 键 → 名称关键词兜底 **且** entry.dictionary_id>0——
        自由文本属性（如 «вид деятельности»，dictionary_id=0）不误伤跳过填充；
      ④ 无 schema 行 → 名称关键词兜底（revalidate 安全优先：宁可多跳过不可重传被拒）。
    retry 阶段对已上架商品的 aspect 属性修改会被 Ozon 拒绝 → 调用方应跳过。
    """
    aid: int | None = None
    if attr_id is not None:
        try:
            aid = int(attr_id)
        except (ValueError, TypeError):
            aid = None
    # ① ID override 优先于一切
    if aid is not None and aid in ASPECT_ATTR_ID_OVERRIDES:
        return True
    matched_entry: dict | None = None
    if schema_entries:
        for entry in schema_entries:
            if not isinstance(entry, dict):
                continue
            try:
                entry_id = int(entry.get("id") or 0)
            except (ValueError, TypeError):
                continue
            if aid is not None and entry_id == aid:
                matched_entry = entry
                break
    if matched_entry is not None:
        # ② 显式键 → 直接尊重（False 不再落关键词兜底）
        if "is_aspect" in matched_entry:
            return bool(matched_entry["is_aspect"])
        # ③ 缺键 → 关键词兜底且要求字典形态（自由文本不误伤）
        try:
            dict_id = int(matched_entry.get("dictionary_id") or 0)
        except (ValueError, TypeError):
            dict_id = 0
        return dict_id > 0 and _name_hits_aspect_keyword(attr_name)
    # ④ 无 schema 行 → 关键词兜底（现状保持）
    return _name_hits_aspect_keyword(attr_name)


def get_safe_hazard_default(dict_vals) -> tuple[int, str] | None:
    """在字典值里挑「非危险」值（按关键词）；找不到返回 None。"""
    values = dict_vals if isinstance(dict_vals, list) else (
        dict_vals.get("result") if isinstance(dict_vals, dict) else []
    )
    for v in values or []:
        if not isinstance(v, dict):
            continue
        val = str(v.get("value") or "").strip()
        if any(kw in val.lower() for kw in HAZARD_SAFE_VALUE_KEYWORDS):
            vid = v.get("id") or v.get("dictionary_value_id") or 0
            if vid:
                return (int(vid), val)
    return None


def pick_dict_fallback_value(attr_id: int | None, attr_name: str, dict_vals) -> tuple[int, str] | None:
    """必填字典属性无匹配时的兜底规则（v0.21）。

    - 危险属性（9782）：只挑「非危险」安全默认，取不到返回 None（跳过，不填危险等级）。
    - 其他属性：仅当字典值唯一时才填；多值/空 → None（跳过，留给 Ozon 报可修复错误）。
    返回 (dictionary_value_id, value)；无安全候选返回 None。
    """
    if is_hazard_attr(attr_id, attr_name):
        return get_safe_hazard_default(dict_vals)
    values = dict_vals if isinstance(dict_vals, list) else (
        dict_vals.get("result") if isinstance(dict_vals, dict) else []
    )
    if isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict):
        vid = values[0].get("id") or values[0].get("dictionary_value_id") or 0
        if vid:
            return (int(vid), str(values[0].get("value") or ""))
    return None


def match_attr_name_synonym(ozon_name, product_attr_names, synonyms) -> str | None:
    """同组双向包含匹配（v0.32 词汇分歧修复）。

    规则（严格，防错误值）：Ozon 属性名含某组 `ozon_name_keywords` 中任一
    **AND** 1688 属性名含**同一组** `zh_keywords` 中任一 → 返回该 1688 属性名。
    宽松单侧命中 / 跨组命中 / 无同义词组 → None（绝不引入错误值）。

    例：Ozon「款式」(style 组 ozon 关键词) + 1688「风格」(style 组 zh 关键词) → "风格"。
    """
    if not ozon_name or not product_attr_names or not synonyms:
        return None
    ozon_lower = str(ozon_name).strip().lower()
    for rule in synonyms.values():
        if not isinstance(rule, dict):
            continue
        ozon_kws = [str(k).lower() for k in rule.get("ozon_name_keywords") or []]
        if not any(kw in ozon_lower for kw in ozon_kws):
            continue
        zh_kws = [str(k).lower() for k in rule.get("zh_keywords") or []]
        for pa_name in product_attr_names:
            pa_lower = str(pa_name).strip().lower()
            if any(kw in pa_lower for kw in zh_kws):
                return pa_name
    return None


def has_chinese(text: str | None) -> bool:
    """是否含 CJK 字符（汉字 + 假名 + CJK 扩展 A——Ozon 禁中文/日文字符）"""
    return bool(text) and bool(_CJK_RE.search(str(text)))


def has_cyrillic(text: str | None) -> bool:
    """是否含西里尔字母"""
    return bool(text) and bool(_CYRILLIC_RE.search(str(text)))
