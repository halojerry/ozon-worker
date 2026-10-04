"""clone_card_builder — 跟卖克隆回退构造器（follow_clone 模式，v0.85）。

背景（PLAN-follow-clone-v1 B0 探针实录 2026-10-03）：
- import-by-sku 是「能复制商品卡直接复制」的官方通道；不可复制（unmatched_sku_list）
  时回退**逐字克隆**——CDP 读竞品卡（skill A7 webCharacteristics 链）→ 信封
  extensions.clone_card → 本模块构造 /v3/product/import CREATE item。
- B0-C 实锤：Ozon 自家 CDN 图 URL 直传 import 被接受并挂图（ir-20.ozone.ru
  原尺寸 URL → 新卡 images_n=1）——零下载零 COS。
- B0-B 实锤：跨店逐字克隆不并卡（三轮 INDEPENDENT）——克隆必为独立新卡。

形状契约（B0-D 实录，clone_card 信封键）：
    {
      "product_id": "竞品 product_id（str）",
      "name": "竞品俄语标题",
      "dc": "description_category_id（str 数字）",
      "tp": "type_id（str 数字）",
      "attributes": [{"id": int, "values": [{"dictionary_value_id": int,
                                             "value": str}, ...]}, ...],
      "images": ["https://ir-N.ozone.ru/s3/multimedia-.../xxx.jpg", ...],
      "weight_g": int, "depth_mm": int, "width_mm": int, "height_mm": int,
    }
- v4 回显属性主键是 ``id``（非 attribute_id）、dictionary_value_id snake_case
  （card_echo 注释旧 camelCase 口径按 B0-D 修正）；import 请求主键是
  ``attribute_id``（探针实收）。
- 竞品卡 attributes 零翻译零改写逐字回显（follow_clone 零 LLM 本意）——本模块
  只做形状映射与防御过滤，绝不动值。

图片口径：复用 ``image_url_guard.filter_reference_images(allow_competitor=True)``
——Ozon CDN 原尺寸图放行、缩略/.webp 恒拒（批I 质量红线同款）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.image_url_guard import filter_competitor_cdn_images

logger = logging.getLogger(__name__)

# 媒体类属性我们产不了（content_enrich.MEDIA_ATTR_IDS 同款），克隆时跳过防 import 拒收
MEDIA_ATTR_IDS = frozenset({21841, 21845, 4195})
# Ozon import images 上限（单卡 15 张）
CLONE_MAX_IMAGES = 15


def normalize_clone_attributes(attrs: Any) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """v4 回显属性（id 主键）→ import 属性（attribute_id 主键）。

    - 无 id / 空值 / 媒体类属性跳过（计数留痕）；
    - **属性对象 = 生产 prepare 同形状（2026-10-04 留观卡 6515871405 六错→清零
      实测）**：``{"complex_id": 0, "id": N, "values": [...]}``——用 ``attribute_id``
      键且无 complex_id 的形状，optional 属性能绑、**required 属性被校验路径
      丢弃 → error_attribute_values_empty**（10096/4295/9163/8292 四个 required
      实锤；换生产形状后四属性全绑、六错全清）。不发明平行形状；
    - **值对象两键恒发**：Ozon 必填校验按 ``value`` 判空（同批实锤）——
      Ozon 自家回显两键原样照抄；
    - 绝不改写值内容（零 LLM 红线）。
    """
    out: List[Dict[str, Any]] = []
    skipped = {"media": 0, "no_id": 0, "no_values": 0}
    if not isinstance(attrs, list):
        return out, skipped
    for a in attrs:
        if not isinstance(a, dict):
            skipped["no_id"] += 1
            continue
        aid = a.get("id") or a.get("attribute_id")
        if aid is None:
            skipped["no_id"] += 1
            continue
        if int(aid) in MEDIA_ATTR_IDS:
            skipped["media"] += 1
            continue
        values: List[Dict[str, Any]] = []
        for v in a.get("values") or []:
            if not isinstance(v, dict):
                continue
            did = v.get("dictionary_value_id") or 0
            val = v.get("value")
            if not did and val in (None, ""):
                continue
            values.append({
                "dictionary_value_id": did,
                **({"value": str(val)} if val not in (None, "") else {}),
            })
        if not values:
            skipped["no_values"] += 1
            continue
        out.append({"complex_id": int(a.get("complex_id") or 0),
                    "id": int(aid), "values": values})
    return out, skipped


def extract_clone_images(images: Any) -> List[str]:
    """竞品 CDN 图过滤（**仅 Ozon 自家 CDN 原尺寸**——filter_competitor_cdn_images，
    货源 alicdn/外站图混入即拒）+ 去重 + 上限 15。"""
    if not isinstance(images, list):
        return []
    seen: set = set()
    out: List[str] = []
    for u in filter_competitor_cdn_images(images):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:CLONE_MAX_IMAGES]


def validate_clone_card(cc: Any) -> Tuple[bool, str]:
    """克隆回退载荷最低门槛：name + 数字 dc/tp 必需；图可空（UPDATE 路径不消费本模块）。"""
    if not isinstance(cc, dict):
        return False, "clone_card 非对象"
    if not str(cc.get("name") or "").strip():
        return False, "clone_card.name 为空"
    dc = str(cc.get("dc") or "").strip()
    tp = str(cc.get("tp") or "").strip()
    if not (dc.isdigit() and int(dc) > 0):
        return False, f"clone_card.dc 非法（{dc!r}）"
    if not (tp.isdigit() and int(tp) > 0):
        return False, f"clone_card.tp 非法（{tp!r}）"
    return True, ""


def build_clone_import_item(
    clone_card: Dict[str, Any],
    *,
    offer_id: str,
    price: Any,
    old_price: Any = None,
    currency_code: str = "CNY",
    vat: str = "0",
) -> Optional[Dict[str, Any]]:
    """克隆回退 → /v3/product/import CREATE item（探针 probe_clone_card 产品化）。

    price 必填（pricing_node 产物）；重量尺寸有则透传（CDP 毫米/克口径 → import
    weight/depth/width/height 字段，B0 实测原值直收）。
    """
    ok, reason = validate_clone_card(clone_card)
    if not ok:
        logger.warning("⛔ 克隆回退载荷不完整: %s", reason)
        return None
    attrs, skipped = normalize_clone_attributes(clone_card.get("attributes"))
    images = extract_clone_images(clone_card.get("images"))
    if not attrs:
        logger.warning("⛔ 克隆回退属性为空（skipped=%s）——零属性 CREATE 必拒，拒绝构造",
                       skipped)
        return None
    item: Dict[str, Any] = {
        "name": str(clone_card["name"]).strip(),
        "offer_id": str(offer_id),
        "price": str(price),
        "vat": vat,
        "currency_code": currency_code,
        "description_category_id": int(clone_card["dc"]),
        "type_id": str(clone_card["tp"]),
        "attributes": attrs,
        "images": images,
    }
    if old_price not in (None, "", 0, "0"):
        item["old_price"] = str(old_price)
    weight = clone_card.get("weight_g")
    if isinstance(weight, (int, float)) and weight > 0:
        # 单位显式声明对齐生产管线 prepare 形状（weight g / dims mm 恒定）
        item["weight"] = int(weight)
        item["weight_unit"] = "g"
    dims = []
    for src, dst in (("depth_mm", "depth"), ("width_mm", "width"), ("height_mm", "height")):
        v = clone_card.get(src)
        if isinstance(v, (int, float)) and v > 0:
            item[dst] = int(v)
            dims.append(dst)
    if dims:
        item["dimension_unit"] = "mm"
    logger.info(
        "🧬 克隆 import item 构造: attrs=%d（skipped=%s） images=%d weight=%s",
        len(attrs), skipped, len(images), item.get("weight", "-"),
    )
    return item
