"""v0.25 图片/制造商兜底修复单测 — ① 跟卖上传绝不用竞品 ir.ozone.ru 图
（wave4 浴刷 0 图下架实证）；② 制造商 23487 中文供应商必须俄语化
（BR_chinese_hieroglyphs_in_attribute 整单拒绝实证）。

运行：
    cd worker && PYTHONPATH=src python3 tests/test_image_fallback_v025.py
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.prepare_ozon_upload_node import _build_shared_marketing_images  # noqa: E402


COS = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/abc.jpeg"
COMPETITOR = "https://ir.ozone.ru/s3/multimedia-1-x/wc50/12466146633.jpg"


def _state(**over):
    base = dict(
        main_image=COS,
        social_proof_image="",
        detail_image="",
        scene_1_image="",
        scene_2_image="",
        scene_3_image="",
        comparison_image="",
        multi_angle_image="",
        white_bg_image="",
        original_images=[COMPETITOR] * 9,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_no_competitor_images_in_upload_list():
    """跟卖且 AI 图不足 10 张 → 不再用竞品 ir.ozone.ru 补位，只返回现有 AI 图。"""
    images, main = _build_shared_marketing_images(_state(), is_follow_sell=True)
    assert images == [COS], f"应只有 1 张 AI 图，实际 {images}"
    assert all("ir.ozone.ru" not in u for u in images), "上传数组不得包含竞品图"
    assert main == COS


def test_main_missing_uses_first_ai_image():
    """AI 主图缺失 → 用第一张 AI 营销图做主图，绝不用竞品图。"""
    images, main = _build_shared_marketing_images(
        _state(main_image="", social_proof_image=COS),
        is_follow_sell=True,
    )
    assert images == [COS]
    assert main == COS
    assert "ir.ozone.ru" not in main


def test_non_follow_unaffected():
    """1688 直采（无竞品图）→ 行为不变：只有 AI 图。"""
    images, main = _build_shared_marketing_images(
        _state(original_images=[]),
        is_follow_sell=False,
    )
    assert images == [COS]
    assert main == COS


def test_manufacturer_chinese_supplier_translated():
    """制造商 23487 用中文供应商填充时必须俄语化（翻译成功用翻译值）。"""
    from unittest import mock
    import graphs.nodes.prepare_ozon_upload_node as mod

    state = SimpleNamespace(
        token="t",
        dictionary_values={},
        description_category_id="78021424",
        type_id="93971",
    )
    draft = {"supplier": "义乌市中亨日用百货有限公司", "title": "沐浴刷", "item_id": "123"}
    items = [{"attributes": []}]
    schema = [{"id": 23487, "name": "Производитель", "is_required": True, "dictionary_id": 0}]

    with mock.patch.object(mod, "_translate_to_russian_llm", return_value="Компания Иу Чжунхэн"):
        out = mod._fill_missing_required_dict_attrs(items, schema, draft, state)
    attr = out[0]["attributes"][0]
    val = attr["values"][0]["value"]
    assert attr["id"] == 23487
    assert "Компания Иу Чжунхэн" in val
    assert not any('\u4e00' <= c <= '\u9fff' for c in val), "不得包含中文"


def test_manufacturer_chinese_supplier_fallback():
    """制造商翻译失败/仍含中文 → 用安全俄语兜底 Китайская компания。"""
    from unittest import mock
    import graphs.nodes.prepare_ozon_upload_node as mod

    state = SimpleNamespace(
        token="t",
        dictionary_values={},
        description_category_id="78021424",
        type_id="93971",
    )
    draft = {"supplier": "义乌市中亨日用百货有限公司", "title": "沐浴刷", "item_id": "123"}
    items = [{"attributes": []}]
    schema = [{"id": 23487, "name": "Производитель", "is_required": True, "dictionary_id": 0}]

    with mock.patch.object(mod, "_translate_to_russian_llm", return_value="义乌市中亨日用百货有限公司"):
        out = mod._fill_missing_required_dict_attrs(items, schema, draft, state)
    val = out[0]["attributes"][0]["values"][0]["value"]
    assert val == "Китайская компания"
    assert not any('\u4e00' <= c <= '\u9fff' for c in val)


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
