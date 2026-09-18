"""fix/upload-image-assertion-v1 收尾卡片图断言回归锁（2026-09-18 用户②）。

背景：商品 6381680593 首传 AI 图被重传链 draft 原图覆盖静默上线——收尾零校验。
"""
from __future__ import annotations

import struct

from utils.card_image_assert import (
    VERIFY_MISMATCH,
    VERIFY_OK,
    VERIFY_SKIPPED,
    VERIFY_UNVERIFIED,
    image_size_from_bytes,
    is_all_ai_images,
    is_allowed_card_url,
    verify_card_images,
)

AI = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/ai1.jpeg"
AI2 = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/ai2.jpeg"
SALVAGE = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-1688/salvage/x.jpg"
CARD = "https://ir-20.ozone.ru/s3/multimedia-1-s/15112568188.jpg"


def _jpg(w: int, h: int) -> bytes:
    # SOI + APP0(最小) + SOF0{len,prec,h,w}
    return (
        b"\xff\xd8"
        b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
        + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w)
        + b"\x00" * 3
    )


def _png(w: int, h: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR"
        + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    )


# ── 头解析 ──

def test_jpeg_header_size():
    assert image_size_from_bytes(_jpg(896, 1200)) == (896, 1200)


def test_png_header_size():
    assert image_size_from_bytes(_png(800, 800)) == (800, 800)


def test_garbage_returns_none():
    assert image_size_from_bytes(b"\x00" * 64) is None
    assert image_size_from_bytes(b"") is None


# ── 域白名单 ──

def test_card_url_allowlist():
    assert is_allowed_card_url(CARD) is True
    assert is_allowed_card_url("https://evil.example.com/x.jpg") is False
    assert is_allowed_card_url("http://ir-20.ozone.ru/x.jpg") is False  # 非 https


# ── verify 决策 ──

def test_skip_when_payload_empty():
    st, _ = verify_card_images([], [CARD])
    assert st == VERIFY_SKIPPED


def test_unverified_when_card_images_not_populated():
    st, _ = verify_card_images([AI], [])
    assert st == VERIFY_UNVERIFIED


def test_mismatch_on_count_shortfall():
    st, d = verify_card_images([AI, AI2], [CARD])
    assert st == VERIFY_MISMATCH and "数量" in d


def test_mismatch_when_ai_payload_but_square_card():
    st, d = verify_card_images([AI], [CARD], fetch_size=lambda u: (800, 800))
    assert st == VERIFY_MISMATCH and "非生成图规格" in d


def test_ok_when_ai_payload_and_3to4_card():
    st, _ = verify_card_images([AI], [CARD], fetch_size=lambda u: (896, 1200))
    assert st == VERIFY_OK


def test_unverified_when_fetch_fails():
    st, _ = verify_card_images([AI], [CARD], fetch_size=lambda u: None)
    assert st == VERIFY_UNVERIFIED


def test_ok_count_only_for_salvage_payload():
    st, _ = verify_card_images([SALVAGE], [CARD], fetch_size=lambda u: (_ for _ in ()).throw(AssertionError("非 AI 路径不应下载")))
    assert st == VERIFY_OK


# ── AI 判定 ──

def test_is_all_ai_images():
    assert is_all_ai_images([AI, AI2]) is True
    assert is_all_ai_images([AI, SALVAGE]) is False
    assert is_all_ai_images([]) is False
