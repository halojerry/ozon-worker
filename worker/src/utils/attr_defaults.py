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

# ⚠️ v0.31 T2: 干扰属性名黑名单 — 名称含「类型/тип」但非 8229 产品类型本身的
# 属性（专利类型/光源类型/开关类型/风扇类型/造型类型…）。它们有各自独立的
# 字典值空间，绝不能套用 8229 的「值 id == 类目 type_id」匹配（否则会把类目
# type 节点的值错填进去，如「专利类型」被填成「手持风扇」）。
# 但【纯「类型」本身绝不拦截】——test_language_routing.py 合法用例
# 「类型: 杀虫剂」→ 8229 必须命中（attr_id==8229 直接豁免）。
INTERFERING_TYPE_ATTR_MARKERS = (
    "专利", "патент",
    "光源", "источник",
    "开关", "выключател",
    "风扇", "вентилятор",
    "造型", "форм",
    "接口", "разъем",
    "传感器", "датчик",
    "电机", "двигател",
    "电池", "аккумулятор",
    "灯泡", "ламп",
)

# 判别词交叉验证表（产品形态/结构判别词，ZH+RU 同组）。
# 仅用「互斥形态词」：桌面/手持/挂脖/落地/夹式/涡轮… 互斥于同品类内。
# 禁用 2-gram 泛词（如「风扇」在每个风扇类型值里都有，用它做交集恒过，
# 验证失去意义）。用于检测「标题明确说 A 形态，但 type_id 匹配值是 B 形态」
# 的高置信错配。
TYPE_DISCRIMINANT_GROUPS = (
    ("桌面", "настольн"),
    ("手持", "ручн"),
    ("挂脖", "шейн"),
    ("落地", "напольн"),
    ("夹式", "прищепк"),
    ("涡轮", "турб"),
    ("无叶", "без лопаст"),
    ("风扇灯", "подсветк"),
    ("折叠", "складн"),
    ("便携", "портативн"),
    ("壁挂", "настенн"),
    ("车载", "автомобильн"),
    ("遥控", "с пульт"),
    ("嵌入式", "встраива"),
    ("升降", "подъемн"),
    ("伸缩", "телескоп"),
)
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


def ozon_attrs_allowed(draft: Any, current_dc: Any) -> bool:
    """竞品属性复用类目一致性校验（v0.29.x）。

    - 无 ozon_attributes → False(不消费)
    - 有 ozon_attributes_category(follow/ozon-ref-url 透传) → 必须与当前类目
      description_category_id 一致才复用；不一致 → False(防跨类目属性错配,
      实测手持风扇 vs 护发素)
    - 无 category 字段(follow 旧路径, 类目天然来自竞品) → True(信任同源)
    """
    if not isinstance(draft, dict) or not draft.get("ozon_attributes"):
        return False
    cat = draft.get("ozon_attributes_category")
    if cat is None:
        return True  # follow 同源(类目即竞品类目)
    try:
        cur = int(current_dc or 0)
        ref = int(cat or 0)
    except (TypeError, ValueError):
        return False
    if cur and ref and cur == ref:
        return True
    if not cur:  # 当前类目未知, 保守不复用
        return False
    return False


def resolve_ozon_attr_value(attr_id: int, attr_name: str, ozon_attrs: Any) -> Optional[str]:
    """从竞品 Ozon 属性表（俄语键值）取对应属性的值，按属性语义关键词匹配。

    v0.31 T1 扩展: 语义关键词未命中时追加「规范化名/2-gram 子串」通用兜底，
    覆盖任意 RU 名（如 Состав/Форма/Сезон），不再局限 6 类语义。
    """
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
        keys = None
    if keys:
        for k, v in ozon_attrs.items():
            kl = str(k or "").lower()
            if any(kw in kl for kw in keys):
                val = str(v or "").strip()
                return val if val else None
    # 通用兜底: 规范化名相等/包含; 长度≥3 防误命中（品牌/性别等敏感属性已由语义分支处理）
    _norm = lambda s: re.sub(r"[\s\-_/()\[\]]+", "", s).lower()
    target = _norm(attr_name)
    if len(target) >= 3:
        for k, v in ozon_attrs.items():
            kn = _norm(str(k or ""))
            if not kn or len(kn) < 3:
                continue
            if kn == target or target in kn or kn in target:
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


def is_interfering_type_attr(attr_id: int, attr_name: str) -> bool:
    """名称含「类型/тип」但非 8229 产品类型本身的干扰属性 → True。

    干扰属性（专利类型/光源类型/开关类型/风扇类型/造型类型…）有自己的字典值
    空间，绝不套用 8229 的 type_id 匹配。纯「类型」本身（attr_id==8229 或
    名称即 类型/Тип）绝不拦截。
    """
    aid = int(attr_id or 0)
    if aid == 8229:
        return False
    name = str(attr_name or "").lower()
    if not any(k in name for k in TYPE_NAME_KEYWORDS):
        return False
    return any(m in name for m in INTERFERING_TYPE_ATTR_MARKERS)


def _discriminant_conflict(source_text: str, matched_value: str) -> bool:
    """判别词交叉验证：源文本（标题/产品名）出现某形态判别词，但匹配的字典值
    不含该形态（任语言）→ 高置信错配 → True（应跳过）。

    仅当匹配值文本非空（dict_id 权威的置空场景不误伤）。判别词表只含互斥
    形态词（桌面/手持/挂脖/落地…），2-gram 泛词（如「风扇」）禁用。
    """
    src = str(source_text or "").lower()
    val = str(matched_value or "").lower()
    if not src or not val:
        return False
    for zh, ru in TYPE_DISCRIMINANT_GROUPS:
        if zh in src or ru in src:
            if not (zh in val or ru in val):
                return True
    return False


def _match_dict_by_source_keywords(
    title_cn: str, product_name_ru: str, values: list,
) -> Optional[tuple[int, str]]:
    """标题/产品名关键词 → 字典值匹配（精确/包含/2-gram 子串）。"""
    _kw = ""
    for _src in (title_cn, product_name_ru):
        if _src and len(str(_src).strip()) >= 2:
            _kw = str(_src).strip()
            break
    if not _kw:
        return None
    for _v in values:
        if not isinstance(_v, dict):
            continue
        _vt = str(_v.get("value") or "")
        if not _vt:
            continue
        _match = _vt in _kw or _kw in _vt
        if not _match and len(_kw) >= 2:
            for _gi in range(len(_kw) - 1):
                _gram = _kw[_gi:_gi + 2]
                if _gram and _gram in _vt:
                    _match = True
                    break
        if _match:
            vid2 = _v.get("id") or _v.get("dictionary_value_id") or 0
            if vid2:
                return int(vid2), _vt
    return None


def resolve_missing_mandatory_dict_attr(
    attr_id: int,
    attr_name: str,
    *,
    title_cn: str = "",
    product_name_ru: str = "",
    size_cn: str = "",
    dict_vals: Any = None,
    type_id: int = 0,
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
        if not values:
            return None
        # ⚠️ v0.31 T2: 干扰属性名(专利类型/光源类型/开关类型/风扇类型/造型类型…)
        # 名称含「类型」但非 8229 产品类型本身，有独立字典值空间，绝不套用
        # 8229 的 type_id 匹配（值 id==类目 type_id 是 8229 专属行为）。
        # 走通用关键词解析 + 单值兜底（与 8229 纪律一致：不盲补首值）。
        if is_interfering_type_attr(attr_id, name):
            _kw_res = _match_dict_by_source_keywords(title_cn, product_name_ru, values)
            if _kw_res:
                return _kw_res
            if len(values) == 1 and isinstance(values[0], dict):
                _vid1 = values[0].get("id") or values[0].get("dictionary_value_id") or 0
                if _vid1:
                    return int(_vid1), str(values[0].get("value") or "")
            return None
        # ⚠️ v0.29.x 修复: 8229(类型)的字典值 id == type_id(类目 type 节点本身,
        # 实测手持风扇 148495146 / 杀虫剂 99385)。优先按 type_id 匹配, 绝不取
        # 第一个字典值(同大类下其他小类, 如「套娃」→ 错配被 Ozon 拒)。
        if type_id and int(type_id) > 0:
            for _v in values:
                if isinstance(_v, dict) and int(_v.get("id") or _v.get("dictionary_value_id") or 0) == int(type_id):
                    _tv = str(_v.get("value") or "")
                    # ⚠️ v0.31 T2 判别词交叉验证: 标题明确说「桌面」/「手持」等
                    # 形态, 但 type_id 匹配值是另一种形态 → 高置信错配 → 跳过。
                    if _discriminant_conflict(f"{title_cn} {product_name_ru}", _tv):
                        return None
                    return int(type_id), _tv
        # 次选: 标题/产品名关键词匹配(避免取到无关首值)；同样过判别词验证。
        _kw_res = _match_dict_by_source_keywords(title_cn, product_name_ru, values)
        if _kw_res:
            if _discriminant_conflict(f"{title_cn} {product_name_ru}", _kw_res[1]):
                return None
            return _kw_res
        # 唯一值场景 → 直接命中(单值语义确定, 不会错配)
        if len(values) == 1 and isinstance(values[0], dict):
            _vid1 = values[0].get("id") or values[0].get("dictionary_value_id") or 0
            if _vid1:
                return int(_vid1), str(values[0].get("value") or "")
        return None  # 无匹配不盲补(宁缺毋滥, 交 Ozon 报可修复错)
    if attr_id == 9782 or any(k in name for k in ("класс опасности", "класс опас", "опасности товара", "危险品等级", "危险等级", "hazard")):
        # ⚠️ v0.29.x: 9782 必填但只能填"非危险"安全默认(填爆炸物→BR_hazard_class1)。
        # 复用 get_safe_hazard_default(RU+ZH 关键词), 取不到返回 None(跳过, 交 Ozon 报可修复错)。
        from utils.attribute_utils import get_safe_hazard_default
        return get_safe_hazard_default(dict_vals)
    return None


# ── v0.31 T1: 跟卖属性合并链 ────────────────────────────────────────────────
# 合并链: draft.ozon_attributes(RU 名→attr_id→dict_id 字典解析) → draft.attributes(1688 中文)
#        → 硬编码兜底(品牌85/5076 + 产地4389 + 型号9048 + 数量8962)。
# 字典属性解析 dict_id 失败 → 跳过，绝不注入原文（防「属性值不正确」被 Ozon 拒）。
FOLLOW_HARDCODED_ATTR_IDS = (85, 31, 5076, 4389, 9048, 8962)


def build_follow_attr_merge(
    draft: dict,
    schema: list,
    dc_id,
    tp_id,
    client_id: str = "",
    api_key: str = "",
    product_id="",
) -> list[dict]:
    """跟卖属性合并链，返回 Ozon 格式 [{id, values:[{dictionary_value_id, value}]}]。

    ① draft.ozon_attributes（竞品 RU 名→值，同类目校验通过才复用）优先
    ② 无 ozon_attributes → draft.attributes（1688 中文，颜色等语义属性）
    ③ 双无 → 硬编码兜底 5 属性
    字典属性: 竞品值经 /values/search 解析 dictionary_value_id，无命中 → 跳过(不注入原文)。
    """
    from utils.ozon_dict_values import search_dictionary_values

    if not isinstance(draft, dict):
        draft = {}
    base = [
        {"id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
        {"id": 5076, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
        {"id": 4389, "values": [{"dictionary_value_id": 90296, "value": "Китай"}]},
        {"id": 9048, "values": [{"dictionary_value_id": 0, "value": str(product_id)}]},
        {"id": 8962, "values": [{"dictionary_value_id": 0, "value": "1"}]},
    ]
    merged = {int(a["id"]): a for a in base}
    schema = schema if isinstance(schema, list) else []
    dc_i = int(dc_id) if str(dc_id or "").isdigit() else 0
    tp_i = int(tp_id) if str(tp_id or "").isdigit() else 0

    # ① 竞品 ozon_attributes（RU 名）—— 同类目才消费（ozon_attrs_allowed）
    ozon_attrs = draft.get("ozon_attributes")
    if ozon_attrs_allowed(draft, dc_id) and isinstance(ozon_attrs, dict) and ozon_attrs:
        resolved = _resolve_source_attrs(
            ozon_attrs, schema, dc_i, tp_i, client_id, api_key, search_dictionary_values,
        )
        if resolved:
            for a in resolved:
                merged.setdefault(int(a["id"]), a)
            return list(merged.values())
        # ozon_attributes 有值但全部无字典命中 → 落到 ② draft.attributes

    # ② 1688 draft.attributes（中文名，语义匹配 颜色/材质 等）
    draft_attrs = draft.get("attributes")
    if isinstance(draft_attrs, dict) and draft_attrs:
        resolved = _resolve_source_attrs(
            draft_attrs, schema, dc_i, tp_i, client_id, api_key, search_dictionary_values,
        )
        for a in resolved:
            merged.setdefault(int(a["id"]), a)

    # ③ 双无 / 无命中 → 硬编码兜底（merged 已含 base）
    return list(merged.values())


def _resolve_source_attrs(
    source_attrs: dict,
    schema: list,
    dc_i: int,
    tp_i: int,
    client_id: str,
    api_key: str,
    search_dictionary_values,
) -> list[dict]:
    """从源属性表（RU 名竞品表 或 1688 中文表）解析字典属性 → Ozon 格式。

    语义关键词(颜色/材质/尺码/性别/类型/品牌) + 通用 RU 名兜底 resolve_ozon_attr_value;
    字典值 /values/search 精确命中 dict_id, 无命中 → 跳过该属性(绝不注入原文)。
    品牌/产地/型号/数量 等硬编码属性由调用方 base 处理, 这里跳过。
    """
    out: list[dict] = []
    for attr in schema or []:
        if not isinstance(attr, dict):
            continue
        aid = int(attr.get("id") or 0)
        if aid <= 0 or aid in FOLLOW_HARDCODED_ATTR_IDS:
            continue
        if int(attr.get("dictionary_id") or 0) <= 0:
            continue  # 只解析字典属性; 自由文本属性(如型号)由 base/prepare 处理
        aname = str(attr.get("name") or "")
        val = resolve_ozon_attr_value(aid, aname, source_attrs)
        if not val:
            continue
        try:
            hits = search_dictionary_values(client_id, api_key, aid, dc_i, tp_i, val)
        except Exception:
            hits = []
        if not hits:
            continue  # ⚠️ 竞品文本值无字典匹配 → 跳过, 绝不注入原文
        res = find_dict_value_id(hits, val)
        if not res and hits:
            res = (int(hits[0].get("id") or 0), str(hits[0].get("value") or ""))
        if not res or int(res[0]) <= 0:
            continue
        out.append({"id": aid, "values": [
            {"dictionary_value_id": int(res[0]), "value": str(res[1])}
        ]})
    return out
