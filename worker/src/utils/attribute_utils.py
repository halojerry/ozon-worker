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

# 「非危险」字典值关键词（RU，属性名/值来自 Ozon API）
HAZARD_SAFE_VALUE_KEYWORDS = (
    "не опас", "неопас", "без класса", "нет класса",
    "не классифицир", "не относится к опас",
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
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


def has_chinese(text: str | None) -> bool:
    """是否含中文字符"""
    return bool(text) and bool(_CJK_RE.search(str(text)))


def has_cyrillic(text: str | None) -> bool:
    """是否含西里尔字母"""
    return bool(text) and bool(_CYRILLIC_RE.search(str(text)))
