#!/usr/bin/env python3
"""
图片预处理器 — 1688 以图搜款图片格式转换

复用自 1688-product-find v1.7.0 _image.py
提供 JPEG 格式转换、透明通道处理、超尺寸缩放。
"""
from __future__ import annotations

import base64
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from scripts.lib.utils import safe_unlink

logger = logging.getLogger(__name__)

_JPEG_EXTENSIONS = {".jpg", ".jpeg"}
IMAGE_MAX_SIZE_MB = 5
# 不强制缩放——保持原始分辨率更利于图像识别
IMAGE_MAX_DIMENSION = 4096


def downscale_for_upload(data: bytes, max_dim: int = 1024, quality: int = 80) -> bytes:
    """上传前压缩（批A Q6，fix/skill-silent-cdp-v1，根治 aibuy upload HTTP 413）。

    最长边 > ``max_dim`` 才等比缩小；无论是否缩放统一转 JPEG（q{quality}，
    动图取首帧；透明通道白底合成对齐 _convert_to_jpeg 先例）。PIL 任何异常
    → **原样返回 bytes，绝不 raise**——调用方兜底语义（原始字节直传）不变。

    ⚠️ 只服务 aibuy upload 的 base64 通道（原图 base64 膨胀 1.33× 无上限曾
    413）；不得改变既有 AK 通道行为（AK 走 preprocess_image 文件路径，不经这里）。
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        try:
            # GIF/WebP 动图：PIL 默认停在首帧，save 即首帧，无需特判
            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1])
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality)
            compressed = out.getvalue()
        finally:
            try:
                img.close()
            except Exception:
                pass
        if not compressed:
            return data
        logger.debug("downscale_for_upload: %d → %d bytes (JPEG q%d, max_dim=%d)",
                     len(data), len(compressed), quality, max_dim)
        return compressed
    except Exception as exc:
        logger.debug("downscale_for_upload 压缩失败（原样返回）: %s", exc)
        return data


def preprocess_image(image_path: str) -> dict[str, Any]:
    """
    预处理图片，返回 {path, type, converted} 或 {url, type}。
    
    Args:
        image_path: 本地文件路径或 HTTP URL
    
    Returns:
        dict with keys: type ("local"|"url"), path/url, converted (bool)
    
    Raises:
        FileNotFoundError: 本地文件不存在
        ValueError: 文件过大
    """
    if image_path.startswith(("http://", "https://")):
        return {"url": image_path, "type": "url"}

    path = os.path.abspath(image_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"图片不存在: {path}")

    file_size_mb = os.path.getsize(path) / (1024 * 1024)
    if file_size_mb > IMAGE_MAX_SIZE_MB:
        raise ValueError(f"图片过大 ({file_size_mb:.1f}MB), 最大 {IMAGE_MAX_SIZE_MB}MB")

    ext = Path(path).suffix.lower()
    converted = False
    needs_resize = False

    try:
        from PIL import Image
        img = Image.open(path)
        max_dim = IMAGE_MAX_DIMENSION
        if img.width > max_dim or img.height > max_dim:
            needs_resize = True
        img.close()
    except Exception:
        pass

    if needs_resize or ext not in _JPEG_EXTENSIONS:
        path = _convert_to_jpeg(path, resize_to=(IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION) if needs_resize else None)
        converted = True
        logger.info(f"图片预处理: resize={needs_resize}, converted={converted}")

    return {
        "path": path,
        "type": "local",
        "size_bytes": os.path.getsize(path),
        "format": os.path.splitext(path)[1],
        "converted": converted,
    }


def _convert_to_jpeg(src_path: str, quality: int = 90, resize_to: tuple | None = None) -> str:
    """
    图片转 JPEG，可选等比缩放。返回输出文件路径。
    """
    from PIL import Image

    img = Image.open(src_path)

    if resize_to:
        max_w, max_h = resize_to
        if img.width > max_w or img.height > max_h:
            img.thumbnail((max_w, max_h), Image.LANCZOS)

    # 透明通道 → 白底 RGB
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    except OSError:
        fallback_dir = os.path.dirname(os.path.abspath(src_path))
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=fallback_dir)

    try:
        img.save(tmp, format="JPEG", quality=quality)
        tmp.close()
    except Exception:
        tmp.close()
        safe_unlink(tmp.name)
        raise

    # ✅ 注册 atexit 清理，防止临时文件累积
    import atexit
    atexit.register(lambda p=tmp.name: safe_unlink(p))
    return tmp.name


def image_to_base64(image_path: str) -> str:
    """图片文件转 base64 字符串."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
