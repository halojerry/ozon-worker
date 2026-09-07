"""商品图 URL 守卫——E1 兜底/生图参考共用的白名单过滤（fix/image-ref-pollution）。

线上事故：多单「产品A的 Ozon 卡片出现产品B的图」。根因链（skill 侧关键词搜索
兜底把别家 1688 商品的 310x310 缩略图串进货源图字段）在 skill 侧修复
（fix/image-ref-pollution），worker 侧本模块是最后一道出口防线：

**参考图两条线**（R2 修正）：
- 1688 直上（graph/discover）：生图参考仅 1688 alicdn 货源原图。
- 跟卖（follow_sell=true）：竞品 Ozon 主图优先参考（CDP 抓取原尺寸，重绘同款
  防侵权检测）> 1688 货源图——`allow_competitor=True` 放行 ozone 域原尺寸图。
- 缩略/转换后缀（`_310x310`/`_460x460`/`.webp`，搜索兜底串图毒源特征）在
  **所有场景恒拒**，且缩略图当主图/生图参考质量不合格。

上传语义（默认参数）恒为「仅 1688 alicdn 原图」：E1 转存、变体原图兜底等
直接进上传 payload 的路径，竞品图永不放行（v0.40.1 防线不动）。
"""
from __future__ import annotations

import re
from typing import Iterable, List

# 缩略/转换后缀：搜索兜底串图特征（cbu01.alicdn.com/..._!!sellerId-0-cib.310x310.jpg
# 或 ..._460x460q100.jpg —— 尺寸段以 . 或 _ 与主体分隔）
_THUMBNAIL_PATTERN = re.compile(r"[._]\d{3,4}x\d{3,4}")


def _is_ozon_domain(lowered: str) -> bool:
    """Ozon 竞品图域（含历史形态 ozonstatic / .cn 镜像），不再枚举黑名单。"""
    return "ozone.ru" in lowered or "ozonstatic" in lowered


def is_product_image_candidate(url: object, allow_competitor: bool = False) -> bool:
    """判定 URL 是否为「合格商品图」。

    allow_competitor=False（默认，上传语义）：仅 1688 alicdn 域原尺寸图。
    allow_competitor=True（跟卖生图参考语义）：额外放行 Ozon 竞品域原尺寸图
    （CDP 抓取的竞品主图——参考图两条线设计）。
    缩略/转换后缀在两种语义下恒拒（串图毒源）。
    任何解析异常输入一律 False（宁缺毋滥）。
    """
    if not isinstance(url, str):
        return False
    lowered = url.strip().lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    # 域名白名单：1688 alicdn 图床；跟卖参考场景额外放行 Ozon 竞品域
    domain_ok = "alicdn.com" in lowered or "1688.com" in lowered
    if not domain_ok and not (allow_competitor and _is_ozon_domain(lowered)):
        return False
    # 拒缩略/转换后缀（全场景恒拒）
    if lowered.endswith(".webp") or ".jpg_.webp" in lowered:
        return False
    if _THUMBNAIL_PATTERN.search(lowered):
        return False
    return True


def filter_product_images(urls: Iterable[object],
                          allow_competitor: bool = False) -> List[str]:
    """批量过滤，保持原顺序去空。生图参考/E1 转存前统一调用。"""
    out: List[str] = []
    for u in urls:
        if (isinstance(u, str) and u.strip()
                and is_product_image_candidate(u, allow_competitor)):
            out.append(u.strip())
    return out
