"""上架收尾卡片图断言（fix/upload-image-assertion-v1，2026-09-18 用户②）。

背景（商品 6381680593 / 任务 bf71442e 实证）：首传载荷已装载 AI 生成图，重传/修复
链曾用 draft 原图覆盖（已修），但此前没有任何收尾校验拦这类「错图静默上线」——
本模块在 ozon_status all_approved 出口校验「卡片图 = 载荷图」：

- 数量：卡片可见图数 < 载荷图数 → mismatch；
- 来源：载荷图全为 AI 生成图（utils/image_source 判 ai：file/images/ 与
  mxou-b64/，批H 接线）→ 抽卡首图下载，宽高比须 ≈ 0.75（3:4，生成图规格
  896×1200；1688 原图通常 1:1）→ 不符 → mismatch；
- 载荷为 salvage/镜像/原图兜底（非 ai）→ 仅数量校验（尺寸不区分来源语义）；
- 载荷无图（跟卖 UPDATE/编辑流）→ skipped；
- 卡图尚未异步填充 / 下载失败 → unverified（warning 不拦，诚实降级——校验
  不可用 ≠ 校验失败，Ozon 图片填充是异步的，硬拦会误杀正常单）。

依赖零新增：下载复用 utils.image_url_processor._download_image（含 UA/Referer
派发）；JPEG/PNG 头解析手写（worker 无 Pillow）；卡图 URL 允许 Ozon CDN 域 +
我方 COS 域（批C fix/card-assert-cos-v1：import 刚完成时 info/list 先返回我方
COS 源 URL，是 Ozon 转存前的合法返回，只认 CDN 会让断言恒 unverified）。
"""
from __future__ import annotations

import logging
import struct
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

# ✅ v0.78 批H (fix/attr4194-regen-v1): AI 判定唯一事实源（批F TODO 收口）——
# 内联 ``/file/images/`` 字面 marker 对 mxou-b64/ 兜底图全盲，改走 image_source。
from utils import image_source

logger = logging.getLogger(__name__)

# 3:4 生成图宽高比容差（896×1200=0.7467；Ozon 侧可能轻微重压缩）
_AI_ASPECT_MIN, _AI_ASPECT_MAX = 0.70, 0.80
# 卡图来源白名单：Ozon CDN（/v3/product/info/list 返回 ir-*.ozone.ru / *.ozonstatic.cn 等）
# + 我方 COS 域（批C fix/card-assert-cos-v1，2026-09-19 取证 I4）：import 刚完成时
# info/list 先返回我方 COS 源 URL——这是 Ozon 转存 CDN 前的合法返回，不是异常；
# 此前仅 Ozon CDN 三域 → COS 卡图拒下载 → 断言恒 unverified 静默放行。
# .myqcloud.com 同时覆盖区域桶（cos.ap-guangzhou）与全域加速（cos.accelerate）两种形态。
_ALLOWED_CARD_HOST_SUFFIXES = (
    ".ozone.ru",
    ".ozonstatic.cn",
    ".ozonstatic.com",
    ".myqcloud.com",
)

VERIFY_OK = "ok"
VERIFY_MISMATCH = "mismatch"
VERIFY_UNVERIFIED = "unverified"
VERIFY_SKIPPED = "skipped"


def is_allowed_card_url(url: str) -> bool:
    """卡图 URL 校验：https + Ozon CDN 域后缀白名单（防任意 URL 下载）。"""
    try:
        p = urlparse(str(url))
    except Exception:
        return False
    if p.scheme != "https" or not p.hostname:
        return False
    host = p.hostname.lower()
    return any(host.endswith(s) for s in _ALLOWED_CARD_HOST_SUFFIXES)


def image_size_from_bytes(data: bytes) -> Optional[Tuple[int, int]]:
    """解析 JPEG(SOF0/1/2/…) / PNG 头取 (width, height)；未知格式返回 None。"""
    if not data or len(data) < 24:
        return None
    # PNG: 89 50 4E 47 0D 0A 1A 0A + IHDR
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    # JPEG: FF D8 … 扫 SOF marker
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker == 0xFF or marker in (0xC4, 0xC8, 0xCC):  # 填充/DHT/JPG/DAC → 按段跳过
                if marker == 0xFF:
                    i += 2
                    continue
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + seg
                continue
            if 0xC0 <= marker <= 0xCF:  # SOF0..SOF15（含 progressive C2）
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]  # APPn/SOS 等按长度跳过
            if seg < 2:
                return None
            i += 2 + seg
        return None
    return None


def _default_fetch_size(url: str) -> Optional[Tuple[int, int]]:
    """下载卡图取尺寸（复用 image_url_processor 下载器；域白名单前置）。"""
    if not is_allowed_card_url(url):
        logger.warning("卡片图 URL 域不在 Ozon CDN 白名单，拒绝下载: %s", str(url)[:80])
        return None
    try:
        from utils.image_url_processor import _download_image

        data = _download_image(str(url), timeout=20)
        if not data:
            return None
        return image_size_from_bytes(data)
    except Exception as exc:
        logger.warning("卡片首图下载失败（尺寸校验降级 unverified）: %s %s", url[:80], exc)
        return None


def is_all_ai_images(payload_images: List[str]) -> bool:
    """载荷图是否全为本方 AI 生成图（utils/image_source 唯一入口逐张判定）。

    ✅ v0.78 批H (fix/attr4194-regen-v1)：内联 ``/file/images/`` marker 换
    ``utils.image_source.has_generated_images``（批A 唯一事实源，批F TODO 收口）——
    行为对 file/images/ 完全兼容，对 mxou-b64/ 兜底图从盲变明（现计 AI 产物，
    同样受 3:4 尺寸断言约束）；salvage/镜像/外链/未知 key 均非 AI。
    """
    urls = [str(u) for u in (payload_images or []) if str(u).strip()]
    return bool(urls) and all(image_source.has_generated_images((u,)) for u in urls)


def verify_card_images(
    payload_images: List[str],
    card_image_urls: List[str],
    fetch_size: Callable[[str], Optional[Tuple[int, int]]] = _default_fetch_size,
) -> Tuple[str, str]:
    """校验卡片图与上传载荷一致 → (status, detail)。

    status: ok / mismatch / unverified / skipped
    """
    payload = [str(u) for u in (payload_images or []) if str(u).strip()]
    if not payload:
        return VERIFY_SKIPPED, "payload 无图（跟卖/编辑流），跳过校验"
    cards = [str(u) for u in (card_image_urls or []) if str(u).strip()]
    if not cards:
        return VERIFY_UNVERIFIED, f"卡片图尚未填充（异步延迟），无法校验（载荷 {len(payload)} 张）"
    if len(cards) < len(payload):
        return VERIFY_MISMATCH, (
            f"卡片图数量 {len(cards)} < 载荷图数量 {len(payload)}（疑似被替换/部分丢失）"
        )
    if is_all_ai_images(payload):
        size = fetch_size(cards[0])
        if size is None:
            return VERIFY_UNVERIFIED, "卡片首图下载/解析失败，尺寸校验降级（AI 载荷）"
        w, h = size
        aspect = w / h if h else 0.0
        if not (_AI_ASPECT_MIN <= aspect <= _AI_ASPECT_MAX):
            return VERIFY_MISMATCH, (
                f"载荷为 AI 生成图（{len(payload)} 张 3:4）但卡片首图 {w}x{h} "
                f"(ratio={aspect:.2f}) 非生成图规格——卡片被原图覆盖"
            )
        return VERIFY_OK, f"卡片图与 AI 载荷一致（首图 {w}x{h} 3:4，卡 {len(cards)} 张）"
    return VERIFY_OK, f"卡片图数量一致（{len(cards)} 张，载荷非 AI 路径仅数量校验）"
