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

⚠️ 2026-09-16 原图上卡事故（fix/image-ref-cos-whitelist-v1 批1）：事故链
draft_image_mirror 把 draft.images 回写为本方 COS URL → 本白名单不认 COS 域 →
生图参考清零 → 生图被跳过 → assemble 用原图补位上卡（用户看到未美化原图）。
拍板口径：**本方 COS 托管图 = 货源原图 1:1 副本，参考语义等价**，白名单放行；
但只豁免图床域白名单，缩略/`.webp` 恒拒对 COS 域照常生效（镜像 key 带
`_310x310` 之类后缀照样拒）。同批 `is_cos_url` 唯一实现自 cos_uploader.py
迁入本模块（cos_uploader 侧模块级 re-export，既有消费方 import 路径零改动）。
"""
from __future__ import annotations

import re
from typing import Iterable, List

# 缩略/转换后缀：搜索兜底串图特征（cbu01.alicdn.com/..._!!sellerId-0-cib.310x310.jpg
# 或 ..._460x460q100.jpg —— 尺寸段以 . 或 _ 与主体分隔）
# 批1 起尺寸段放宽到两位（\d{2,4}）：淘宝/pdd 缩略后缀 `_60x60`/`.jpg_50x50.jpg`
# 形态旧 \d{3,4} 拦不住；只收紧不放宽，既有拒绝面零回归。
_THUMBNAIL_PATTERN = re.compile(r"[._]\d{2,4}x\d{2,4}")

# 货源图床白名单：1688 alicdn（1688.com 覆盖 img.1688.com 等自有域）
# + 跨平台 v1 新平台图床：淘宝系 taobaocdn / 拼多多 pddpic·yangkeduo·pinduoduo
_SOURCE_IMAGE_HOSTS = (
    "alicdn.com", "1688.com",
    "taobaocdn.com",
    "pddpic.com", "yangkeduo.com", "pinduoduo.com",
)


def is_cos_url(url: object) -> bool:
    """判断 URL 是否已托管在本方 COS（幂等判定的唯一共享实现，自 cos_uploader 迁入）。

    ⚠️ 既有契约：非 str / 空串 → True（「非外链即视为本方托管」）——这与
    is_product_image_candidate 的宁缺毋滥方向相反，故后者只在已确认 http(s)
    非空字符串后才调用本函数（顺序红线，见其注释）。

    v0.69 declined IMAGE_ERROR 根因修复的共享件：submit 镜像闸（draft_service）、
    validate 全外链硬拦（ozon_validate_node）、retry pictures/import 取图
    （validation_retry_loop）、镜像回写（draft_image_mirror）同源判定，
    禁止各自内联（防漂移）。消费方统一经 `utils.cos_uploader` re-export
    import（路径不变）。覆盖 区域域名 cos.{region}.myqcloud.com 与全球加速
    cos.accelerate.myqcloud.com。
    """
    if not isinstance(url, str):
        return True
    lowered = url.strip().lower()
    if not lowered:
        return True
    return ".myqcloud.com" in lowered or "cos." in lowered


def is_product_image_candidate(url: object) -> bool:
    """判定 URL 是否为「合格商品原图」（E1 转存/生图参考白名单）。

    True 仅当：http(s) URL +（本方 COS 托管图 OR 货源图床白名单）+
    非缩略/转换后缀。任何解析异常输入一律 False（宁缺毋滥）。
    """
    if not isinstance(url, str):
        return False
    lowered = url.strip().lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    # 顺序红线：is_cos_url 对非 str/空串返回 True 的既有契约绝不外溢——
    # 走到这里已确认是 http(s) 非空字符串，COS 放行分支只作用于该前提。
    # 放行面 = 本方 COS 托管图（镜像=货源原图 1:1 副本，参考语义等价）
    # OR 货源图床白名单。
    if not (is_cos_url(lowered) or any(dom in lowered for dom in _SOURCE_IMAGE_HOSTS)):
        return False
    # 拒缩略/转换后缀（COS 域不豁免：镜像 key 带 _310x310 之类后缀照样拒）
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
