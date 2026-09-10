"""跨平台货源扩展 v1 批1:图片 URL 守卫多平台白名单单测。

货源从「仅 1688」扩为 1688/淘宝/天猫/拼多多 后,taobaocdn/pddpic/yangkeduo
图床的货源原图必须过得了 E1 转存/生图参考白名单(image_url_guard),否则 pdd
货源生图退化纯文本、validate 全外链硬拦无解(PLAN §2 A1/A3)。

「缩略恒拒」语义不变:新平台的缩略后缀形态(_60x60 / .jpg_400x400)同样拒绝。

运行(无需 PG/GPU):
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_image_guard_multi_platform_v1.py -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.image_url_guard import filter_product_images, is_product_image_candidate


# 现有 1688 夹具(锁旧行为不回归——与 test_image_url_guard.py 同源语义)
FOREIGN_THUMB_1 = ("https://cbu01.alicdn.com/img/ibank/"
                   "O1CN018Sj4ys2BTJkwUJ3Lc_!!2208080228339-0-cib.310x310.jpg")
GOOD_1688_ORIGINAL = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-0-cib.jpg"
OZON_COMPETITOR = "https://ir.ozone.ru/s3/multimedia-1/cdn1/photo.jpg"

# 新平台原图(白名单放行目标)
PDD_ORIGINAL = "https://img.pddpic.com/mms-material-img/2024-06-11/abcdef.jpeg"
PDD_ORIGINAL_T00 = "https://t00img.yangkeduo.com/goods/images/2019-03-24/abc.jpeg"
PDD_ORIGINAL_OMS = "https://omsproductionimgs.yangkeduo.com/goods/images/abc.jpeg"
PDD_ORIGINAL_PIN = "https://mobile.pinduoduo.com/goods/images/abc.jpg"
TAOBAO_ORIGINAL = "https://img.taobaocdn.com/bao/uploaded/i1/T1abcXXXXXXXXX.jpg"


def test_accepts_pdd_originals():
    """拼多多图床(pddpic/yangkeduo/pinduoduo)原尺寸图 → 放行。"""
    assert is_product_image_candidate(PDD_ORIGINAL) is True
    assert is_product_image_candidate(PDD_ORIGINAL_T00) is True
    assert is_product_image_candidate(PDD_ORIGINAL_OMS) is True
    assert is_product_image_candidate(PDD_ORIGINAL_PIN) is True


def test_accepts_taobao_cdn_original():
    """淘宝系图床(taobaocdn;主图床 img.alicdn.com 现状已在白名单) → 放行。"""
    assert is_product_image_candidate(TAOBAO_ORIGINAL) is True
    # 天猫/淘宝主图实际多挂 img.alicdn.com——旧行为已放行,锁住
    assert is_product_image_candidate(
        "https://img.alicdn.com/imgextra/i2/O1CNmain_!!600000000.jpg") is True


def test_rejects_random_cdn():
    """非白名单域(随机 CDN/竞品 Ozon 图)→ 拒,白名单语义不放宽。"""
    assert is_product_image_candidate("https://cdn.example.com/x.jpg") is False
    assert is_product_image_candidate(OZON_COMPETITOR) is False
    assert is_product_image_candidate(
        "https://cdn1.ozone.ru/s3/multimedia/abc.jpg") is False
    # 白名单是子串语义（与 alicdn 既有口径一致）：仅含「pddpic」字样但不含
    # 「pddpic.com」子串的域不放行
    assert is_product_image_candidate("https://pddpic.example.com/x.jpg") is False


def test_rejects_new_platform_thumbnails():
    """淘宝/pdd 缩略后缀形态 → 拒(缩略恒拒语义跨平台一致)。

    _60x60 等两位数尺寸段是本批新增拦截面(旧 \\d{3,4} 漏两位);
    .jpg_400x400 / _jpeg_310x310 形态旧 pattern 已拦,本测锁死防回归。
    """
    # 两位数缩略(新增拦截)
    assert is_product_image_candidate(
        "https://img.pddpic.com/mms-material-img/abc.jpeg_60x60.jpeg") is False
    assert is_product_image_candidate(
        "https://img.alicdn.com/imgextra/i2/O1CNmain_!!600000000.jpg_60x60.jpg") is False
    assert is_product_image_candidate(
        "https://img.taobaocdn.com/bao/uploaded/i1/T1abc.jpg_50x50.jpg") is False
    # 三四位数缩略(旧 pattern 已拦,跨新域锁死)
    assert is_product_image_candidate(
        "https://img.taobaocdn.com/bao/uploaded/i1/T1abc.jpg_400x400.jpg") is False
    assert is_product_image_candidate(
        "https://t00img.yangkeduo.com/goods/images/abc_jpeg_310x310.jpg") is False
    assert is_product_image_candidate(
        "https://img.pddpic.com/mms-material-img/abc.jpeg_460x460q100.jpeg") is False


def test_rejects_webp_and_garbage_unchanged():
    """webp 转换后缀 + 垃圾输入 → 拒(现有语义不变)。"""
    assert is_product_image_candidate(
        "https://img.pddpic.com/x/abc.jpg_.webp") is False
    assert is_product_image_candidate(
        "https://img.taobaocdn.com/x/abc.jpg_.webp") is False
    assert is_product_image_candidate(None) is False
    assert is_product_image_candidate("") is False
    assert is_product_image_candidate(123) is False
    assert is_product_image_candidate("img.pddpic.com/x/a.jpg") is False


def test_existing_fixtures_unchanged():
    """现有 1688 夹具行为逐字节不变:原图放行/串图缩略拒。"""
    assert is_product_image_candidate(FOREIGN_THUMB_1) is False
    assert is_product_image_candidate(GOOD_1688_ORIGINAL) is True
    assert is_product_image_candidate(OZON_COMPETITOR) is False


def test_filter_multi_platform_mix_keeps_order():
    """批量过滤:四平台混合列表,只留各平台合格原图,保序去空。"""
    out = filter_product_images([
        FOREIGN_THUMB_1, GOOD_1688_ORIGINAL, "", PDD_ORIGINAL,
        OZON_COMPETITOR, None, TAOBAO_ORIGINAL, PDD_ORIGINAL_T00,
    ])
    assert out == [GOOD_1688_ORIGINAL, PDD_ORIGINAL, TAOBAO_ORIGINAL, PDD_ORIGINAL_T00], out
