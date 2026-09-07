"""fix/image-ref-pollution: 图片 URL 守卫单测——白名单 + 拒缩略后缀。

线上事故：多单「产品A卡片出现产品B图」。skill 侧搜索兜底把别家 310x310
缩略图串进货源图；worker 侧 E1 转存/生图参考必须把这类图挡在出口。

运行（无需 PG/GPU）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_image_url_guard.py -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.image_url_guard import filter_product_images, is_product_image_candidate


# 线上事故 payload 里的真实串图形态（两个不同卖家的搜索缩略图）
FOREIGN_THUMB_1 = ("https://cbu01.alicdn.com/img/ibank/"
                   "O1CN018Sj4ys2BTJkwUJ3Lc_!!2208080228339-0-cib.310x310.jpg")
FOREIGN_THUMB_2 = ("https://cbu01.alicdn.com/img/ibank/"
                   "O1CN01ZCd2oB1Np5z2dD7fR_!!2208161081618-0-cib.310x310.jpg")
GOOD_ORIGINAL = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-0-cib.jpg"
OZON_COMPETITOR = "https://ir.ozone.ru/s3/multimedia-1/cdn1/photo.jpg"


def test_rejects_search_thumbnail_310():
    """1688 搜索兜底串图特征（_310x310 缩略）→ 拒。"""
    assert is_product_image_candidate(FOREIGN_THUMB_1) is False
    assert is_product_image_candidate(FOREIGN_THUMB_2) is False


def test_rejects_other_thumbnail_sizes():
    """_460x460q100 等其他尺寸缩略 → 拒。"""
    assert is_product_image_candidate(
        "https://cbu01.alicdn.com/img/ibank/X_!!1-0-cib_460x460q100.jpg") is False


def test_rejects_webp():
    """webp 转换后缀 → 拒。"""
    assert is_product_image_candidate(
        "https://cbu01.alicdn.com/img/ibank/X.jpg_.webp") is False


def test_rejects_non_alicdn_domain():
    """非 alicdn/1688 域（竞品 Ozon 图等参考图）→ 拒，不再枚举黑名单域名。"""
    assert is_product_image_candidate(OZON_COMPETITOR) is False
    assert is_product_image_candidate(
        "https://cdn1.ozone.ru/s3/multimedia/abc.jpg") is False
    assert is_product_image_candidate(
        "https://ir-20.ozonstatic.cn/s3/x.jpg") is False


def test_accepts_alicdn_original():
    """alicdn 原尺寸图 → 放行。"""
    assert is_product_image_candidate(GOOD_ORIGINAL) is True


def test_rejects_garbage_input():
    """非字符串/空串/非 http → 拒（宁缺毋滥）。"""
    assert is_product_image_candidate(None) is False
    assert is_product_image_candidate("") is False
    assert is_product_image_candidate("  ") is False
    assert is_product_image_candidate("cbu01.alicdn.com/img/a.jpg") is False
    assert is_product_image_candidate(123) is False


def test_filter_product_images_keeps_order():
    """批量过滤：保序、去空、混合列表只留合格原图。"""
    out = filter_product_images([
        FOREIGN_THUMB_1, GOOD_ORIGINAL, "", OZON_COMPETITOR, None, FOREIGN_THUMB_2,
    ])
    assert out == [GOOD_ORIGINAL], out


# ═══ 参考图两条线：跟卖场景放行竞品原图（fix/image-ref-pollution R2）═══

def test_allow_competitor_accepts_ozone_original():
    """allow_competitor=True → ozone 域原尺寸图放行（跟卖参考线）。"""
    assert is_product_image_candidate(OZON_COMPETITOR, allow_competitor=True) is True


def test_allow_competitor_still_rejects_thumbnail():
    """allow_competitor=True → 竞品域缩略图仍拒（310x310 串图特征恒拒）。"""
    assert is_product_image_candidate(
        "https://ir.ozone.ru/s3/multimedia/photo_310x310.jpg",
        allow_competitor=True) is False


def test_allow_competitor_still_rejects_alicdn_thumb_and_webp():
    """allow_competitor=True 不影响 alicdn 缩略/webp 判定。"""
    assert is_product_image_candidate(FOREIGN_THUMB_1, allow_competitor=True) is False


def test_default_rejects_ozone_original():
    """默认（上传语义）→ ozone 图仍拒（向后兼容，E1/变体兜底零改动）。"""
    assert is_product_image_candidate(OZON_COMPETITOR) is False


def test_filter_allow_competitor_mixes_domains():
    """filter allow_competitor=True：alicdn 原图 + ozone 原图都留，缩略仍拒。"""
    out = filter_product_images(
        [GOOD_ORIGINAL, OZON_COMPETITOR, FOREIGN_THUMB_1], allow_competitor=True)
    assert out == [GOOD_ORIGINAL, OZON_COMPETITOR], out
