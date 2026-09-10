"""商品图 URL 守卫——E1 兜底/生图参考共用的白名单过滤（fix/image-ref-pollution）。

线上事故：多单「产品A的 Ozon 卡片出现产品B的图」。根因链（skill 侧关键词搜索
兜底把别家 1688 商品的 310x310 缩略图串进货源图字段）在 skill 侧修复
（fix/image-ref-pollution），worker 侧本模块是最后一道出口防线：

- **白名单**：E1 兜底转存/生图参考只放行 1688 alicdn 图床的原尺寸图。旧黑名单
  （ir.ozone.ru/ozonstatic/ir-20.）漏域名形态（Ozon CDN 多变），白名单一次堵死
  竞品 Ozon 图等一切非货源域。
- **拒缩略后缀**：`_310x310`/`_460x460`/`.webp` 是 1688 搜索兜底串图的特征
  形态，且缩略图当主图/生图参考质量不合格。

跨平台货源扩展 v1（feat/cross-platform-sourcing 批1）：白名单扩入淘宝/天猫
（taobaocdn.com；天猫主图实际多挂 img.alicdn.com，旧行为已放行）与拼多多
（pddpic.com / yangkeduo.com / pinduoduo.com）图床；缩略恒拒语义不变——新平台
缩略后缀（`_60x60` 两位数尺寸段、`.jpg_400x400` 风格）同样拒绝。
"""
from __future__ import annotations

import re
from typing import Iterable, List

# 缩略/转换后缀：搜索兜底串图特征（cbu01.alicdn.com/..._!!sellerId-0-cib.310x310.jpg
# 或 ..._460x460q100.jpg —— 尺寸段以 . 或 _ 与主体分隔）
# 批1 起尺寸段放宽到两位（\d{2,4}）：淘宝/pdd 缩略后缀 `_60x60`/`.jpg_50x50.jpg`
# 形态旧 \d{3,4} 拦不住；只收紧不放宽，既有拒绝面零回归。
_THUMBNAIL_PATTERN = re.compile(r"[._]\d{2,4}x\d{2,4}")


def is_product_image_candidate(url: object) -> bool:
    """判定 URL 是否为「合格商品原图」（E1 转存/生图参考白名单）。

    True 仅当：http(s) URL + 货源图床（1688 alicdn / 淘宝系 / pdd 系，见下）+
    非缩略/转换后缀。任何解析异常输入一律 False（宁缺毋滥）。
    """
    if not isinstance(url, str):
        return False
    lowered = url.strip().lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    # 白名单：1688 alicdn 图床（1688.com 覆盖 img.1688.com 等自有域）
    # + 批1 新平台图床：淘宝系 taobaocdn / 拼多多 pddpic·yangkeduo·pinduoduo
    if not any(dom in lowered for dom in (
        "alicdn.com", "1688.com",
        "taobaocdn.com",
        "pddpic.com", "yangkeduo.com", "pinduoduo.com",
    )):
        return False
    # 拒缩略/转换后缀
    if lowered.endswith(".webp") or ".jpg_.webp" in lowered:
        return False
    return not _THUMBNAIL_PATTERN.search(lowered)


def filter_product_images(urls: Iterable[object]) -> List[str]:
    """批量过滤，保持原顺序去空。生图参考/E1 转存前统一调用。"""
    out: List[str] = []
    for u in urls:
        if isinstance(u, str) and u.strip() and is_product_image_candidate(u):
            out.append(u.strip())
    return out
