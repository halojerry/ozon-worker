"""属性填充辅助工具（v0.16）

- 海关编码（ТН ВЭД）属性识别：Ozon 部分类目 schema 会返回海关编码属性（如 22604），
  由平台/税费系统自动关联，手动乱填会被拒或误导审核 → 一律不填（可空）。
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


def has_chinese(text: str | None) -> bool:
    """是否含中文字符"""
    return bool(text) and bool(_CJK_RE.search(str(text)))


def has_cyrillic(text: str | None) -> bool:
    """是否含西里尔字母"""
    return bool(text) and bool(_CYRILLIC_RE.search(str(text)))
