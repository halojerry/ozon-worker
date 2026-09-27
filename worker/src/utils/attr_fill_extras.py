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
  ③ sanitize_numeric_semantics(items)
     出口语义闸——2026-09-24 gate 实证：1688「箱装数量:500」（批发箱规）被
     name-heuristic 命中三个「数量」属性、「产品上市时间:2022」（年份）进了
     「组合成类似的产品」(22390，期望商品 ID 列表)。规则：
       - 22390 恒禁自动填（任何自动来源的值都是语义错配）；
       - 8513/11650/23249（每包/原厂包装/统一计量数量）值 >200 剥除
         （箱规 MOQ 特征；五金大包误伤属已知取舍，见 PLAN §五）。

审计：所有成功填点写 attr_match_log，match_layer ∈
  {title_evidence, class_default, title_numeric, dims_string}。

⑤ build_llm_schema_prompt / apply_llm_schema_fill（v0.84 A5，用户驱动：
  「每个类目的特征属性都缓存了，这样 LLM 就知道怎么填写了」）
  schema 缓存驱动 LLM 兜底——把缓存中「未填属性清单」（中文名+描述+类型+字典
  标记，属性上限 15）+ 产品证据（标题/1688 属性/dims）交给 vision LLM 提案；
  提案逐条过确定性验证（字典唯一精确/颜色全等/布尔直通/自由文本去中文/
  数值 sanity + 禁填清单），验证不过一律剥除。本模块只含确定性部分；
  LLM 调用在 prepare_ozon_upload_node._llm_schema_fill（复用 call_mxou_chat_api）。
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
    r"([一二三四五六七八九十\d]+)\s*(?:个装|只装|件装|件套|支装|片装|双|组|套|pcs|pieces)",
    re.IGNORECASE)


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _to_int(s: str) -> int:
    """数字或中文数字（含 X十Y 形态）→ int；解析失败返回 0。"""
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    if not s or any(ch not in _CN_NUM and ch != "十" for ch in s):
        return 0
    if s == "十":
        return 10
    if "十" in s:
        head, _, tail = s.partition("十")
        return _CN_NUM.get(head, 1) * 10 + (_CN_NUM.get(tail, 0) if tail else 0)
    return _CN_NUM.get(s, 0)


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
        n = _to_int(m.group(1))
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

        # A8b⑤ 数值派生的字典属性感知（gate 实证：tp=93720 类目 6788 容量是
        # 字典属性，A1 塞 dictionary_value_id=0 的文本被 validate 拦——同
        # attr_id 跨类目形态不同，落卡前必须查 schema 的 dictionary_id）。
        def _fill_numeric(attr: dict, aid: int, aname: str, txt: str, layer: str) -> bool:
            if int(attr.get("dictionary_id") or 0) <= 0:
                attrs.append({"id": aid, "values": [{"value": txt}]})
                existing.add(aid)
                _log(aid, aname, txt, layer)
                logger.info("✅ %s 补齐 %s(%s) = %s", layer, aname, aid, txt)
                return True
            try:
                _hits = search_dictionary_values(
                    _cid, _key, aid, int(dc) if dc else 0, int(tp) if tp else 0, txt) or []
            except Exception:
                _hits = []
            _res = find_dict_value_id(_hits, txt) if _hits else None
            if not (_res and int(_res[0] or 0) > 0):
                logger.info("⏭️ %s 跳过字典属性 %s(%s): 字典无命中 值=%s", layer, aname, aid, txt)
                return False
            attrs.append({"id": aid, "values": [
                {"dictionary_value_id": int(_res[0]), "value": str(_res[1] or txt)}]})
            existing.add(aid)
            _log(aid, aname, str(_res[1] or txt), layer, int(_res[0]))
            logger.info("✅ %s 补齐 %s(%s) = %s (dict_id=%s)", layer, aname, aid, _res[1], _res[0])
            return True

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
                _fill_numeric(attr, aid, aname, str(vol), "title_numeric")
                continue

            # ③ 每包数量派生
            cnt = facts.get("package_count")
            if cnt and _name_hit(al, _COUNT_NAME_KEYS):
                _fill_numeric(attr, aid, aname, str(cnt), "title_numeric")
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
                    _fill_numeric(attr, aid, aname, dims_str, "dims_string")
                    continue
        return items
    except Exception as _e:
        logger.warning("类目默认/数值派生异常（不影响主流程）: %s", _e)
        return items


# ---------------------------------------------------------------------------
# ④ 出口语义闸（gate 实证驱动）：剥除语义错配的自动数值
# ---------------------------------------------------------------------------

# 22390 组合成类似的产品：期望关联商品 ID 列表，自动来源必错（gate 实测被填 2022 年份）
_BANNED_NUMERIC_ATTR_IDS = {22390}
# 每包/原厂包装/统一计量数量：>200 视为批发箱规（MOQ）渗透，剥除
_COUNT_ATTR_MAX = 200
_COUNT_SEMANTIC_ATTR_IDS = {8513, 11650, 23249}


def sanitize_numeric_semantics(items: list) -> list:
    """剥除语义错配的自动数值属性（非致命，纯出口清洗）。

    gate 实证（2026-09-24，卡 6446931479）：1688「箱装数量:500」（批发箱规）
    被 name-heuristic 命中 8513/11650/23249；「产品上市时间:2022」进 22390。
    """
    try:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes")
            if not attrs:
                continue
            kept, dropped = [], []
            for a in attrs:
                if not isinstance(a, dict):
                    kept.append(a)
                    continue
                aid = int(a.get("id") or 0)
                if aid in _BANNED_NUMERIC_ATTR_IDS:
                    dropped.append((aid, "禁填属性(期望商品ID列表)"))
                    continue
                if aid in _COUNT_SEMANTIC_ATTR_IDS:
                    try:
                        _v = (a.get("values") or [{}])[0].get("value")
                        if _v is not None and float(str(_v)) > _COUNT_ATTR_MAX:
                            dropped.append((aid, f"箱规MOQ疑似({{{_v}}})"))
                            continue
                    except (TypeError, ValueError):
                        pass
                kept.append(a)
            if dropped:
                item["attributes"] = kept
                logger.warning("✂️ 数值语义闸剥除: %s", dropped)
        return items
    except Exception as _e:
        logger.warning("数值语义闸异常（不影响主流程）: %s", _e)
        return items


# ---------------------------------------------------------------------------
# ⑤ schema 驱动 LLM 兜底（v0.84 A5）：确定性 prompt 构造 + 提案验证
# ---------------------------------------------------------------------------

# 禁入 LLM 提案的属性：系统派生/商品字段/富媒体/合规/已在别链处理的
_LLM_FILL_BANNED_ATTR_IDS = {
    8229,   # 类型（dc/tp 派生）
    4180, 4191,  # 名称/简介（标题描述链）
    9048, 9024,  # 型号合并键/卖家代码（商品个体值）
    22232,  # HS 编码（合规恒宁缺）
    22390,  # 组合成类似的产品（A4 恒禁）
    23536,  # 需要标记代码（系统布尔）
    85, 5076,  # 品牌族（必填链恒 Нет бренда）
    4389,   # 产地（恒 Китай）
    23171,  # hashtag（assemble 生成链）
    4497, 4383,  # 重量（商品字段链，LLM 猜重量=幻觉）
    21845, 21841, 21837, 22273, 8789, 8790, 11254,  # 视频/PDF/JSON 富媒体
    11650,  # 原厂包装数量（箱规渗透重灾区，A4 已剥，源头不提案）
}
_LLM_FILL_MAX_ATTRS = 15
_LLM_FREE_TEXT_MAX = 120
# 数值 sanity 上限（体积 ml/直径 mm 等物理量合理域）
_LLM_NUMERIC_MAX = 100_000


def build_llm_schema_prompt(
    items: list, schema: list, draft: dict, dict_samples: Optional[dict] = None,
) -> tuple[Optional[str], list[dict]]:
    """构造 schema 驱动 prompt。返回 (prompt|None, 待填属性列表)。

    待填 = 缓存 schema 中未填、非禁填、非纯噪音的属性（cap 15），
    携带中文名+描述+类型+字典标记——LLM 由此「知道要填什么」。
    dict_samples: state.dictionary_values 缓存（attr_id → [{id,value},…]），
    为字典属性附最多 4 个样例值帮 LLM 对齐命名。
    """
    item0 = next((it for it in items or [] if isinstance(it, dict)), None)
    if item0 is None:
        return None, []
    existing = {int(a.get("id") or 0) for a in (item0.get("attributes") or []) if isinstance(a, dict)}
    samples = dict(dict_samples or {})
    todo = []
    for attr in schema or []:
        if not isinstance(attr, dict):
            continue
        aid = int(attr.get("id") or 0)
        if aid <= 0 or aid in existing or aid in _LLM_FILL_BANNED_ATTR_IDS:
            continue
        aname = str(attr.get("name") or "")
        # 纯噪音（臭氧/PDF 同族名已 ban ID 兜底；此处按名再滤一次尺寸重量类）
        if any(k in aname for k in ("臭氧", "视频", "PDF", "JSON", "重量", "отзыв", "видео")):
            continue
        entry = {
            "id": aid,
            "name": aname,
            "desc": str(attr.get("description") or "")[:80],
            "type": str(attr.get("type") or "String"),
            "dict": int(attr.get("dictionary_id") or 0),
            "collection": bool(attr.get("is_collection")),
        }
        _sv = samples.get(str(aid)) or []
        if _sv and entry["dict"] > 0:
            entry["样例"] = [str(v.get("value") or "") for v in _sv[:4]
                             if str(v.get("value") or "").strip()]
        todo.append(entry)
        if len(todo) >= _LLM_FILL_MAX_ATTRS:
            break
    if not todo:
        return None, []

    t = draft or {}
    attrs_dump = "; ".join(
        f"{k}={v}" for k, v in list((t.get("attributes") or {}).items())[:20]
        if str(v or "").strip()
    )
    # A7b (feat/competitor-fullattrs-v1): 竞品特征表进证据——draft.ozon_attributes
    # 是 Ozon 竞品页已过审的俄语/英语键值（follow 复制卡/page 真值透传），是比
    # 1688 推断更准的证据源；LLM 负责键名语义映射（EN/RU→schema 中文名），
    # 值仍过确定性字典验证（宁缺红线不变）。竞品事实类（品牌/认证）除外——
    # 规则 5 已禁品牌，认证类验证闸兜底。
    comp_dump = "; ".join(
        f"{k}={v}" for k, v in list((t.get("ozon_attributes") or {}).items())[:30]
        if str(v or "").strip()
    )
    lines = [
        "根据商品资料，为下列 Ozon 商品特征属性给出值。规则：",
        "1. 自由文本属性(String 且 dict=0)：只填资料能确定的内容，不确定输出 null；",
        "   必须用俄语输出（Ozon 卡片展示语言）——资料/1688 证据是中文时由你翻译。",
        "2. 字典类属性(dict>0)：给出你的最佳判断（中文名称即可，参考「样例」的命名风格）。",
        "   系统会查字典验证，验证不过会自动丢弃、不会上卡，所以不必过度保守；",
        "   但绝不编造资料/图片中完全没有的事实（品牌、认证、精确尺寸除外——图片可见可估）。",
        "3. 类型为 Boolean 的属性只输出 true/false（仅当资料/图片能确认）。",
        "4. 数值属性输出纯数字（不带单位）。产品图上可见的容量/体积/数量（瓶身标注、包装文字）可以读出填入——图片是可靠证据。",
        "5. 品牌名称一律不填（资料视为无品牌）。",
        "",
        f"商品标题: {str(t.get('title') or '')[:120]}",
        f"商品类目: {str(t.get('category_path') or '')[:60]}",
        f"1688属性: {attrs_dump[:600] or '无'}",
        "竞品特征(来自Ozon同类商品卡,键为俄语/英语,优先级高于1688推断,但品牌不填):",
        f"{comp_dump[:900] or '无'}",
        f"尺寸mm: {item0.get('depth','')}x{item0.get('width','')}x{item0.get('height','')}",
        "",
        "待填属性（JSON 数组）:",
        json.dumps(todo, ensure_ascii=False),
        "",
        "只输出 JSON: {\"fills\": [{\"id\": 数字, \"value\": 值或 null}]}；",
        "值可为中文/俄语/数字/布尔（自由文本属性值一律俄语）。",
    ]
    return "\n".join(lines), todo


def _translate_ru(text: str, token: str) -> str:
    """中文自由文本提案 → 俄语（A5 验证闸兜底，v0.80 gate 实证新增）。

    LLM 属性提案用 1688 中文证据时值本身有效、只是语言错（护手霜 8048
    「取适量本品涂抹…」被整条剥除的浪费）。链路里标题/描述已有自动翻译
    先例，属性值同待遇：翻译一次后按非中文规则重验。任何失败/空译文/
    译文仍含中文 → 返回空串（调用方照剥，宁缺红线不变）。
    """
    if not any('\u4e00' <= ch <= '\u9fff' for ch in text):
        return text.strip()
    try:
        from utils.mxou_api import call_mxou_chat_api, MxouOutOfQuotaError
        ans = call_mxou_chat_api(
            token=str(token or ""),
            system_prompt="你是电商商品属性翻译。把中文属性值翻译成简洁自然的俄语，只输出译文本身，不加任何解释或引号。",
            user_prompt=text[:500],
            model="deepseek-v4-flash-vision-exp",
            temperature=0.0,
            max_tokens=512,
        )
        out = str(ans or "").strip().strip('"').strip()
        if not out or len(out) > _LLM_FREE_TEXT_MAX:
            return ""
        if any('\u4e00' <= ch <= '\u9fff' for ch in out):
            return ""
        return out
    except MxouOutOfQuotaError:
        raise  # ✅ v0.80 arch-findings #4: 余额/鉴权永久错误必须上抛（apply_llm_schema_fill → prepare A5 链路透传任务明确失败），不吞成空串「剥除处理」静默降级
    except Exception as _e:
        logger.warning("属性值中文翻译失败（剥除处理）: %s", _e)
        return ""


def apply_llm_schema_fill(
    items: list, todo: list[dict], raw_answer: str,
    draft: dict, state, audit_task_id: str = "",
) -> list:
    """验证 LLM 提案并落卡（确定性验证，宁缺红线不变）。

    字典类：search 精确唯一命中才填（颜色类沿用全等放宽）；
    Boolean：true/false 直通；数值：纯数字 sanity；自由文本：去中文+限长。
    """
    # ✅ v0.80 arch-findings #4: MxouOutOfQuotaError 透传闸——下方 except Exception
    # 会吞掉 _translate_ru 等上抛的余额/鉴权永久错误，必须先于它放行（任务明确失败，
    # 不静默降级为「填充异常」warning 照跑）。
    from utils.mxou_api import MxouOutOfQuotaError
    try:
        item0 = next((it for it in items or [] if isinstance(it, dict)), None)
        if item0 is None or not raw_answer or not todo:
            return items
        m = re.search(r"\{[\s\S]*\}", raw_answer)
        if not m:
            return items
        data = json.loads(m.group(0))
        proposals = data.get("fills") if isinstance(data, dict) else None
        if not isinstance(proposals, list):
            return items

        todo_by_id = {a["id"]: a for a in todo}
        existing = {int(a.get("id") or 0) for a in (item0.get("attributes") or []) if isinstance(a, dict)}
        attrs = item0.setdefault("attributes", [])
        dc = str(getattr(state, "description_category_id", "") or "")
        tp = str(getattr(state, "type_id", "") or "")
        _cid = str(getattr(state, "ozon_client_id", "") or "")
        _key = str(getattr(state, "ozon_api_key", "") or "")
        _tenant = str(getattr(state, "user_id", "") or "")
        _task = str(audit_task_id or getattr(state, "task_id", "") or "")

        from utils.ozon_dict_values import search_dictionary_values
        from utils.attr_value_matcher import unique_or_none

        for p in proposals:
            if not isinstance(p, dict):
                continue
            try:
                aid = int(p.get("id") or 0)
            except (TypeError, ValueError):
                logger.info("⏭️ schema-LLM 剥除: 非法 id=%r", p.get("id"))
                continue
            val = p.get("value")
            if val is None:
                logger.info("⏭️ schema-LLM 剥除 %s: LLM 自认不确定(null)", aid)
                continue
            if aid <= 0 or aid in existing or aid in _LLM_FILL_BANNED_ATTR_IDS or aid not in todo_by_id:
                logger.info("⏭️ schema-LLM 剥除 %s: 已填/禁填/不在待填单", aid)
                continue
            meta = todo_by_id[aid]
            aname = meta["name"]

            # Boolean 直通
            if str(meta.get("type")) == "Boolean":
                bv = str(val).strip().lower()
                if bv in ("true", "false"):
                    attrs.append({"id": aid, "values": [{"dictionary_value_id": 0, "value": bv}]})
                    existing.add(aid)
                    _log_llm(_task, aid, aname, bv, _tenant)
                else:
                    logger.info("⏭️ schema-LLM 剥除 %s(%s): Boolean 非法值=%r", aid, aname, val)
                continue

            sval = str(val).strip()
            if not sval or len(sval) > _LLM_FREE_TEXT_MAX:
                logger.info("⏭️ schema-LLM 剥除 %s(%s): 空/超长(%s)", aid, aname, len(sval))
                continue

            if meta.get("dict", 0) > 0:
                # A8b③ 近义候选归一放宽（gate 实证 6383 材质「Нержавеющая сталь」
                # 撞字典 3 个同义写法被剥）：search 命中多候选时，若**全部候选**
                # 拉丁/西里尔归一后互相一致（同一概念的不同拼写），取字典序第一
                # ——确定性规则，不是猜；候选语义真正分歧时仍剥（宁缺红线）。
                def _norm_txt(sv: str) -> str:
                    _t = sv.lower().replace("ё", "е")
                    _t = re.sub(r"[.\-_/]", " ", _t)
                    return re.sub(r"\s+", " ", _t).strip()

                def _same_concept(a: str, b: str) -> bool:
                    """归一后同概念：全等，或逐词互为前缀缩写（нерж.↔нержавеющая）。

                    缩写判定要求两侧词长 ≥3（防止虚词/单字母误判），逐词位置
                    对应；词数不同直接不同概念（宁缺红线）。
                    """
                    if a == b:
                        return True
                    _ta, _tb = a.split(), b.split()
                    if len(_ta) != len(_tb) or not _ta:
                        return False
                    return all(
                        _x == _y or (len(_x) >= 3 and len(_y) >= 3
                                     and (_x.startswith(_y) or _y.startswith(_x)))
                        for _x, _y in zip(_ta, _tb))

                def _resolve_one(sv: str, _aid: int = aid, _aname: str = aname,
                                 _cid: str = _cid, _key: str = _key,
                                 _dc: str = dc, _tp: str = tp) -> tuple[int, str] | None:
                    """单值字典解析：search + 唯一命中/颜色全等/近义归一放宽。

                    循环变量经默认参数固化（B023）：本函数在循环体内定义并
                    立即调用，无延迟引用，绑定只为静态检查与语义显式化。
                    """
                    try:
                        _hits = search_dictionary_values(
                            _cid, _key, _aid, int(_dc) if _dc else 0, int(_tp) if _tp else 0, sv) or []
                    except Exception:
                        _hits = []
                    if not _hits:
                        return None
                    _res = unique_or_none(_aid, _aname, _hits)
                    if _res.status == "matched" and _res.dictionary_value_id > 0:
                        return int(_res.dictionary_value_id), str(_res.value or sv)
                    # 颜色类全等放宽（v082 T2 同款语义）
                    _is_color = _aid in (10096, 10097) or "цвет" in _aname.lower() or "颜色" in _aname
                    _eq = next((h for h in _hits if str((h or {}).get("value") or "").strip().lower()
                                == sv.lower()), None) if _is_color else None
                    if _eq and int(_eq.get("id") or 0) > 0:
                        return int(_eq["id"]), str(_eq.get("value") or sv)
                    # A8b③ 全候选互相归一/缩写一致 → 同一概念多写法，取字典序第一
                    _vals = [str((h or {}).get("value") or "") for h in _hits
                             if str((h or {}).get("value") or "").strip()]
                    _first_v = min(_vals)
                    _fn = _norm_txt(_first_v)
                    if all(_same_concept(_fn, _norm_txt(v)) for v in _vals[1:]):
                        _first = next(h for h in _hits
                                      if str((h or {}).get("value") or "") == _first_v)
                        if int(_first.get("id") or 0) > 0:
                            logger.info("🔀 schema-LLM 近义归一 %s(%s): %d 候选同概念 → %s",
                                        _aid, _aname, len(_hits), _first.get("value"))
                            return int(_first["id"]), str(_first.get("value") or sv)
                    logger.info("⏭️ schema-LLM 剥除 %s(%s): 字典 %s 候选无唯一/全等/归一 值=%s",
                                _aid, _aname, len(_hits), sv[:30])
                    return None

                # A8b② 集合类多值拆分（gate 实证 6829「带支架；含盖」整串查字典
                # 恒无命中）：is_collection 属性按分隔符拆分逐值解析，命中的
                # 逐个落卡（Ozon collection 契约本就收数组）；单值行为不变。
                _parts = ([p.strip() for p in re.split(r"[;；,，、/]", sval) if p.strip()]
                          if meta.get("collection") and len(sval) > 2 else [sval])
                _resolved: list[dict] = []
                for _p in _parts:
                    _hit = _resolve_one(_p)
                    if _hit:
                        _dv, _fv = _hit
                        if any('\u4e00' <= ch <= '\u9fff' for ch in _fv):
                            _fv = ""  # 中文值清空，dict_id 权威
                        _resolved.append({"dictionary_value_id": _dv, "value": _fv})
                if not _resolved:
                    continue  # 全部未命中（含无命中/多分歧）→ 剥，日志已在 _resolve_one 打
                attrs.append({"id": aid, "values": _resolved})
                existing.add(aid)
                _log_llm(_task, aid, aname, "; ".join(str(v["value"] or v["dictionary_value_id"])
                                                       for v in _resolved), _tenant,
                         int(_resolved[0]["dictionary_value_id"]))
                logger.info("✅ schema-LLM 补齐 %s(%s) = %s", aname, aid,
                            "; ".join(str(v["value"]) for v in _resolved))
                continue

            # 自由文本/数值
            if re.fullmatch(r"-?\d+(?:\.\d+)?", sval):
                try:
                    if not (0 < abs(float(sval)) <= _LLM_NUMERIC_MAX):
                        logger.info("⏭️ schema-LLM 剥除 %s(%s): 数值越界=%s", aid, aname, sval)
                        continue
                except ValueError:
                    continue
                if aid in {8513, 23249} and float(sval) > 200:  # 数量类语义闸前移
                    logger.info("⏭️ schema-LLM 剥除 %s(%s): 数量类>200=%s", aid, aname, sval)
                    continue
            else:
                if any('\u4e00' <= ch <= '\u9fff' for ch in sval):
                    # v0.80 gate 实证（护手霜 8048 使用方法）：LLM 用 1688 中文证据
                    # 提案——值本身有效只是语言错。走一次翻译重验（fail-closed：
                    # 翻译失败/结果仍含中文照剥，宁缺红线不变）。
                    _ru = _translate_ru(sval, str(getattr(state, "token", "") or ""))
                    if not _ru:
                        logger.info("⏭️ schema-LLM 剥除 %s(%s): 自由文本含中文且翻译失败=%s", aid, aname, sval[:30])
                        continue  # 自由文本必须非中文（Ozon RU 市场）
                    logger.info("🔁 schema-LLM 中文提案翻译重验 %s(%s): %r → %r", aid, aname, sval[:30], _ru[:40])
                    sval = _ru
            attrs.append({"id": aid, "values": [{"dictionary_value_id": 0, "value": sval}]})
            existing.add(aid)
            _log_llm(_task, aid, aname, sval, _tenant)
            logger.info("✅ schema-LLM 补齐 %s(%s) = %s (free-text)", aname, aid, sval[:40])
        return items
    except MxouOutOfQuotaError:
        raise  # ✅ v0.80 arch-findings #4: 余额/鉴权永久错误透传（prepare A5 双层 except 前置同款），不吞成 warning
    except Exception as _e:
        logger.warning("schema-LLM 填充异常（不影响主流程）: %s", _e)
        return items


def _log_llm(task: str, aid: int, aname: str, val: str, tenant: str, dict_id: int = 0):
    try:
        from utils.attr_match_log import log_attr_match
        log_attr_match(
            task_id=task, attr_id=aid, attr_name=aname, source_value=str(val)[:60],
            status="matched", match_layer="llm_schema",
            dictionary_value_id=dict_id, confidence=0.8, source="attr_fill_extras",
            tenant_id=tenant,
        )
    except Exception:
        pass
