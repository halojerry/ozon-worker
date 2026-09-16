"""fix/image-ref-pollution: 图片 URL 守卫单测——白名单 + 拒缩略后缀。

线上事故：多单「产品A卡片出现产品B图」。skill 侧搜索兜底把别家 310x310
缩略图串进货源图；worker 侧 E1 转存/生图参考必须把这类图挡在出口。

fix/image-ref-cos-whitelist-v1（2026-09-16 原图上卡事故批1）：白名单放行本方
COS 托管图（镜像 = 货源原图 1:1 副本，参考语义等价）。

运行（无需 PG/GPU）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_image_url_guard.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.image_url_guard import filter_product_images, is_cos_url, is_product_image_candidate


# 线上事故 payload 里的真实串图形态（两个不同卖家的搜索缩略图）
FOREIGN_THUMB_1 = ("https://cbu01.alicdn.com/img/ibank/"
                   "O1CN018Sj4ys2BTJkwUJ3Lc_!!2208080228339-0-cib.310x310.jpg")
FOREIGN_THUMB_2 = ("https://cbu01.alicdn.com/img/ibank/"
                   "O1CN01ZCd2oB1Np5z2dD7fR_!!2208161081618-0-cib.310x310.jpg")
GOOD_ORIGINAL = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-0-cib.jpg"
OZON_COMPETITOR = "https://ir.ozone.ru/s3/multimedia-1/cdn1/photo.jpg"

# 本方 COS 托管图（2026-09-16 原图上卡事故：draft_image_mirror 回写后白名单拒 COS）
COS_MIRROR_ORIGINAL = ("https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
                       "/draft-images/8dbf02b9f.jpg")
COS_AI_GEN = ("https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
              "/file/images/abc.jpg")
COS_E1_SALVAGE = ("https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
                  "/ozon-1688/salvage/d41d8cd9.jpg")
COS_EVIL_DOMAIN = "https://evil.example.com/draft-images/x.jpg"
COS_ACCELERATE = ("https://yss-1256275613.cos.accelerate.myqcloud.com"
                  "/draft-images/x.jpg")
COS_THUMBNAIL = ("https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
                 "/draft-images/x_310x310.jpg")


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


# ---- fix/image-ref-cos-whitelist-v1：白名单放行本方 COS 托管图（批1） ----


def test_accepts_cos_mirror_original():
    """用例1：COS 镜像原图（draft_image_mirror 回写形态）→ 放行（本事故主场景）。"""
    assert is_product_image_candidate(COS_MIRROR_ORIGINAL) is True


def test_accepts_cos_ai_generated():
    """用例2：COS AI 生成图回流作参考 → 放行。"""
    assert is_product_image_candidate(COS_AI_GEN) is True


def test_accepts_cos_e1_salvage():
    """用例3：COS E1 兜底转存产物 → 放行。"""
    assert is_product_image_candidate(COS_E1_SALVAGE) is True


def test_rejects_non_cos_image_host():
    """用例4：非 COS 非白名单域（evil.example.com 挂 draft-images 路径）→ 仍拒。"""
    assert is_product_image_candidate(COS_EVIL_DOMAIN) is False


def test_accepts_cos_accelerate_domain():
    """用例5：COS 全球加速形态 cos.accelerate.myqcloud.com → 放行。"""
    assert is_product_image_candidate(COS_ACCELERATE) is True


def test_cos_true_contract_does_not_leak():
    """用例6：非字符串/空串/非 http 协议 → False（is_cos_url 对它们的 True 契约
    绝不外溢到本函数——顺序红线：isinstance/前缀检查必须在前）。"""
    assert is_product_image_candidate(None) is False
    assert is_product_image_candidate(123) is False
    assert is_product_image_candidate("") is False
    assert is_product_image_candidate("ftp://x") is False


def test_cos_thumbnail_still_rejected():
    """用例7：COS 域不豁免缩略恒拒——镜像 key 带 _310x310 后缀照样拒。"""
    assert is_product_image_candidate(COS_THUMBNAIL) is False


def test_webp_rejection_applies_to_cos():
    """补充：.webp 恒拒对 COS 域同样生效（COS 分支只豁免图床域白名单）。"""
    assert is_product_image_candidate(
        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg_.webp",
    ) is False


def test_existing_whitelist_face_unchanged():
    """用例8：既有图床白名单面（alicdn/1688/taobaocdn/pdd）回归零变化。"""
    assert is_product_image_candidate(GOOD_ORIGINAL) is True
    assert is_product_image_candidate(
        "https://img.1688.com/img/bao/uploaded/i3/abc.jpg") is True
    assert is_product_image_candidate(
        "https://img.alicdn.com/imgextra/taobao/def.jpg") is True
    assert is_product_image_candidate(
        "https://cbu01.alicdn.com/img/ibank/ghz.jpg") is True
    assert is_product_image_candidate(
        "https://img.pddpic.com/mms-material/uvw.jpg") is True
    assert is_product_image_candidate(
        "https://yangkeduo.com/img/xyz.jpg") is True
    # 拒绝面不变
    assert is_product_image_candidate(OZON_COMPETITOR) is False
    assert is_product_image_candidate(FOREIGN_THUMB_1) is False


def test_filter_product_images_incident_regression():
    """用例12（本事故直接回归闸）：镜像 COS + alicdn 原图保留、None/竞品图剔除，
    顺序保持、去空。"""
    out = filter_product_images([
        COS_MIRROR_ORIGINAL, GOOD_ORIGINAL, None, OZON_COMPETITOR,
    ])
    assert out == [COS_MIRROR_ORIGINAL, GOOD_ORIGINAL], out


# ---- is_cos_url 迁移（utils/cos_uploader → utils/image_url_guard）----


def test_is_cos_url_reexport_identity():
    """re-export 防断裂锁定：两个 import 路径拿到同一对象。"""
    from utils.cos_uploader import is_cos_url as from_cos_uploader

    assert from_cos_uploader is is_cos_url


def test_is_cos_url_semantics_preserved():
    """迁移后原语义零变化（3+1 个消费方依赖）：非 str/空串 → True；
    区域域/全球加速域 → True；普通域 → False。"""
    assert is_cos_url(None) is True
    assert is_cos_url(123) is True
    assert is_cos_url("") is True
    assert is_cos_url("   ") is True
    assert is_cos_url(COS_MIRROR_ORIGINAL) is True
    assert is_cos_url(COS_ACCELERATE) is True
    assert is_cos_url(GOOD_ORIGINAL) is False
    assert is_cos_url(OZON_COMPETITOR) is False
