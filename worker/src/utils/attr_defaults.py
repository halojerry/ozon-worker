"""必填字典属性默认值解析（v0.24 F1b，参考 1688-product-to-ozon 字典值方法论）。

Ozon 字典属性绝不能手写 dictionary_value_id，必须来自类目字典值缓存
（dictionary_value_cache）或 /values/search。本模块只做「值文本 → id」的
语义解析与安全默认选择；查不到安全默认返回 None，由调用方走既有 LLM 兜底
或暴露可行动错误（F2）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from utils.size_mapper import map_size_to_russian

BRAND_NAME_KEYWORDS = ("бренд", "品牌", "brand")
GENDER_NAME_KEYWORDS = ("пол", "性别", "gender")
SIZE_NAME_KEYWORDS = ("размер", "尺码", "尺寸", "size")
TYPE_NAME_KEYWORDS = ("тип", "类型", "type")
MERGE_CARD_ATTR_IDS = (8292,)
MODEL_ATTR_IDS = (22390,)
NO_MERGE_KEYWORDS = ("не объедин", "не обьедин", "нет", "不合并", "否")

# 1688 关键词 → 俄语性别值（再查 dictionary_value_id）
GENDER_MAP = (
    ("男女通用", "Унисекс"), ("男女", "Унисекс"), ("中性", "Унисекс"),
    ("通用", "Унисекс"), ("男款", "Мужской"), ("男", "Мужской"),
    ("女款", "Женский"), ("女", "Женский"),
    # ✅ v0.25: follow 标题是俄语（如 "Носки женские"），必须能识别
    ("женск", "Женский"), ("дамск", "Женский"), ("для женщин", "Женский"),
    ("мужск", "Мужской"), ("для мужчин", "Мужской"),
    ("унисекс", "Унисекс"), ("универсальн", "Унисекс"),
    # ✅ v0.25: 女性专属品类词（裤袜/丝袜/裙子/连衣裙/女式上衣）
    ("колготк", "Женский"), ("чулк", "Женский"), ("юбк", "Женский"),
    ("плать", "Женский"), ("блузк", "Женский"), ("бюстгальтер", "Женский"),
)

# 俄语标题颜色词 → 字典值（无 1688 颜色时从竞品标题推断）
TITLE_COLOR_MAP = (
    ("черн", "черный"), ("бел", "белый"), ("сер", "серый"),
    ("син", "синий"), ("красн", "красный"), ("зелен", "зеленый"),
    ("розов", "розовый"), ("беж", "бежевый"), ("фиолет", "фиолетовый"),
    ("коричн", "коричневый"), ("золот", "золотой"), ("серебр", "серебристый"),
    ("прозрачн", "прозрачный"), ("желт", "желтый"),
)


def infer_color_ru(title_text: str) -> Optional[str]:
    """从标题（1688 中文或 Ozon 俄语）推断颜色词。"""
    t = str(title_text or "").lower()
    for kw, ru in TITLE_COLOR_MAP:
        if kw in t:
            return ru
    return None


def _as_list(dict_vals: Any) -> list:
    if isinstance(dict_vals, list):
        return dict_vals
    if isinstance(dict_vals, dict):
        return dict_vals.get("result") or []
    return []


def find_dict_value_id(dict_vals: Any, value_text: str) -> Optional[tuple[int, str]]:
    """字典值列表里按文本精确匹配（忽略大小写/空白），返回 (id, value)；无则 None。"""
    target = re.sub(r"\s+", "", str(value_text or "")).lower()
    if not target:
        return None
    for v in _as_list(dict_vals):
        if not isinstance(v, dict):
            continue
        if re.sub(r"\s+", "", str(v.get("value") or "")).lower() == target:
            vid = v.get("id") or v.get("dictionary_value_id") or 0
            if vid:
                return int(vid), str(v.get("value") or "")
    return None


def resolve_brand_default(dict_vals: Any) -> Optional[tuple[int, str]]:
    """品牌默认：Нет бренда（id=126745801）。字典值里按 id 或文本命中。"""
    for v in _as_list(dict_vals):
        if not isinstance(v, dict):
            continue
        vid = v.get("id") or v.get("dictionary_value_id") or 0
        val = str(v.get("value") or "")
        if int(vid or 0) == 126745801 or "нет бренда" in val.lower():
            return 126745801, "Нет бренда"
    return None


def resolve_gender_default(title_text: str, dict_vals: Any) -> Optional[tuple[int, str]]:
    """按标题/属性推断性别（女→Женский…），再查字典值 id。"""
    ru = infer_gender_ru(title_text)
    if ru:
        return find_dict_value_id(dict_vals, ru)
    return None


def infer_gender_ru(title_text: str) -> Optional[str]:
    """按 1688 标题推断性别俄语值（Женский/Мужской/Унисекс）；无则 None。"""
    t = str(title_text or "").lower()
    for zh, ru in GENDER_MAP:
        if zh in t:
            return ru
    return None


def resolve_ozon_attr_value(attr_id: int, attr_name: str, ozon_attrs: Any) -> Optional[str]:
    """从竞品 Ozon 属性表（俄语键值）取对应属性的值，按属性语义关键词匹配。"""
    if not isinstance(ozon_attrs, dict) or not ozon_attrs:
        return None
    attr_id = int(attr_id or 0)
    name = str(attr_name or "").lower()
    if attr_id in (85, 31, 5076) or any(k in name for k in BRAND_NAME_KEYWORDS):
        keys = ("бренд", "品牌", "brand")
    elif attr_id == 9163 or any(k in name for k in GENDER_NAME_KEYWORDS):
        keys = ("пол", "性别", "gender")
    elif attr_id in (4295, 4411) or any(k in name for k in SIZE_NAME_KEYWORDS):
        keys = ("размер", "尺码", "size")
    elif attr_id == 8229 or any(k in name for k in TYPE_NAME_KEYWORDS):
        keys = ("тип", "类型", "вид")
    elif attr_id in (10096, 10097) or "цвет" in name or "颜色" in name:
        keys = ("цвет", "颜色", "color")
    elif any(k in name for k in ("материал", "材质", "material")):
        keys = ("материал", "材质", "material")
    else:
        return None
    for k, v in ozon_attrs.items():
        kl = str(k or "").lower()
        if any(kw in kl for kw in keys):
            val = str(v or "").strip()
            return val if val else None
    return None


def dict_search_terms(
    attr_id: int,
    attr_name: str,
    *,
    title_cn: str = "",
    product_name_ru: str = "",
    size_cn: str = "",
) -> list[str]:
    """按属性语义返回 live search 关键词（RU 优先）：
    品牌→Нет бренда；性别→俄语性别；尺码→俄罗斯尺码；8292→不合并；类型→产品名/标题。"""
    attr_id = int(attr_id or 0)
    name = str(attr_name or "").lower()
    if attr_id in (85, 31, 5076) or any(k in name for k in BRAND_NAME_KEYWORDS):
        return ["Нет бренда"]
    if attr_id == 9163 or any(k in name for k in GENDER_NAME_KEYWORDS):
        ru = infer_gender_ru(title_cn)
        return [ru] if ru else []
    if attr_id in (4295, 4411) or any(k in name for k in SIZE_NAME_KEYWORDS):
        if not size_cn:
            return []
        mapped = map_size_to_russian(size_cn, product_name_ru or "")
        return [mapped[0]] if mapped else []
    if attr_id in MERGE_CARD_ATTR_IDS:
        return ["Нет", "не объединять"]
    if attr_id == 8229 or any(k in name for k in TYPE_NAME_KEYWORDS):
        terms = [product_name_ru, title_cn]
        # 短词优先：产品名首词/前两词（如 "Носки" 可搜到，全名搜不到）
        if product_name_ru:
            words = str(product_name_ru).replace(",", " ").split()
            if words:
                terms.insert(0, words[0])
            if len(words) >= 2:
                terms.insert(1, " ".join(words[:2]))
        return [t for t in terms if t]
    return []


def resolve_size_default(size_cn: str, product_name_ru: str, dict_vals: Any) -> Optional[tuple[int, str]]:
    """1688 尺码 → 俄罗斯尺码（size_mapper + 尺码表）→ 查字典值 id。"""
    if not size_cn:
        return None
    mapped = map_size_to_russian(size_cn, product_name_ru or "")
    if not mapped:
        return None
    ru_size, _ = mapped
    return find_dict_value_id(dict_vals, ru_size)


def resolve_merge_card_default(dict_vals: Any) -> Optional[tuple[int, str]]:
    """8292 合并至一张卡片：取「不合并」值（Нет / не объединять…），无则 None。"""
    for v in _as_list(dict_vals):
        if not isinstance(v, dict):
            continue
        val = str(v.get("value") or "")
        if any(kw in val.lower() for kw in NO_MERGE_KEYWORDS):
            vid = v.get("id") or v.get("dictionary_value_id") or 0
            if vid:
                return int(vid), val
    return None


def resolve_missing_mandatory_dict_attr(
    attr_id: int,
    attr_name: str,
    *,
    title_cn: str = "",
    product_name_ru: str = "",
    size_cn: str = "",
    dict_vals: Any = None,
) -> Optional[tuple[int, str]]:
    """按属性语义解析必填字典属性的安全默认值；查不到返回 None。"""
    attr_id = int(attr_id or 0)
    name = str(attr_name or "").lower()
    if attr_id in MERGE_CARD_ATTR_IDS:
        return resolve_merge_card_default(dict_vals)
    if attr_id in MODEL_ATTR_IDS:
        return None  # 22390 型号是自由文本（= itemId），不走字典
    if attr_id in (85, 31, 5076) or any(k in name for k in BRAND_NAME_KEYWORDS):
        return resolve_brand_default(dict_vals)
    if attr_id == 9163 or any(k in name for k in GENDER_NAME_KEYWORDS):
        return resolve_gender_default(title_cn, dict_vals)
    if attr_id in (4295, 4411) or any(k in name for k in SIZE_NAME_KEYWORDS):
        return resolve_size_default(size_cn, product_name_ru, dict_vals)
    if attr_id == 8229 or any(k in name for k in TYPE_NAME_KEYWORDS):
        values = _as_list(dict_vals)
        if values and isinstance(values[0], dict):
            vid = values[0].get("id") or values[0].get("dictionary_value_id") or 0
            if vid:
                return int(vid), str(values[0].get("value") or "")
        return None
    return None
