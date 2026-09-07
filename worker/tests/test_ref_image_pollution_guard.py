# -*- coding: utf-8 -*-
"""fix/image-ref-pollution — 生图参考/变体兜底的串图防线单测。

线上事故：skill 搜索兜底把别家 1688 商品的 310x310 缩略图串进货源图字段 →
worker 生图节点拿它当参考（AI 重绘出别家产品）/ 变体原图兜底直上卡。

本文件锁定 worker 出口防线：
  1. white_bg / multi_angle / main_image 的参考图白名单过滤
     （拒缩略图/竞品域，保序保留合格 alicdn 原图）
  2. 参考图全不合格 → 跳过生图（不拿垃圾图硬生成）
  3. variant 原图兜底同样过白名单

运行（无需 PG/GPU）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_ref_image_pollution_guard.py -q
"""
import os
import sys
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.white_bg_gen_node import white_bg_gen_node  # noqa: E402
from graphs.nodes.multi_angle_gen_node import multi_angle_gen_node  # noqa: E402
from graphs.nodes.main_image_gen_node import main_image_gen_node  # noqa: E402
from graphs.nodes.variant_primary_loop_node import variant_primary_loop_node  # noqa: E402

import graphs.nodes.white_bg_gen_node as _white_bg_mod  # noqa: E402
import graphs.nodes.multi_angle_gen_node as _multi_angle_mod  # noqa: E402
import graphs.nodes.main_image_gen_node as _main_image_mod  # noqa: E402
import graphs.nodes.variant_primary_loop_node as _variant_mod  # noqa: E402

# ⚠️ 节点 import 链会触发 storage/db.py 的 load_dotenv()（注入本地 deploy/.env 的
# COS_*），污染 test_cos_uploader 的「未配置 COS」前提——此处统一清空（须在
# import 节点之后执行才有效）。
for _k in list(os.environ.keys()):
    if _k.startswith("COS_"):
        os.environ.pop(_k, None)

# 线上事故 payload 的真实串图形态（两个不同卖家的搜索缩略图）
FOREIGN_THUMB_1 = ("https://cbu01.alicdn.com/img/ibank/"
                   "O1CN018Sj4ys2BTJkwUJ3Lc_!!2208080228339-0-cib.310x310.jpg")
FOREIGN_THUMB_2 = ("https://cbu01.alicdn.com/img/ibank/"
                   "O1CN01ZCd2oB1Np5z2dD7fR_!!2208161081618-0-cib.310x310.jpg")
OZON_COMPETITOR = "https://ir.ozone.ru/s3/multimedia-1/photo.jpg"
GOOD_ORIGINAL = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-0-cib.jpg"
GOOD_ORIGINAL_2 = "https://cbu01.alicdn.com/img/ibank/O1CNgood2_!!987654321-0-cib.jpg"

_CONFIG = {"metadata": {"execute_id": "test-run"}}
_RUNTIME = type("FakeRuntime", (), {"context": None})()


def _capture_ref_images(node_fn, state, node_module):
    """执行节点，捕获 call_mxou_image_api 的 ref_images 参数列表。"""
    captured = []

    def _capture(token, prompt, ref_images=None, **kwargs):
        captured.append(list(ref_images or []))
        return "https://example.com/mock_image.jpg"

    with patch.object(node_module, "call_mxou_image_api", side_effect=_capture):
        node_fn(state, _CONFIG, _RUNTIME)
    return captured


def test_white_bg_filters_foreign_thumbnails():
    """white_bg：original_images 混入串图缩略/竞品图 → 参考只留合格原图。"""
    from graphs.state_image_gen import WhiteBgInput
    state = WhiteBgInput(draft={"title": "保温杯"}, token="t",
                         original_images=[FOREIGN_THUMB_1, GOOD_ORIGINAL, OZON_COMPETITOR])
    captured = _capture_ref_images(white_bg_gen_node, state, _white_bg_mod)
    assert captured, "white_bg 应正常调用生图"
    assert all(r not in (FOREIGN_THUMB_1, FOREIGN_THUMB_2, OZON_COMPETITOR)
               for r in captured[0]), captured[0]
    assert GOOD_ORIGINAL in captured[0], captured[0]


def test_white_bg_all_invalid_skips_gen():
    """white_bg：参考图全为串图 → 不调生图 API（不拿垃圾图硬生成）。"""
    from graphs.state_image_gen import WhiteBgInput
    state = WhiteBgInput(draft={"title": "保温杯"}, token="t",
                         original_images=[FOREIGN_THUMB_1, FOREIGN_THUMB_2])
    captured = _capture_ref_images(white_bg_gen_node, state, _white_bg_mod)
    assert not captured, f"参考全不合格应跳过生图，实际调用了: {captured}"


def test_multi_angle_filters_foreign_thumbnails():
    """multi_angle：同 white_bg 的白名单过滤。"""
    from graphs.state_image_gen import MultiAngleInput
    state = MultiAngleInput(draft={"title": "保温杯"}, token="t",
                            original_images=[FOREIGN_THUMB_1, GOOD_ORIGINAL_2])
    captured = _capture_ref_images(multi_angle_gen_node, state, _multi_angle_mod)
    assert captured
    assert captured[0] == [GOOD_ORIGINAL_2], captured[0]


def test_main_fallback_ref_filters_competitor():
    """main_image：Phase1 全失败走 original 兜底时，同样过滤串图。"""
    from graphs.state_image_gen import MainImageInput
    state = MainImageInput(draft={"title": "保温杯"}, token="t",
                           original_images=[OZON_COMPETITOR, FOREIGN_THUMB_1, GOOD_ORIGINAL])
    captured = _capture_ref_images(main_image_gen_node, state, _main_image_mod)
    assert captured
    assert GOOD_ORIGINAL in captured[0], captured[0]
    assert OZON_COMPETITOR not in captured[0] and FOREIGN_THUMB_1 not in captured[0], captured[0]


def test_variant_fallback_rejects_thumbnail():
    """variant：生图失败 + 原图是串图缩略 → 不兜底（返回空，由上层降级）。"""
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput
    state = VariantPrimaryLoopInput(
        variants=[{"name": "红色", "image": FOREIGN_THUMB_1}],
        white_bg_image="https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/wb.jpg",
        draft={"title": "保温杯"}, token="t",
    )

    def _fail(*args, **kwargs):
        return None  # 生图失败

    with patch.object(_variant_mod, "call_mxou_image_api", side_effect=_fail):
        out = variant_primary_loop_node(state, _CONFIG, _RUNTIME)
    assert list(out.variant_primary_images) == [""], out.variant_primary_images


def test_variant_fallback_keeps_good_original():
    """variant：生图失败 + 原图是合格 alicdn 原图 → 正常兜底（v0.60 行为保持）。"""
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput
    state = VariantPrimaryLoopInput(
        variants=[{"name": "红色", "image": GOOD_ORIGINAL}],
        draft={"title": "保温杯"}, token="t",
    )

    def _fail(*args, **kwargs):
        return None

    with patch.object(_variant_mod, "call_mxou_image_api", side_effect=_fail):
        out = variant_primary_loop_node(state, _CONFIG, _RUNTIME)
    assert list(out.variant_primary_images) == [GOOD_ORIGINAL], out.variant_primary_images


def test_variant_missing_image_uses_phase1_fallback_ref():
    """R2 回归锁定：variant.image 缺失时用 white_bg_image（自家 AI 生成图，
    mxou COS 域）做参考正常生图——白名单只管 1688 原图，不得误杀
    v0.26 的缺图兜底通道（审查实证回归，review 后修复）。"""
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput
    state = VariantPrimaryLoopInput(
        variants=[{"name": "红色", "image": ""}],
        white_bg_image="https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/wb.jpg",
        draft={"title": "保温杯"}, token="t",
    )
    captured = _capture_ref_images(variant_primary_loop_node, state, _variant_mod)
    assert captured and captured[0] == [
        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/wb.jpg"], captured


def test_variant_bad_image_falls_back_to_phase1():
    """R2 回归锁定：variant.image 是串图缩略图 → 降级用 white_bg_image 参考，
    而非直接放弃生图。"""
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput
    state = VariantPrimaryLoopInput(
        variants=[{"name": "红色", "image": FOREIGN_THUMB_1}],
        white_bg_image="https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/wb.jpg",
        draft={"title": "保温杯"}, token="t",
    )
    captured = _capture_ref_images(variant_primary_loop_node, state, _variant_mod)
    assert captured and captured[0] == [
        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/wb.jpg"], captured


# ═══ fix/image-ref-pollution R2: 跟卖标记 extensions 全链透传 ═══

def test_follow_import_output_declares_extensions():
    """跟卖导入 Output 必须声明 extensions（否则 GlobalState.extensions
    断链 → prepare 跟卖判定与生图参考分线读不到 follow_sell）。"""
    import inspect

    from graphs.state import FollowSellImportOutput
    assert "extensions" in FollowSellImportOutput.model_fields, \
        "FollowSellImportOutput 缺 extensions 字段（跟卖标记断链）"
    # 三个 return 点（ozon_product_id 为空 / 类目解析失败 / 主成功路径）都必须透传
    import graphs.nodes.follow_sell_import_node as fi_mod
    src = inspect.getsource(fi_mod)
    assert src.count('"extensions": extensions') >= 3, \
        "follow_sell_import_node 返回点未透传 extensions（全部路径均需）"


# ═══ fix/image-ref-pollution R2: 生图参考两条线 ═══

OZON_COMPETITOR_ORIGINAL = "https://ir.ozone.ru/s3/multimedia-1/cdn1/photo.jpg"


def test_white_bg_follow_line_prefers_competitor_refs():
    """跟卖线：竞品主图优先于 1688 货源图（两条线设计语义）。"""
    from graphs.state_image_gen import WhiteBgInput
    state = WhiteBgInput(
        draft={"title": "保温杯"}, token="t", original_images=[GOOD_ORIGINAL],
        extensions={"follow_sell": True,
                    "competitor_ref_images": [OZON_COMPETITOR_ORIGINAL]},
    )
    captured = _capture_ref_images(white_bg_gen_node, state, _white_bg_mod)
    assert captured, "跟卖线应有参考图可生图"
    assert captured[0][0] == OZON_COMPETITOR_ORIGINAL, captured[0]
    assert GOOD_ORIGINAL in captured[0], captured[0]


def test_white_bg_follow_line_uses_competitor_when_no_1688():
    """跟卖线：1688 货源图缺失 → 竞品主图独立担纲参考（不再无参考跳过）。"""
    from graphs.state_image_gen import WhiteBgInput
    state = WhiteBgInput(
        draft={"title": "保温杯"}, token="t", original_images=[],
        extensions={"follow_sell": True,
                    "competitor_ref_images": [OZON_COMPETITOR_ORIGINAL]},
    )
    captured = _capture_ref_images(white_bg_gen_node, state, _white_bg_mod)
    assert captured and captured[0] == [OZON_COMPETITOR_ORIGINAL], captured


def test_white_bg_follow_line_rejects_competitor_thumbnail():
    """跟卖线：竞品缩略图（串图特征）仍拒 → 无合格参考不生图。"""
    from graphs.state_image_gen import WhiteBgInput
    state = WhiteBgInput(
        draft={"title": "保温杯"}, token="t", original_images=[],
        extensions={"follow_sell": True,
                    "competitor_ref_images": [
                        "https://ir.ozone.ru/s3/multimedia/photo_310x310.jpg"]},
    )
    captured = _capture_ref_images(white_bg_gen_node, state, _white_bg_mod)
    assert not captured, "竞品缩略图不得做参考"


def test_white_bg_non_follow_still_rejects_competitor():
    """非跟卖（1688 直上）：竞品原图混入 original_images 仍拒（两条线边界）。"""
    from graphs.state_image_gen import WhiteBgInput
    state = WhiteBgInput(
        draft={"title": "保温杯"}, token="t",
        original_images=[OZON_COMPETITOR_ORIGINAL, GOOD_ORIGINAL],
        extensions={"follow_sell": False},
    )
    captured = _capture_ref_images(white_bg_gen_node, state, _white_bg_mod)
    assert captured and captured[0] == [GOOD_ORIGINAL], captured


def test_main_follow_line_competitor_fallback_ref():
    """main 兜底参考（Phase1 失败）跟卖线同样竞品优先。"""
    from graphs.state_image_gen import MainImageInput
    state = MainImageInput(
        draft={"title": "保温杯"}, token="t", original_images=[GOOD_ORIGINAL],
        extensions={"follow_sell": True,
                    "competitor_ref_images": [OZON_COMPETITOR_ORIGINAL]},
    )
    captured = _capture_ref_images(main_image_gen_node, state, _main_image_mod)
    assert captured and captured[0][0] == OZON_COMPETITOR_ORIGINAL, captured
