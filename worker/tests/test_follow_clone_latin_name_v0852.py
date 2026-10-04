"""follow_clone 复测遗留三件回归（v0.85.2 批，2026-10-04 复测 4/5 死因打通）。

复测 gate（/tmp/followclone_eval2/）暴露的三件，本文件逐件锁定：
1. 克隆卡名回读：import-by-sku 复制成功后卡上 name=竞品卡俄语原名（Ozon 已
   接受的事实），draft.title 只是 CDP 抓的拉丁占位（"Beanie Hat"）——
   follow_sell_import_node 的 /v3 info/list 反查（类目回填同一次调用）把 name
   一并带回（copied_card_name 通道）→ prepare follow_clone 模式 item name
   逐字用它；空值回落 draft.title（现状）；非 clone 模式零变化；
2. retry 环拉丁错快速失败：clone 模式 name 拉丁错（本地预检曾被关键词粗分类
   误归 BR_chinese_hieroglyphs_in_attribute → 中文属性翻译空转 3 轮终态还挂
   误导错误码）→ LOCAL_NAME_LATIN 快速失败终态；
3. Step-7 图片校验 clone 豁免：clone 模式零生图 → shared_marketing_images 恒
   空，「图片列表为空」是过时误报（图走 _apply_follow_clone_images 回填）——
   该校验行 clone 模式跳过，非 clone 语义零变化。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_follow_clone_latin_name_v0852.py -q
"""
from __future__ import annotations

import sys
import unittest.mock as mock
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_RU_NAME = "Шапка бини зимняя с помпоном"
_LATIN_NAME = "Beanie Hat"


# ═══════════════ 件1a: follow_sell_import 克隆卡名回读 ═══════════════


class _FakeState:
    def __init__(self, envelope):
        self.envelope = envelope
        self.ozon_client_id = "1"
        self.ozon_api_key = "k"
        self.currency_code = "CNY"
        self.token = "sk-t"


class _PostRecorder:
    """对齐 test_follow_clone_v085._PostRecorder；product/info/list 增带 name。"""

    def __init__(self, unmatched=False, imported=True, info_name=""):
        self.calls = []
        self.unmatched = unmatched
        self.imported = imported
        self.info_name = info_name

    def __call__(self, client_id, api_key, endpoint, body=None, timeout=30, **kw):
        self.calls.append(endpoint)
        if "import-by-sku" in endpoint:
            return {"result": {"task_id": "7",
                               "unmatched_sku_list": [111] if self.unmatched else []}}
        if "import/info" in endpoint:
            items = [{"product_id": 999888777, "status": "imported"}] if self.imported else []
            return {"result": {"items": items}}
        if "product/info/list" in endpoint:
            item = {"id": 999888777, "description_category_id": 17027933,
                    "type_id": 970742618}
            if self.info_name:
                item["name"] = self.info_name
            return {"items": [item]}
        if "product/info/attributes" in endpoint:
            return {"result": []}
        return {}


def _clone_envelope(title=_LATIN_NAME):
    return {
        "draft": {
            "ozon_product_id": "2062059459",
            "title": title,
            "images": [],
            "ozon_category": {},
            "competitor_price": "240.00",
            "purchase_cost": 15.0,
            "purchase_url": "https://detail.1688.com/offer/9.html",
            "item_id": "9",
        },
        "extensions": {"follow_sell": True, "follow_type": "clone",
                       "follow_clone": True},
    }


def _run_follow(envelope, recorder):
    import graphs.nodes.follow_sell_import_node as fsin
    import utils.attr_defaults as attr_defaults_mod
    import utils.category_mapping_learn as cml_mod
    import utils.ozon_category_query as ocq_mod

    with mock.patch.object(ocq_mod, "get_category_query", return_value=None), \
         mock.patch.object(fsin, "_verify_category_schema", return_value=False), \
         mock.patch.object(fsin, "_resolve_category_by_id", return_value=("", "")), \
         mock.patch.object(fsin, "_gated_category_arbitration", return_value=("", "")), \
         mock.patch.object(cml_mod, "lookup_mapping", return_value=None), \
         mock.patch.object(attr_defaults_mod, "build_follow_attr_merge",
                           return_value=[]), \
         mock.patch("time.sleep", lambda s: None), \
         mock.patch.object(fsin, "ozon_post", recorder):
        return fsin.follow_sell_import_node(_FakeState(envelope))


def test_11_copied_card_name_readback_on_import_success():
    """复制成功 → /v3 反查（类目回填同一次调用）带回卡上俄语 name。"""
    rec = _PostRecorder(unmatched=False, imported=True, info_name=_RU_NAME)
    result = _run_follow(_clone_envelope(), rec)
    assert "/v1/product/import-by-sku" in rec.calls
    assert str(result.get("product_id")) == "999888777"
    assert result.get("copied_card_name") == _RU_NAME, (
        f"复制卡 name 必须经 /v3 反查带回，实际 {result.get('copied_card_name')!r}")
    assert result.get("description_category_id") == "17027933", "类目回填语义不变"


def test_12_copied_card_name_empty_when_not_copied():
    """不可复制（unmatched）→ 无反查 → copied_card_name 空（clone_card 回退
    路径到最终返回，prepare 回落现状）。"""
    rec = _PostRecorder(unmatched=True)
    env = _clone_envelope()
    env["extensions"]["clone_card"] = {
        "product_id": "2062059459", "name": "Организатор",
        "dc": "92417201", "tp": "92532",
        "attributes": [{"id": 85, "values": [
            {"dictionary_value_id": 126745801, "value": "Нет бренда"}]}],
        "images": [],
    }
    result = _run_follow(env, rec)
    assert not result.get("product_id")
    assert (result.get("copied_card_name") or "") == ""


def test_13_copied_card_name_empty_when_info_list_omits_name():
    """反查命中但响应无 name 字段 → 空（不编造）。"""
    rec = _PostRecorder(unmatched=False, imported=True, info_name="")
    result = _run_follow(_clone_envelope(), rec)
    assert str(result.get("product_id")) == "999888777"
    assert result.get("copied_card_name") == ""


def test_14_channel_declared_in_state_models():
    """channel 纪律：GlobalState / PrepareOzonUploadInput 两处声明（漏一跳被
    langgraph channel 静默过滤，W1 检测器红线）。"""
    from graphs.state import GlobalState, PrepareOzonUploadInput
    assert "copied_card_name" in GlobalState.model_fields
    assert "copied_card_name" in PrepareOzonUploadInput.model_fields


# ═══════════════ 件1b: prepare follow_clone item name 回读 ═══════════════


def _prepare_draft(**over):
    d = {
        "item_id": "clonehat",
        "title": _LATIN_NAME,  # CDP 抓的拉丁占位（复测 4/5 死因）
        "images": [],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {},
        "sku_id": "clonehat",
        "price": "100",
        "original_price": "150",
        "ozon_product_id": "2062059459",
    }
    d.update(over)
    return d


def _make_prepare_state(draft, extensions=None, copied_card_name=""):
    from graphs.state import PrepareOzonUploadInput
    return PrepareOzonUploadInput(
        draft=draft,
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        extensions=extensions or {},
        pricing_info={"price": 100, "old_price": 150, "variant_prices": []},
        description_category_id="17027933",
        type_id="970742618",
        final_attributes=[],
        attributes_schema=[],
        dictionary_values={},
        token="sk-test",
        original_images=[],
        copied_card_name=copied_card_name,
    )


def _base_patches():
    """统一 mock（对齐 test_validate_category_source_v078 手法，防网络）。"""
    return [
        patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title", return_value="Товар для дома"),
        patch("utils.mxou_api.call_mxou_chat_api", return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm", return_value=""),
    ]


_CLONE_EXT = {"follow_sell": True, "follow_type": "clone", "follow_clone": True}


def test_21_clone_mode_name_uses_copied_card_name():
    """clone 模式 + copied_card_name 非空 → payload name 逐字 = 复制卡俄语名
    （拉丁 draft.title 不再上卡，本地拉丁预检不再拒——复测 4/5 死因修复）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    with ExitStack() as st:
        for p in _base_patches():
            st.enter_context(p)
        output = prepare_ozon_upload_node(
            _make_prepare_state(_prepare_draft(), extensions=_CLONE_EXT,
                                copied_card_name=_RU_NAME), None, None)
    items = output.ozon_payload.get("items") or []
    assert items, "payload 必须组装成功"
    assert items[0]["name"] == _RU_NAME, (
        f"clone 模式 item name 必须逐字回读复制卡 name，实际 {items[0]['name']!r}")


def test_22_clone_mode_name_falls_back_to_draft_title():
    """clone 模式 + copied_card_name 空 → 回落 draft.title（现状语义）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    with ExitStack() as st:
        for p in _base_patches():
            st.enter_context(p)
        output = prepare_ozon_upload_node(
            _make_prepare_state(_prepare_draft(), extensions=_CLONE_EXT,
                                copied_card_name=""), None, None)
    items = output.ozon_payload.get("items") or []
    assert items and items[0]["name"] == _LATIN_NAME


def test_23_non_clone_mode_untouched_by_copied_card_name():
    """非 clone 模式零变化：copied_card_name 即使非空也不得影响 item name。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    ru_title = "Набор салфеток"
    draft = _prepare_draft(title=ru_title)
    draft.pop("ozon_product_id")  # 非 follow 卡
    with ExitStack() as st:
        for p in _base_patches():
            st.enter_context(p)
        # 图片出口硬闸/主图收口闸与本回归无关（无 AI 图必 raise），摘除以隔离变量
        st.enter_context(patch(
            "graphs.nodes.prepare_ozon_upload_node._enforce_payload_image_policy"))
        st.enter_context(patch(
            "graphs.nodes.prepare_ozon_upload_node._apply_no_primary_fallback"))
        output = prepare_ozon_upload_node(
            _make_prepare_state(draft, extensions={},
                                copied_card_name=_RU_NAME), None, None)
    items = output.ozon_payload.get("items") or []
    assert items and items[0]["name"] == ru_title, (
        f"非 clone 模式 item name 必须保持 draft 标题链，实际 {items[0]['name']!r}")


# ═══════════════ 件2: retry 环 clone 模式拉丁名快速终态 ═══════════════


def _retry_state(extensions, validation_errors, **kw):
    from graphs.validation_retry_loop import ValidationRetryLoopState
    return ValidationRetryLoopState(
        extensions=extensions, validation_errors=validation_errors, **kw)


_NAME_LATIN_ERR = f"item[0].name含拉丁字母（Ozon要求俄语名称）: {_LATIN_NAME}"


def test_31_clone_name_latin_fast_fail():
    """clone 模式 + name 拉丁错 → LOCAL_NAME_LATIN 失败终态（不进中文属性翻译
    空转 3 轮）；error_message 带人工动作指引；原文累积 decline_errors。"""
    from utils.pipeline_error_codes import LOCAL_NAME_LATIN
    from graphs.validation_retry_loop import parse_error_node
    out = parse_error_node(_retry_state(_CLONE_EXT, [_NAME_LATIN_ERR]))
    assert out.error_code == LOCAL_NAME_LATIN
    assert out.upload_status == "failed", "必须显式失败终态（_graph_result_is_failed 口径）"
    assert out.is_valid is False
    assert out.repair_node == "final_result"
    assert out.error_type == "unfixable"
    assert "竞品卡名拉丁" in out.error_message and "换西里尔名竞品" in out.error_message
    assert out.decline_errors, "消费前原文必须累积（moderation_texts 审计）"
    assert "name含拉丁" in out.decline_errors[0]["texts"]["message"]


def test_32_non_clone_name_latin_unchanged():
    """非 clone 模式现状锁定：同一错误仍走 BR_chinese_hieroglyphs_in_attribute
    （主链有翻译通道，行为零变化）。"""
    from graphs.validation_retry_loop import parse_error_node
    out = parse_error_node(_retry_state({}, [_NAME_LATIN_ERR]))
    assert out.error_code == "BR_chinese_hieroglyphs_in_attribute"
    assert out.upload_status != "failed"


def test_33_clone_description_latin_not_fast_failed():
    """clone 模式 + 仅 description 拉丁错（非 name）→ 不触发快速终态
    （判据只收 name 拉丁，其他错误各走既有链）。"""
    from graphs.validation_retry_loop import parse_error_node
    desc_err = "item[0].description含拉丁字母（Ozon要求纯俄语描述）: Beanie, Hat"
    out = parse_error_node(_retry_state(_CLONE_EXT, [desc_err]))
    assert out.error_code != "LOCAL_NAME_LATIN"


def test_34_name_latin_registered_in_routing_tables():
    """路由三表同码登记（REPAIR_STRATEGY / FIX_TYPE_UNFIXABLE / ERROR_NOTICE_MAP）。"""
    from utils.pipeline_error_codes import LOCAL_NAME_LATIN
    from graphs.validation_retry_loop import (
        ERROR_NOTICE_MAP, FIX_TYPE_UNFIXABLE, REPAIR_STRATEGY, classify_fix_type,
    )
    assert REPAIR_STRATEGY[LOCAL_NAME_LATIN] == "unfixable"
    assert LOCAL_NAME_LATIN in FIX_TYPE_UNFIXABLE
    assert classify_fix_type(LOCAL_NAME_LATIN) == "unfixable"
    assert ERROR_NOTICE_MAP[LOCAL_NAME_LATIN] == (
        "竞品卡名拉丁，克隆零 LLM 不可译——换西里尔名竞品或开单次翻译豁免")


def test_35_latin_detector_discrimination():
    """判据甄别：name 拉丁命中；description 拉丁/空槽坏标题/中文 name 不命中。"""
    from graphs.validation_retry_loop import _is_name_latin_error
    assert _is_name_latin_error(_NAME_LATIN_ERR)
    assert not _is_name_latin_error(
        "item[0].description含拉丁字母（Ozon要求纯俄语描述）: abc, def")
    assert not _is_name_latin_error(
        "item[0].name无≥4字符西里尔词（疑似单位残壳/空槽坏标题）: Вт шт")
    assert not _is_name_latin_error(
        "item[0].name含中文字符（Ozon要求俄语名称）: 冬季帽子")
    assert not _is_name_latin_error("")


# ═══════════════ 件3: Step-7 图片校验 clone 豁免 ═══════════════


def test_41_clone_mode_skips_empty_images_validation_error():
    """clone 模式零生图（无 AI 图）→ 「图片列表为空」不再误报（图走回填通道）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    with ExitStack() as st:
        for p in _base_patches():
            st.enter_context(p)
        output = prepare_ozon_upload_node(
            _make_prepare_state(_prepare_draft(), extensions=_CLONE_EXT,
                                copied_card_name=_RU_NAME), None, None)
    assert "图片列表为空（images is required）" not in output.validation_errors, (
        f"clone 模式不得误报空图，实际 {output.validation_errors}")


def test_42_non_clone_empty_images_error_unchanged():
    """非 clone 模式现状锁定：无图仍报「图片列表为空」（主图收口硬闸与本回归
    无关，摘除以隔离变量）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    draft = _prepare_draft()
    draft.pop("ozon_product_id")  # 非 follow 卡
    with ExitStack() as st:
        for p in _base_patches():
            st.enter_context(p)
        st.enter_context(patch(
            "graphs.nodes.prepare_ozon_upload_node._enforce_payload_image_policy"))
        st.enter_context(patch(
            "graphs.nodes.prepare_ozon_upload_node._apply_no_primary_fallback"))
        output = prepare_ozon_upload_node(
            _make_prepare_state(draft, extensions={}), None, None)
    assert "图片列表为空（images is required）" in output.validation_errors


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
