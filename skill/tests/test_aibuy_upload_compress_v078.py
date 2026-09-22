#!/usr/bin/env python3
"""批A Q6（fix/skill-silent-cdp-v1）：aibuy upload 前压缩回归（根治 HTTP 413）。

背景：_aibuy_image_upload 此前把原图 base64（膨胀 1.33×）直接 POST，无任何
上限/压缩 → 大图 413 后静默降级。本文件锁定：
1. image_preprocessor.downscale_for_upload：最长边 >1024 才缩；统一转 JPEG q80
   （动图取首帧）；PIL 任何异常原样返回 bytes 绝不 raise；
2. _aibuy_image_upload 下载后过压缩再 base64（len<100 原始字节守卫保留）；
   压缩失败回退原始字节。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_aibuy_upload_compress_v078.py -q
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_image_search as ois  # noqa: E402
from scripts.lib.image_preprocessor import downscale_for_upload  # noqa: E402

from PIL import Image as PILImage  # noqa: E402


def _png_bytes(w: int, h: int) -> bytes:
    """内存里造一张 w×h 纯色 PNG（无需磁盘文件）。"""
    img = PILImage.new("RGB", (w, h), (200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _noisy_png_bytes(w: int, h: int) -> bytes:
    """噪声图 PNG（模拟真实照片字节量——纯色 PNG 本身就比 JPEG 小，测不出压缩收益）。"""
    img = PILImage.effect_noise((w, h), 64).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _dims(data: bytes) -> tuple[int, int, str]:
    img = PILImage.open(io.BytesIO(data))
    w, h = img.size
    fmt = img.format or ""
    img.close()
    return w, h, fmt


# ── downscale_for_upload 单元 ─────────────────────────────────────────────


def test_downscale_scales_long_edge_over_1024():
    out = downscale_for_upload(_png_bytes(2000, 1500))
    w, h, fmt = _dims(out)
    assert fmt == "JPEG", "统一转 JPEG"
    assert max(w, h) <= 1024, f"最长边应压到 ≤1024，实际 {w}x{h}"
    assert (w, h) == (1024, 768), "等比缩放保比例（2000x1500 → 1024x768）"
    raw = _noisy_png_bytes(2000, 1500)
    assert len(downscale_for_upload(raw)) < len(raw), "照片类噪声图压缩产物应显著变小"


def test_downscale_keeps_small_image_dims_but_converts_jpeg():
    out = downscale_for_upload(_png_bytes(500, 400))
    w, h, fmt = _dims(out)
    assert (w, h) == (500, 400), "≤1024 不缩"
    assert fmt == "JPEG", "统一转 JPEG（q80）"


def test_downscale_broken_bytes_returns_original():
    raw = b"\x89PNG\r\n\x1a\n" + b"not-really-an-image" * 10
    out = downscale_for_upload(raw)
    assert out == raw, "PIL 异常必须原样返回 bytes，绝不 raise"


def test_downscale_takes_first_frame_of_animated_gif():
    buf = io.BytesIO()
    base = PILImage.new("RGB", (1600, 900), (10, 10, 10))
    frames = [base.copy() for _ in range(3)]
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    out = downscale_for_upload(buf.getvalue())
    w, h, fmt = _dims(out)
    assert fmt == "JPEG" and max(w, h) <= 1024, "动图取首帧并压缩"


def test_downscale_transparent_png_gets_white_background():
    img = PILImage.new("RGBA", (1200, 800), (255, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = downscale_for_upload(buf.getvalue())  # 不应因 alpha 通道 raise
    _, _, fmt = _dims(out)
    assert fmt == "JPEG"


# ── _aibuy_image_upload 接线 ──────────────────────────────────────────────

_UPLOAD_OK_TEXT = (
    'mtopjsonp_aibuy({"ret":["SUCCESS::调用成功"],'
    '"data":{"result":{"imageUrl":"https://img.example/up.jpg"}}})'
)


class _Resp:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


def _capture_upload(monkeypatch, download_content: bytes):
    """monkeypatch requests.get/post：get 回下载内容，post 捕获 payload。"""
    captured: dict = {}

    def _fake_get(url, **kw):
        return _Resp(status_code=200, content=download_content)

    def _fake_post(url, params=None, data=None, timeout=None, headers=None,
                   cookies=None):
        captured["data"] = data
        return _Resp(status_code=200, text=_UPLOAD_OK_TEXT)

    monkeypatch.setattr(ois.requests, "get", _fake_get)
    monkeypatch.setattr(ois.requests, "post", _fake_post)
    return captured


def test_upload_sends_downscaled_jpeg_base64(monkeypatch):
    raw = _noisy_png_bytes(2000, 1500)
    captured = _capture_upload(monkeypatch, raw)
    uploaded = ois._aibuy_image_upload("https://img.example/big.png",
                                       {"_m_h5_tk": "tk_1"})
    assert uploaded == "https://img.example/up.jpg"
    data = json.loads(captured["data"]["data"])
    payload = base64.b64decode(data["imageBase64"])
    w, h, fmt = _dims(payload)
    assert fmt == "JPEG", "上传 payload 必须是压缩后的 JPEG"
    assert max(w, h) <= 1024
    assert len(payload) < len(raw), "不得再把原图 base64 直灌（413 根源）"


def test_upload_falls_back_to_raw_bytes_when_uncompressible(monkeypatch):
    raw = b"\x00\x01" * 100  # ≥100 字节但不是合法图片
    captured = _capture_upload(monkeypatch, raw)
    ois._aibuy_image_upload("https://img.example/broken.bin", {"_m_h5_tk": "tk_1"})
    data = json.loads(captured["data"]["data"])
    assert base64.b64decode(data["imageBase64"]) == raw, "压缩失败回退原始字节"


def test_upload_small_download_guard_kept(monkeypatch):
    """len<100 原始字节守卫逐字保留：过小内容不上传（走原始 URL 直搜兜底）。"""
    captured = _capture_upload(monkeypatch, b"tiny")
    uploaded = ois._aibuy_image_upload("https://img.example/tiny.png",
                                       {"_m_h5_tk": "tk_1"})
    assert uploaded == ""
    assert "data" not in captured, "过小内容不得触发 upload POST"
