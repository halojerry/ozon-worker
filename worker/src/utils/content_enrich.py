"""内容评分闭环共享层（v0.81，feat/content-rating-loop-v081）。

Ozon 内容评级（content rating）0-100 直接影响搜索展示：rating < 40 几乎不展示，
> 90 优秀。实机取证（2026-09-26）：我们管线 characteristics 填得尚可，但
**Аннотация(4191) 与 Rich-контент JSON(11254) 从不上卡**（文本描述组占 50% 权重），
import 补 4191+11254 后卡分实测 42-57 → 90+。

本模块是「评分驱动的卡片增强」唯一逻辑入口，三消费方：
  1. prepare_ozon_upload_node 出口恒填闸（预防层，4191/11254）；
  2. fetch_back_node 过审后复检闭环（rating-by-sku → 可填属性 → /v3 UPDATE）；
  3. scripts/content_rating_sweep.py 存量清扫（复用同一构造器，防逻辑漂移）。

契约坑（2026-09-26 实机踩过，改 UPDATE 构造前必读）：
- /v3/product/import 的 dimensions 是**扁平字段**（depth/width/height + dimension_unit、
  weight + weight_unit），不得嵌套、不得为 0；
- price/old_price 是**字符串**；old_price 规则唯一出口
  utils/pricing_estimate.enforce_old_price_rule（≥ price×1.2，且差价 <400 时 ≥20）；
- attributes 是**全量替换语义**——UPDATE 必须先拉现卡 /v4/product/info/attributes
  全量回显再叠加新值，否则把卡片其余特征洗掉（同 A6 防洗卡纪律）；
- complex_attributes/images360/pdf_list/vat 同属全量替换域：v0.81.1 前写死
  空数组/vat="0" 会洗掉现卡视频/PDF/360 图/税率——现口径「能回读带回真实值，
  回读不到省略键」（回读通道见 _card_echo_base 留证）；
- 被 Ozon 擦掉的值（erased_attribute_value）是警告不是失败。

设计口径：**保守不确定就不填**。视频/图片类属性（21841/21845/4195）我们产不了恒排除；
字典型属性无字典 id 证据跳过；含中文且无法确定性译俄的自由文本值跳过（中文零容忍，
宁缺毋滥）。本模块零网络依赖（纯函数），HTTP 由调用方（ozon_client / 脚本）承接。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# old_price 唯一规则（v0.81.1 收敛）：常量与实现在 utils/pricing_estimate，
# 此处 re-export 既有名字保持兼容（clamp_old_price 薄转发同一出口）。
from utils.pricing_estimate import (
    LARGE_OLD_PRICE_GAP,
    MIN_OLD_PRICE_GAP,
    enforce_old_price_rule,
)

# 评级阈值：>= 90 视为优秀，复检闭环不再动作
RATING_THRESHOLD = 90.0
# Аннотация（简介，HTML 富文本）
ANNOTATION_ATTR_ID = 4191
# Rich-контент JSON（图文小插件）
RICH_CONTENT_ATTR_ID = 11254
# 媒体类属性：视频主图/视频/图片——我们产不了，恒排除（在审计块如实列出）
MEDIA_ATTR_IDS = frozenset({21841, 21845, 4195})
# Rich content 取前 4 张图（实机验证口径）
RICH_CONTENT_MAX_IMAGES = 4

_CJK_RE = re.compile(r"[\u2e80-\u2eff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_INT_RE = re.compile(r"\d+")

# 常见中文材料/成分值 → 俄语（确定性映射；映射不到 → 跳过不填，宁缺毋滥）。
# 覆盖 1688 高频材料词；新增按生产实证补。
_ZH_VALUE_RU: Dict[str, str] = {
    "木": "дерево",
    "木质": "дерево",
    "实木": "массив дерева",
    "竹": "бамбук",
    "竹木": "бамбук",
    "塑料": "пластик",
    "pp": "полипропилен",
    "pe": "полиэтилен",
    "abs": "пластик ABS",
    "硅胶": "силикон",
    "不锈钢": "нержавеющая сталь",
    "金属": "металл",
    "铝": "алюминий",
    "合金": "металлический сплав",
    "铁": "железо",
    "玻璃": "стекло",
    "陶瓷": "керамика",
    "棉": "хлопок",
    "涤纶": "полиэстер",
    "布": "ткань",
    "布艺": "ткань",
    "无纺布": "нетканый материал",
    "纸": "бумага",
    "皮革": "кожа",
    "pu": "экокожа",
    "橡胶": "резина",
    "尼龙": "нейлон",
    "亚克力": "акрил",
    "海绵": "поролон",
    "毛绒": "плюш",
    "麻": "лён",
}

# 语义槽位表：(俄语属性名关键词..., 中文 draft 键关键词...)
# 用于 fillable_improves 把 improve_attributes（俄语名）对到 draft 中文证据键。
_SEMANTIC_SLOTS: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("мощност",), ("功率", "瓦数")),
    (("количеств", "штук", "единиц"), ("数量", "包装数量", "片数", "支数", "只数", "包数", "个数")),
    (("размер", "габарит"), ("尺寸", "规格", "尺寸规格")),
    (("длин",), ("长度",)),
    (("ширин",), ("宽度",)),
    (("высот",), ("高度",)),
    (("толщин",), ("厚度",)),
    (("вес", "масса"), ("重量", "净重", "毛重", "产品重量")),
    (("материал", "состав"), ("材料", "材质", "主要材料", "面料", "成分")),
    (("объём", "объем", "емкост", "ёмкост"), ("容量", "容积", "水容量")),
    (("производител",), ("制造商", "厂家", "生产厂家")),
    (("модел",), ("型号", "产品型号")),
)


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _strip_cjk(text: str) -> str:
    return _CJK_RE.sub("", str(text or ""))


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _translate_zh_value(value: str) -> str:
    """中文属性值 → 俄语。整串/子串最长匹配 _ZH_VALUE_RU；映射不到返回空串。"""
    v = _norm_text(value).lower()
    if not v:
        return ""
    if v in _ZH_VALUE_RU:
        return _ZH_VALUE_RU[v]
    for zh in sorted(_ZH_VALUE_RU, key=len, reverse=True):
        if zh in v:
            return _ZH_VALUE_RU[zh]
    return ""


def enrichment_enabled() -> bool:
    """env 闸 CONTENT_RATING_ENHANCE（默认开；=0 关闭）。"""
    import os

    return os.getenv("CONTENT_RATING_ENHANCE", "1").strip() != "0"


# ── 生成器 ────────────────────────────────────────────────────


def build_annotation(title_ru: str, attrs: Dict[str, Any]) -> str:
    """从标题 + draft 中文属性证据组装 2-3 句 RU HTML 简介（属性 4191）。

    格式 `<p>...</p>`；风格参照实机验证样本：
    `<p>Деревянные одноразовые ложки, 100 шт в упаковке. Натуральный материал... </p>`
    - 数量：键含 数量/*装 → 提取整数 → "N шт в упаковке"；
    - 材料：确定性 ZH→RU 映射命中 → "Материал: X"；映射不到不编造；
    - 尺寸：数字串透传（语言无关）→ "Размер: ..."；
    - 功率：整数 → "Мощность: N Вт"；
    - 无任何证据 → 标题 + 通用一句（不虚构参数）。
    畸形输入（None/非 dict attrs）不 raise；输出恒无中文（防御剥除）。
    """
    title = _norm_text(_strip_cjk(_HTML_TAG_RE.sub(" ", str(title_ru or ""))))
    if not title:
        title = "Товар"
    if not isinstance(attrs, dict):
        attrs = {}

    sentences: List[str] = []

    qty = _find_number(attrs, ("数量", "包装数量", "片数", "支数", "只数", "包数", "个数"), suffix_key="装")
    head = f"{title}, {qty} шт в упаковке" if qty else title
    if not head.endswith((".", "!", "?")):
        head += "."
    sentences.append(head)

    material = _find_by_keys(attrs, ("材料", "材质", "主要材料", "面料", "成分"))
    material_ru = _translate_zh_value(material) if material else ""
    if material_ru:
        sentences.append(f"Материал: {material_ru}.")

    size = _find_by_keys(attrs, ("尺寸", "规格", "尺寸规格"))
    size_digits = _norm_text(_strip_cjk(_HTML_TAG_RE.sub(" ", str(size or "")))) if size else ""
    if size_digits and re.search(r"\d", size_digits):
        sentences.append(f"Размер: {size_digits}.")

    power = _find_by_keys(attrs, ("功率", "瓦数"))
    power_num = _first_int(power) if power else None
    if power_num:
        sentences.append(f"Мощность: {power_num} Вт.")

    if len(sentences) == 1:
        sentences.append("Качественный товар для дома и повседневного использования.")
    sentences.append("Идеально для дома и повседневного использования.")

    body = " ".join(sentences)
    body = _strip_cjk(body)
    body = _norm_text(body)
    if len(body) > 800:
        body = body[:797].rstrip() + "..."
    return f"<p>{body}</p>"


def _find_by_keys(attrs: Dict[str, Any], zh_keywords: Tuple[str, ...]) -> str:
    """按中文键关键词找第一个非空属性值。"""
    for key, value in attrs.items():
        k = str(key or "")
        if not any(kw in k for kw in zh_keywords):
            continue
        v = str(value or "").strip()
        if v:
            return v
    return ""


def _find_number(
    attrs: Dict[str, Any],
    zh_keywords: Tuple[str, ...],
    suffix_key: str = "",
) -> Optional[int]:
    """找数量证据：键含关键词，或键以 suffix_key 结尾（如「100只装」）。"""
    direct = _first_int(_find_by_keys(attrs, zh_keywords))
    if direct:
        return direct
    if suffix_key:
        for key, value in attrs.items():
            k = str(key or "")
            if k.endswith(suffix_key):
                n = _first_int(value)
                if n:
                    return n
    return None


def _first_int(value: Any) -> Optional[int]:
    m = _INT_RE.search(str(value or ""))
    return int(m.group()) if m else None


def build_rich_json(images: List[str], title_ru: str) -> Optional[str]:
    """确定性生成 Rich-контент JSON（属性 11254）。

    结构为 2026-09-26 真实卡验证有效口径（raShowcase / chess / img 块、
    position=to_the_edge），前 4 张图；json.dumps ensure_ascii=False。
    有效图 < 2 张返回 None（chess 最低 2 blocks，不强造）。
    """
    imgs: List[str] = []
    for img in images or []:
        if isinstance(img, str) and img.strip() and img.strip() not in imgs:
            imgs.append(img.strip())
    if len(imgs) < 2:
        return None
    blocks = [
        {
            "img": {
                "src": url,
                "srcMobile": url,
                "alt": _norm_text(_strip_cjk(str(title_ru or "")))[:120],
                "position": "to_the_edge",
                "positionMobile": "to_the_edge",
            }
        }
        for url in imgs[:RICH_CONTENT_MAX_IMAGES]
    ]
    payload = {"content": [{"widgetName": "raShowcase", "type": "chess", "blocks": blocks}]}
    return json.dumps(payload, ensure_ascii=False)


# ── 评级响应解析 ──────────────────────────────────────────────


def parse_rating_products(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """rating-by-sku 响应 → products 列表（畸形输入防御）。"""
    if not isinstance(resp, dict):
        return []
    products = resp.get("products")
    if not isinstance(products, list):
        return []
    return [p for p in products if isinstance(p, dict)]


def pick_product(products: List[Dict[str, Any]], sku: Any) -> Optional[Dict[str, Any]]:
    """按 sku 选对应卡；未命中回退首个。"""
    want = str(sku or "")
    for p in products:
        if str(p.get("sku", "")) == want:
            return p
    return products[0] if products else None


def extract_improve_attrs(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    """products[] 元素 → 去重 improve_attributes 列表 [{"id", "name"}]（保序）。"""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for group in product.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("improve_attributes") or []:
            if not isinstance(item, dict):
                continue
            try:
                aid = int(item.get("id") or 0)
            except (ValueError, TypeError):
                continue
            if aid <= 0 or aid in seen:
                continue
            seen.add(aid)
            out.append({"id": aid, "name": str(item.get("name") or "")})
    return out


def rating_groups_summary(product: Dict[str, Any]) -> Dict[str, float]:
    """groups → {name: rating}（审计块用）。"""
    out: Dict[str, float] = {}
    for group in product.get("groups") or []:
        if isinstance(group, dict) and group.get("name"):
            try:
                out[str(group["name"])] = float(group.get("rating") or 0)
            except (ValueError, TypeError):
                continue
    return out


# ── 可填属性裁决 ──────────────────────────────────────────────


def fillable_improves(
    improve_attrs: List[Dict[str, Any]],
    draft_attrs: Dict[str, Any],
    schema_by_id: Dict[int, Dict[str, Any]],
) -> Dict[int, str]:
    """给定 improve_attributes，返回可用 draft 证据填充的 {attr_id: value}。

    规则（保守：不确定就不填）：
    - 视频/图片类 id（21841/21845/4195）与 4191/11254 恒排除
      （前者产不了，后者走专用生成器）；
    - schema 缺失该属性 → 跳过；type 非 String/Integer → 跳过；
    - 字典型属性（dictionary_id > 0）无字典 id 证据 → 跳过；
    - draft 无同语义中文键 → 跳过；
    - Integer：提取数字；String：中文值须命中确定性 ZH→RU 映射，否则跳过
      （中文零容忍，绝不把中文值送上卡）。
    """
    if not isinstance(draft_attrs, dict):
        draft_attrs = {}
    out: Dict[int, str] = {}
    for item in improve_attrs or []:
        if not isinstance(item, dict):
            continue
        try:
            aid = int(item.get("id") or 0)
        except (ValueError, TypeError):
            continue
        if aid <= 0 or aid in MEDIA_ATTR_IDS:
            continue
        if aid in (ANNOTATION_ATTR_ID, RICH_CONTENT_ATTR_ID):
            continue
        schema = schema_by_id.get(aid) if isinstance(schema_by_id, dict) else None
        if not isinstance(schema, dict):
            continue
        attr_type = str(schema.get("type") or "")
        if attr_type not in ("String", "Integer"):
            continue
        try:
            if int(schema.get("dictionary_id") or 0) > 0:
                continue  # 字典属性无字典 id 证据，宁缺毋滥
        except (ValueError, TypeError):
            continue
        value = _match_draft_value(aid_name=str(item.get("name") or ""), draft_attrs=draft_attrs)
        if not value:
            continue
        if attr_type == "Integer":
            num = _first_int(value)
            if not num:
                continue
            out[aid] = str(num)
        else:
            v = _norm_text(_strip_cjk(_HTML_TAG_RE.sub(" ", str(value))))
            if _has_cjk(value):
                translated = _translate_zh_value(value)
                if not translated:
                    continue  # 中文且映射不到 → 跳过
                v = translated
            if not v or len(v) > 200:
                continue
            out[aid] = v
    return out


def _match_draft_value(aid_name: str, draft_attrs: Dict[str, Any]) -> str:
    """俄语属性名 → 语义槽位 → draft 中文键证据值。"""
    name = str(aid_name or "").lower()
    for ru_keywords, zh_keywords in _SEMANTIC_SLOTS:
        if not any(kw in name for kw in ru_keywords):
            continue
        return _find_by_keys(draft_attrs, zh_keywords)
    return ""


# ── UPDATE 构造（唯一入口：复检闭环与清扫脚本共用）─────────────


def clamp_old_price(price: float, old_price: float) -> int:
    """Ozon old_price 规则薄转发（v0.81.1 收敛：唯一实现在
    utils/pricing_estimate.enforce_old_price_rule——「≥ price×1.2 且差价 <400
    必须 ≥20」。保留名字做兼容，消费方零改动）。"""
    enforced = enforce_old_price_rule(price, old_price)
    if enforced is None:
        # price 无效（≤0/畸形）→ 纯防御兜底（上游 no_price 闸已挡该路径）
        return int(price or 0) + MIN_OLD_PRICE_GAP
    return int(enforced)


def _norm_vat(vat: Any) -> str:
    """vat 规整：非空字符串才回显（绝不写死 "0"——v0.81.1 前写死会把非零税率卡洗成 0）。"""
    v = str(vat if vat is not None else "").strip()
    if not v or v.lower() == "none":
        return ""
    return v


def _card_echo_base(
    product_id: Any,
    stored_item: Dict[str, Any],
    *,
    price: Any,
    old_price: Any = None,
    currency_code: str = "CNY",
    images_override: Optional[List[str]] = None,
    vat: Any = None,
    images360: Optional[List[Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """构造 /v3/product/import UPDATE item 的全量回显骨架（不含 attributes）。

    ⚠️ 防洗卡契约取证（v0.81.1，改回显逻辑前先读；mcp ozon_describe_method）：
    - import 是全量替换语义（ProductAPI_ImportProductsV3：«При обновлении товара
      передайте в запросе всю информацию о нём»），媒体改动词更明示 «Скопируйте
      данные полей images, images360, color_image»（/v3/product/info/list 取）；
    - v0.81.1 前写死 `complex_attributes: [] / images360: [] / pdf_list: [] /
      vat: "0"`，全量替换下会洗掉现卡视频/PDF/360 图/税率——修复口径：
      **Ozon 能回读的字段带回真实值，回读不到的省略键，绝不发空数组/写死值**；
    - 回读通道实证：/v4/product/info/attributes（ProductAPI_GetProductAttributesV4）
      响应含 complex_attributes/pdf_list/color_image，**不含 vat/images360**；
      vat/images360 只在 /v3/product/info/list（ProductAPI_GetProductInfoList）
      响应里 → 由调用方取到后传入，传不到就省略键；
    - 形状留证：v4 的 complex_attributes[].values 用 camelCase dictionaryValueId
      （import 请求是 snake_case dictionary_value_id）——原样透传 Ozon 自家回显，
      不做键名改写（Ozon 两侧各自认自家的形状）。

    Args:
        product_id: Ozon product_id。
        stored_item: /v4/product/info/attributes 回显的单卡 item。
        price/old_price/currency_code: 现价三元组（/v5/product/info/prices）。
        images_override: 改图场景的新图 URL 列表；None = 回显现卡图（评分增强/清扫）。
        vat: /v3/product/info/list 回显的税率（None/空 → 省略键）。
        images360: /v3/product/info/list 回显的 360 图（None/空 → 省略键）。

    Returns:
        (parts, reason)。parts=None 表示不该动作（reason ∈ no_card_echo /
        bad_product_id / no_price / zero_dims / no_images，保守放弃语义）。
        parts: {"item": 骨架 dict（无 attributes 键）, "card_attrs": {aid: attr},
                "img_urls": 图列表, "primary_image": 主图}。
    """
    if not isinstance(stored_item, dict) or not stored_item:
        return None, "no_card_echo"

    try:
        pid = int(product_id)
    except (ValueError, TypeError):
        return None, "bad_product_id"

    _p_num = _first_int(price)
    if not _p_num or _p_num <= 0:
        return None, "no_price"
    _o_num = _first_int(old_price) or _p_num

    weight = _first_int(stored_item.get("weight"))
    depth = _first_int(stored_item.get("depth"))
    width = _first_int(stored_item.get("width"))
    height = _first_int(stored_item.get("height"))
    if not weight or not depth or not width or not height:
        # 扁平 dimensions 不得为 0——现卡缺失维度时保守放弃（不编造）
        return None, "zero_dims"

    primary_image = ""
    if images_override is not None:
        # 改图场景：调用方给定的存活 URL 列表（顺序即卡上顺序）
        img_urls: List[str] = []
        for u in images_override:
            s = str(u or "").strip()
            if s and s not in img_urls:
                img_urls.append(s)
        if not img_urls:
            return None, "no_images"
        # 现卡主图仍在新图集 → 保持；不在 → 省略键（import 契约：缺省取 images[0]）
        card_primary = stored_item.get("primary_image") or ""
        if isinstance(card_primary, list):
            card_primary = card_primary[0] if card_primary else ""
        card_primary = str(card_primary or "").strip()
        if card_primary and card_primary in img_urls:
            primary_image = card_primary
    else:
        images_in = stored_item.get("images") or []
        img_urls = []
        if isinstance(images_in, list):
            def _img_key(im: Dict[str, Any]) -> int:
                try:
                    return int(im.get("index") or 0)
                except (ValueError, TypeError):
                    return 0

            # ⚠️ /v4 回显双形状（2026-09-27 实测）：新卡为 dict[{file_name,index,default}]，
            # 旧 workbuddy 卡为**纯字符串 URL 数组**——字符串直接采纳，dict 走原逻辑。
            _str_urls = [str(x).strip() for x in images_in if isinstance(x, str) and str(x).strip()]
            _dict_imgs = sorted([x for x in images_in if isinstance(x, dict)], key=_img_key)
            for url in _str_urls:
                if url not in img_urls:
                    img_urls.append(url)
            for im in _dict_imgs:
                url = str(im.get("file_name") or "").strip()
                if url and url not in img_urls:
                    img_urls.append(url)
                if im.get("default") and url:
                    primary_image = url
        if not img_urls:
            # 空图回显会把卡上图片洗没（全量替换语义）——保守放弃
            return None, "no_images"

    item: Dict[str, Any] = {
        "product_id": pid,
        "offer_id": str(stored_item.get("offer_id") or ""),
        "name": str(stored_item.get("name") or ""),
        "description_category_id": _first_int(stored_item.get("description_category_id")) or 0,
        "type_id": _first_int(stored_item.get("type_id")) or 0,
        # 扁平 dimensions（契约坑：不得嵌套、不得为 0）
        "weight": weight,
        "weight_unit": str(stored_item.get("weight_unit") or "g"),
        "depth": depth,
        "width": width,
        "height": height,
        "dimension_unit": str(stored_item.get("dimension_unit") or "mm"),
        "currency_code": str(currency_code or "CNY"),
        "price": str(_p_num),
        "old_price": str(clamp_old_price(_p_num, _o_num)),
        "images": img_urls[:30],
    }
    if primary_image:
        item["primary_image"] = primary_image

    # vat：/v3/product/info/list 回显真实值；取不到省略键（v0.81.1 前写死 "0"
    # 会把非零税率卡洗成 0——vat 不在 /v4 attributes 响应里，MCP 实证）。
    _vat = _norm_vat(vat)
    if _vat:
        item["vat"] = _vat
    # images360：info/list 可读回 → 调用方传了才带回；省略键不发空数组（全量替换
    # 域，[] 会洗掉现卡 360 图——import 契约明示改图时 «Скопируйте ... images360»）。
    if images360:
        im360 = [str(u).strip() for u in images360 if str(u or "").strip()]
        if im360:
            item["images360"] = im360[:70]  # 契约上限 70
    # complex_attributes（视频 21841/21845 等）/pdf_list/color_image：v4 回显可读回
    # → 有则原样带回，无则省略键（此前写死 [] 会洗掉现卡视频/PDF/营销色）。
    complex_echo = stored_item.get("complex_attributes")
    if isinstance(complex_echo, list) and complex_echo:
        item["complex_attributes"] = complex_echo
    pdf_echo = stored_item.get("pdf_list")
    if isinstance(pdf_echo, list) and pdf_echo:
        item["pdf_list"] = pdf_echo
    color_echo = stored_item.get("color_image")
    if isinstance(color_echo, str) and color_echo.strip():
        item["color_image"] = color_echo.strip()
    elif isinstance(color_echo, list) and color_echo:
        # info/list 形状是数组 → 取首个（import 契约 color_image 是单字符串）
        first = str(color_echo[0] or "").strip()
        if first:
            item["color_image"] = first

    card_attrs: Dict[int, Dict[str, Any]] = {}
    for a in stored_item.get("attributes") or []:
        if isinstance(a, dict):
            try:
                aid = int(a.get("id") or 0)
            except (ValueError, TypeError):
                continue
            if aid > 0:
                card_attrs[aid] = a

    return {
        "item": item,
        "card_attrs": card_attrs,
        "img_urls": img_urls,
        "primary_image": primary_image,
    }, ""


def _echo_attributes(
    card_attrs: Dict[int, Dict[str, Any]],
    skip_ids: Any = frozenset(),
) -> List[Dict[str, Any]]:
    """现卡特征全量回显（防洗卡核心）：跳过 Ozon 自动设置的 23536/海关码与 skip_ids。"""
    from utils.attribute_utils import is_customs_attr

    echoed: List[Dict[str, Any]] = []
    for aid, a in card_attrs.items():
        if aid in skip_ids:
            continue
        if aid == 23536 or is_customs_attr(aid):
            continue
        values = [
            {
                "dictionary_value_id": int(v.get("dictionary_value_id") or 0) if isinstance(v, dict) else 0,
                "value": str(v.get("value") or "") if isinstance(v, dict) else str(v or ""),
            }
            for v in (a.get("values") or []) if isinstance(v, dict)
        ]
        if not values:
            continue
        echoed.append({"complex_id": 0, "id": aid, "values": values})
    return echoed


def build_enrich_update_body(
    product_id: Any,
    stored_item: Dict[str, Any],
    improve_attrs: List[Dict[str, Any]],
    draft_attrs: Dict[str, Any],
    schema_by_id: Dict[int, Dict[str, Any]],
    price: Any = None,
    old_price: Any = None,
    currency_code: str = "CNY",
    vat: Any = None,
    images360: Optional[List[Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """构造评分驱动的 /v3/product/import UPDATE body（全量回显 + 新填属性）。

    Args:
        product_id: Ozon product_id。
        stored_item: /v4/product/info/attributes 回显的单卡 item（含 attributes/
            depth/width/height/weight/name/offer_id/images 等）。
        improve_attrs: extract_improve_attrs 产物。
        draft_attrs: draft.attributes 中文证据（清扫脚本无 draft 传 {}）。
        schema_by_id: {attr_id: schema}（无 schema 传 {}——可填裁决保守跳过）。
        price/old_price: 现价（回显用；缺价 → 不动作）。清扫脚本从
            /v4/product/info/prices 取。
        vat/images360: /v3/product/info/list 回显（v0.81.1 新参；可得则带回真实
            值，缺省省略键——绝不写死 vat="0"/空数组，见 _card_echo_base 留证）。

    Returns:
        (body, audit_partial)。body=None 表示不该动作（audit_partial.reason 说明）。
        audit_partial: {filled, skipped, media_gap, reason?}——filled 含 4191/11254。
    """
    audit: Dict[str, Any] = {"filled": [], "skipped": [], "media_gap": []}
    parts, reason = _card_echo_base(
        product_id, stored_item,
        price=price, old_price=old_price, currency_code=currency_code,
        vat=vat, images360=images360,
    )
    if parts is None:
        audit["reason"] = reason
        return None, audit

    card_attrs = parts["card_attrs"]
    img_urls = parts["img_urls"]
    improves_by_id = {int(i["id"]): i for i in (improve_attrs or []) if isinstance(i, dict) and i.get("id")}

    def _card_value(aid: int) -> str:
        vals = (card_attrs.get(aid) or {}).get("values") or []
        if vals and isinstance(vals[0], dict):
            return str(vals[0].get("value") or "").strip()
        return ""

    new_attrs: List[Dict[str, Any]] = []

    # ① 4191 简介：卡片缺失 或 Ozon 点名要求补 → 用 build_annotation 兜底
    need_annotation = ANNOTATION_ATTR_ID in improves_by_id or not _card_value(ANNOTATION_ATTR_ID)
    if need_annotation:
        annotation = build_annotation(str(stored_item.get("name") or ""), draft_attrs)
        if annotation:
            new_attrs.append(_free_text_attr(ANNOTATION_ATTR_ID, annotation))
            audit["filled"].append(ANNOTATION_ATTR_ID)

    # ② 11254 Rich JSON：卡片缺失 或 点名要求补，且有 ≥2 图
    need_rich = RICH_CONTENT_ATTR_ID in improves_by_id or not _card_value(RICH_CONTENT_ATTR_ID)
    if need_rich:
        rich = build_rich_json(img_urls, str(stored_item.get("name") or ""))
        if rich:
            new_attrs.append(_free_text_attr(RICH_CONTENT_ATTR_ID, rich))
            audit["filled"].append(RICH_CONTENT_ATTR_ID)

    # ③ 其余可填属性（保守裁决）
    fills = fillable_improves(improve_attrs, draft_attrs, schema_by_id)
    for aid, value in fills.items():
        if _card_value(aid):
            audit["skipped"].append(aid)  # 卡上已有值不覆盖（Ozon 已过审）
            continue
        new_attrs.append(_free_text_attr(aid, value))
        audit["filled"].append(aid)

    # ④ 审计：媒体缺口如实列出（产不了）
    audit["media_gap"] = sorted(aid for aid in improves_by_id if aid in MEDIA_ATTR_IDS)
    audit["skipped"] = sorted(
        {aid for aid in improves_by_id if aid not in audit["filled"] and aid not in MEDIA_ATTR_IDS}
    )

    if not new_attrs:
        audit.setdefault("reason", "nothing_to_fill")
        return None, audit

    # 全量回显（import 是全量替换语义——现卡特征原样带回，防洗卡）：
    # 新旧 4191/11254 由新值顶替的跳过；23536/海关码 Ozon 自动设置不回发。
    replaced = {aid for aid in (ANNOTATION_ATTR_ID, RICH_CONTENT_ATTR_ID)
                if any(n["id"] == aid for n in new_attrs)}
    item = parts["item"]
    item["attributes"] = _echo_attributes(card_attrs, replaced) + new_attrs

    return {"items": [item]}, audit


def build_price_update_body(
    product_id: Any,
    stored_item: Dict[str, Any],
    *,
    price: Any,
    old_price: Any = None,
    currency_code: str = "CNY",
    vat: Any = None,
    images360: Optional[List[Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """价格单字段修复场景的 /v3/product/import 全量回显 UPDATE（防洗卡家族构造器）。

    card_audit 域 D 不变量（PLAN-card-audit-sweep-v1）自动修唯一出口：old_price
    缺失 / 低于现价 / 差价不足时，_card_echo_base 骨架内 clamp_old_price
    （enforce_old_price_rule 唯一规则出口）产出合规 old_price（≥ price×1.2 且
    差价 <400 时 ≥20），attributes 现卡特征全量回显（与 build_image_update_body
    同源口径，不动卡上内容）。**新自动修出口必须走本家族，绝不裸拼 import POST。**

    Args:
        price: 现价（/v5 嵌套 price 对象取出的字符串）。
        old_price: 现划线价（None/畸形 → 规则下限兜底）。
        其余参数语义见 _card_echo_base。

    Returns:
        (body, reason)。body=None 表示保守放弃（reason 同 _card_echo_base），
        调用方落 finding 不裸发。
    """
    parts, reason = _card_echo_base(
        product_id, stored_item,
        price=price, old_price=old_price, currency_code=currency_code,
        vat=vat, images360=images360,
    )
    if parts is None:
        return None, reason
    item = parts["item"]
    # 现卡特征原样带回（含 4191/11254——价格修复不动卡上内容，全量回显保全）
    item["attributes"] = _echo_attributes(parts["card_attrs"])
    return {"items": [item]}, ""


def build_image_update_body(
    product_id: Any,
    stored_item: Dict[str, Any],
    images: List[str],
    *,
    price: Any,
    old_price: Any = None,
    currency_code: str = "CNY",
    vat: Any = None,
    images360: Optional[List[Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """在线改图场景的 /v3/product/import 全量回显 UPDATE（防洗卡第四出口）。

    历史事故口径（A6）：import 是全量替换语义，只发 product_id/offer_id/images
    三键会把整卡特征/价格/尺寸洗空——本构造器与 build_enrich_update_body 同源
    _card_echo_base（三消费方同源纪律：复检闭环/清扫脚本/在线改图）。

    Args:
        images: 已过滤的存活新图 URL 列表（顺序即卡上顺序）。
        其余参数语义见 _card_echo_base。

    Returns:
        (body, reason)。body=None 表示保守放弃（reason ∈ no_card_echo /
        bad_product_id / no_price / zero_dims / no_images），调用方应拒绝改图
        （fail-closed），绝不裸发。
    """
    parts, reason = _card_echo_base(
        product_id, stored_item,
        price=price, old_price=old_price, currency_code=currency_code,
        images_override=list(images or []),
        vat=vat, images360=images360,
    )
    if parts is None:
        return None, reason
    item = parts["item"]
    # 现卡特征原样带回（含 4191/11254——改图不动卡上内容，全量回显保全）
    item["attributes"] = _echo_attributes(parts["card_attrs"])
    return {"items": [item]}, ""


def _free_text_attr(attr_id: int, value: str) -> Dict[str, Any]:
    return {
        "complex_id": 0,
        "id": int(attr_id),
        "values": [{"dictionary_value_id": 0, "value": value}],
    }
