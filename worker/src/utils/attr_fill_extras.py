"""v0.83 三期「特征属性尽可能填满」——标题证据词 + 类目级保守默认 + 数值派生。

设计口径（docs/PLAN-attr-fill-max-v1.md，2026-09-24 多类目 fill 对账驱动）：
  证据链（信任序）= 本商品证据(1688 attrs) > 标题证据词(本模块, 确定性词表)
                    > vision > 模板继承 > 类目级保守默认(本模块, 白名单)
  全部遵守「字典精确命中才填」的既有安全链；宁缺毋滥不变——
  布尔/个体事实类属性（包括盖子/可洗碗机清洗等）禁入默认表。

三个入口（prepare_ozon_upload_node 接线）：
  ① augment_draft_with_title_evidence(draft)
     在 _fill_optional_dict_attrs 之前调用——把标题/1688 类目路径里的
     材料/颜色/形状/性别证据词合成伪 draft.attributes（不覆盖真实键），
     复用现有同义词链（缓存精确→raw 中文直搜→RU 映射兜底），零新匹配逻辑。
  ② apply_class_defaults_and_numerics(items, schema, draft, state)
     在模板继承之后调用——类目级保守默认（保证/目标受众/性别兜底，
     config/attr_class_defaults.json 白名单 + 字典精确命中才填）
     + 标题数值派生（容量 ml / 每包数量）+ 尺寸串（dims → 尺寸，毫米）。

审计：所有成功填点写 attr_match_log，match_layer ∈
  {title_evidence, class_default, title_numeric, dims_string}。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("worker.attr_fill_extras")

# ---------------------------------------------------------------------------
# ① 标题证据词表（顺序即优先级；命中一个即停）
# ---------------------------------------------------------------------------

# 材料：canonical 值必须是 attr_synonyms.json material 组 value_map 的键
# （或 raw 中文直搜可命中的词）。
_MATERIAL_PATTERNS: list[tuple[str, str]] = [
    (r"不锈钢|304\s*钢|食品级钢", "不锈钢"),
    (r"纳米玻璃", "玻璃"),
    (r"玻璃", "玻璃"),
    (r"塑胶|塑料|PP\s*料|食品级PP", "塑料"),
    (r"硅胶", "硅胶"),
    (r"陶瓷", "陶瓷"),
    (r"亚克力|亚力克", "亚克力"),
    (r"铝合金", "铝合金"),
    (r"实木|木质|木制|木头", "木质"),
    (r"竹纤维|竹制|竹木|竹", "竹"),
    (r"皮革|真皮", "皮革"),
    (r"帆布", "帆布"),
    (r"尼龙", "尼龙"),
    (r"涤纶|聚酯", "涤纶"),
    (r"树脂", "树脂"),
    (r"棉", "棉"),
]

# 颜色：多字词优先，单字词兜底；canonical 值进 color 组 value_map 键。
_COLOR_PATTERNS: list[tuple[str, str]] = [
    (r"乳白|奶白", "白色"),
    (r"白色", "白色"),
    (r"黑色", "黑色"),
    (r"酒红|大红|红色", "红色"),
    (r"天蓝|藏青|蓝色", "蓝色"),
    (r"军绿|墨绿|翠绿|绿色", "绿色"),
    (r"姜黄|明黄|黄色", "黄色"),
    (r"紫色", "紫色"),
    (r"粉红|粉色", "粉色"),
    (r"灰色", "灰色"),
    (r"橙色|橘色", "橙色"),
    (r"咖啡色|棕色|褐色", "棕色"),
    (r"透明", "透明"),
    (r"金色|香槟金", "金色"),
    (r"银色", "银色"),
    (r"米色|米白", "米色"),
    (r"红\b", "红色"),
    (r"黑\b", "黑色"),
    (r"白\b", "白色"),
    (r"蓝\b", "蓝色"),
    (r"绿\b", "绿色"),
    (r"黄\b", "黄色"),
    (r"紫\b", "紫色"),
    (r"灰\b", "灰色"),
    (r"橙\b", "橙色"),
]

# 多色/无法确定单色的信号 → 整体放弃颜色证据
_COLOR_GIVEUP = re.compile(r"双色|两色|三色|多色|彩色|七彩|渐变|混色|印花色|随机色")
# 已知伪命中短语（先剥再扫）
_FALSE_POSITIVE_PHRASES = ["黑科技", "白科技", "黑色星期", "白色噪声"]

_SHAPE_PATTERNS: list[tuple[str, str]] = [
    (r"长方形|长方型|矩形", "长方形"),
    (r"正方形|方形", "方形"),
    (r"圆形|正圆|圆筒", "圆形"),
    (r"心形", "心形"),
    (r"椭圆形|椭圆", "椭圆形"),
]

# 性别：只认明确人群词（多字词规避「少妇」级噪音）
_GENDER_PATTERNS: list[tuple[str, str]] = [
    (r"男女通用|男女适用|男女款|中性", "男女通用"),
    (r"女款|女士|女式|女装|少女|妈妈装", "女"),
    (r"男款|男士|男式|男装|爸爸装", "男"),
]

# 伪属性键 → 同义词规则 zh_keywords 的匹配键（材质/颜色/形状/性别组）
_PSEUDO_KEYS = {
    "material": ("材质", ("材质", "材料", "面料", "主要材质")),
    "color": ("颜色", ("颜色", "颜色分类")),
    "shape": ("形状", ("形状", "外形")),
    "gender": ("性别", ("性别", "适用性别")),
}

_MATERIAL_RE = [(re.compile(p), v) for p, v in _MATERIAL_PATTERNS]
_COLOR_RE = [(re.compile(p), v) for p, v in _COLOR_PATTERNS]
_SHAPE_RE = [(re.compile(p), v) for p, v in _SHAPE_PATTERNS]
_GENDER_RE = [(re.compile(p), v) for p, v in _GENDER_PATTERNS]


def _clean_title(title: str) -> str:
    t = title or ""
    for phrase in _FALSE_POSITIVE_PHRASES:
        t = t.replace(phrase, "")
    return t


def _first_match(patterns: list[tuple[re.Pattern, str]], text: str) -> Optional[str]:
    for rx, val in patterns:
        if rx.search(text):
            return val
    return None


def _title_evidence_kinds(title: str) -> dict[str, str]:
    """扫描标题 → {kind: canonical 中文值}；颜色多色信号在场则放弃颜色。"""
    t = _clean_title(title)
    out: dict[str, str] = {}
    m = _first_match(_MATERIAL_RE, t)
    if m:
        out["material"] = m
    if not _COLOR_GIVEUP.search(t):
        c = _first_match(_COLOR_RE, t)
        if c:
            out["color"] = c
    s = _first_match(_SHAPE_RE, t)
    if s:
        out["shape"] = s
    g = _first_match(_GENDER_RE, t)
    if g:
        out["gender"] = g
    return out


def augment_draft_with_title_evidence(draft: dict) -> dict:
    """把标题证据词合成伪 draft.attributes（不覆盖真实键），返回新 draft 副本。

    消费方：prepare_ozon_upload_node 在调 _fill_optional_dict_attrs 前替换 draft，
    伪属性走既有同义词安全链（raw 中文直搜 / value_map RU 兜底 / 精确命中才填）。
    """
    draft = dict(draft or {})
    title = str(draft.get("title") or "")
    cat_path = str(draft.get("category_path") or "")
    text = f"{title} {cat_path}"
    if not text.strip():
        return draft
    kinds = _title_evidence_kinds(text)
    if not kinds:
        return draft
    attrs = dict(draft.get("attributes") or {})
    added: list[str] = []
    for kind, (pseudo_key, real_keys) in _PSEUDO_KEYS.items():
        val = kinds.get(kind)
        if not val:
            continue
        if any(k in attrs for k in real_keys):
            continue  # 真实 1688 证据优先，不覆盖
        attrs[pseudo_key] = val
        added.append(f"{pseudo_key}={val}")
    if added:
        draft["attributes"] = attrs
        logger.info("✅ 标题证据词进属性链: %s", "; ".join(added))
    return draft


# ---------------------------------------------------------------------------
# ② 标题数值派生（容量 / 每包数量）
# ---------------------------------------------------------------------------

_VOL_ML_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ml|mL|毫升)", re.IGNORECASE)
# L/升 后只挡拉丁字母（"2L保鲜盒"无空格合法；"L型" 用 (?![A-Za-z]) 挡）
_VOL_L_RE = re.compile(
    r"(?:容量|容积|大容量)?\s*(\d+(?:\.\d+)?)\s*(?:[Ll](?![A-Za-z])|升)")
_COUNT_RE = re.compile(
    r"(\d+)\s*(?:个装|只装|件装|件套|支装|片装|双|组|套|pcs|pieces)", re.IGNORECASE)


def _title_numeric_facts(title: str) -> dict[str, int]:
    t = _clean_title(title)
    facts: dict[str, int] = {}
    m = _VOL_ML_RE.search(t)
    if m:
        v = int(float(m.group(1)))
        if 50 <= v <= 50_000:
            facts["volume_ml"] = v
    if "volume_ml" not in facts:
        m = _VOL_L_RE.search(t)
        if m:
            v = int(float(m.group(1)) * 1000)
            if 50 <= v <= 50_000:
                facts["volume_ml"] = v
    m = _COUNT_RE.search(t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 100:
            facts["package_count"] = n
    return facts


_VOL_NAME_KEYS = ("体积", "объем", "объём", "容量", "volume")
_VOL_NAME_EXCLUDE = ("упаковк", "包装")  # 包装体积走商品字段，不填属性
_COUNT_NAME_KEYS = (
    "每包数量", "количество в упаковке", "количество предметов",
    "原厂包装数量", "统一计量单位", "количество штук",
)
_DIMS_NAME_KEYS = ("尺寸，毫米", "размер, мм", "размер мм")


def _name_hit(aname_lower: str, keys: tuple[str, ...], exclude: tuple[str, ...] = ()) -> bool:
    if any(x in aname_lower for x in exclude):
        return False
    return any(k in aname_lower for k in keys)


# ---------------------------------------------------------------------------
# ③ 类目级保守默认表（config/attr_class_defaults.json）
# ---------------------------------------------------------------------------

_DEFAULTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "attr_class_defaults.json",
)


def _load_class_defaults() -> list[dict]:
    try:
        with open(_DEFAULTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("defaults") if isinstance(data, dict) else data
        return [r for r in (rows or []) if isinstance(r, dict) and r.get("candidates")]
    except Exception:
        return []


def _default_entry_for(attr: dict, aname_lower: str, defaults: list[dict]) -> Optional[dict]:
    aid = int(attr.get("id") or 0)
    for entry in defaults:
        ids = {int(x) for x in (entry.get("ids") or []) if str(x).isdigit()}
        exact = [n.lower() for n in (entry.get("exact_names") or [])]
        keys = [k.lower() for k in (entry.get("name_keys") or [])]
        if exact:
            if aname_lower.strip() in exact:
                return entry
            continue  # 声明了 exact_names 的条目只走精确名匹配（防 'пол' 误击 'полотенце'）
        if aid and aid in ids:
            return entry
        if keys and any(k in aname_lower for k in keys):
            return entry
    return None


def apply_class_defaults_and_numerics(
    items: list, schema: list, draft: dict, state, audit_task_id: str = "",
) -> list:
    """类目级保守默认 + 标题数值派生 + 尺寸串（模板继承之后、值数闸之前的末端补缺）。"""
    try:
        if not items or not schema:
            return items
        item0 = next((it for it in items if isinstance(it, dict)), None)
        if item0 is None:
            return items
        existing = {
            int(a.get("id") or 0)
            for a in (item0.get("attributes") or []) if isinstance(a, dict)
        }
        attrs = item0.setdefault("attributes", [])

        title = str((draft or {}).get("title") or "")
        t = _clean_title(title)
        facts = _title_numeric_facts(title)
        defaults = _load_class_defaults()

        dc = str(getattr(state, "description_category_id", "") or "")
        tp = str(getattr(state, "type_id", "") or "")
        _cid = str(getattr(state, "ozon_client_id", "") or "")
        _key = str(getattr(state, "ozon_api_key", "") or "")
        _tenant = str(getattr(state, "user_id", "") or "")
        _task = str(audit_task_id or getattr(state, "task_id", "") or "")

        from utils.attr_match_log import log_attr_match
        from utils.ozon_dict_values import search_dictionary_values
        from utils.attr_defaults import find_dict_value_id

        def _log(aid: int, aname: str, val: str, layer: str, did: int = 0):
            try:
                log_attr_match(
                    _task, aid, aname, val, "matched", layer,
                    dictionary_value_id=did, source="attr_fill_extras",
                    tenant_id=_tenant,
                )
            except Exception:
                pass

        for attr in schema:
            if not isinstance(attr, dict):
                continue
            aid = int(attr.get("id") or 0)
            if aid <= 0 or aid in existing:
                continue
            aname = str(attr.get("name") or "")
            al = aname.lower()

            # ① 类目级保守默认（字典精确命中才填）
            entry = _default_entry_for(attr, al, defaults)
            if entry is not None:
                skip_words = entry.get("skip_if_title_has") or []
                if any(w in t for w in skip_words):
                    continue  # 标题有人群信号 → 让证据链负责，默认不抢
                filled = False
                for cand in entry.get("candidates") or []:
                    try:
                        hits = search_dictionary_values(
                            _cid, _key, aid, int(dc) if dc else 0,
                            int(tp) if tp else 0, str(cand),
                        ) or []
                    except Exception:
                        hits = []
                    res = find_dict_value_id(hits, str(cand)) if hits else None
                    if res and int(res[0] or 0) > 0:
                        attrs.append({"id": aid, "values": [
                            {"dictionary_value_id": int(res[0]), "value": str(res[1] or cand)}
                        ]})
                        existing.add(aid)
                        _log(aid, aname, str(res[1] or cand), "class_default", int(res[0]))
                        logger.info("✅ 类目默认补齐 %s(%s) = %s", aname, aid, res[1])
                        filled = True
                        break
                if filled:
                    continue

            # ② 体积派生（标题 ml/L 正则）
            vol = facts.get("volume_ml")
            if vol and _name_hit(al, _VOL_NAME_KEYS, _VOL_NAME_EXCLUDE):
                attrs.append({"id": aid, "values": [{"value": str(vol)}]})
                existing.add(aid)
                _log(aid, aname, str(vol), "title_numeric")
                logger.info("✅ 标题数值补齐 %s(%s) = %s ml", aname, aid, vol)
                continue

            # ③ 每包数量派生
            cnt = facts.get("package_count")
            if cnt and _name_hit(al, _COUNT_NAME_KEYS):
                attrs.append({"id": aid, "values": [{"value": str(cnt)}]})
                existing.add(aid)
                _log(aid, aname, str(cnt), "title_numeric")
                logger.info("✅ 标题数值补齐 %s(%s) = %s", aname, aid, cnt)
                continue

            # ④ 尺寸串（item 自身 dims，毫米）
            if _name_hit(al, _DIMS_NAME_KEYS):
                try:
                    d = int(float(item0.get("depth") or 0))
                    w = int(float(item0.get("width") or 0))
                    h = int(float(item0.get("height") or 0))
                except Exception:
                    d = w = h = 0
                if d > 0 and w > 0 and h > 0:
                    dims_str = f"{d}x{w}x{h}"
                    attrs.append({"id": aid, "values": [{"value": dims_str}]})
                    existing.add(aid)
                    _log(aid, aname, dims_str, "dims_string")
                    logger.info("✅ 尺寸串补齐 %s(%s) = %s", aname, aid, dims_str)
                    continue
        return items
    except Exception as _e:
        logger.warning("类目默认/数值派生异常（不影响主流程）: %s", _e)
        return items
