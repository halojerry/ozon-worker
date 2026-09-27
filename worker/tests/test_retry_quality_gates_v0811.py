# -*- coding: utf-8 -*-
"""v0.81 retry-quality 止血批（fix/retry-quality-gates）——四残余旁路闭合回归：

Fix 1 假事实兜底表清退（8050 同构残留）：
  prepare _FALLBACK_FREE_TEXT_ATTRS / retry _KNOWN_DEFAULTS_RETRY 中的
  7578/8205(保质期天数)/10350/10351(储存温度上下限)/8787(储存条件) 对任意商品
  编造「保质期 365/730 天、≤40℃/≥0℃、储存于干燥处」——清退后收敛到
  utils.attr_defaults.FACT_NEUTRAL_FREE_TEXT_DEFAULTS（唯一出口，只含事实中性
  条目 8962=1 / 9048="" / 23487=Нет бренда）。行为断言：编造键不再注入、
  中性键照常兜底。

Fix 2 retry 标题结构闸（登记 defer 闭合）：
  LLM 标题修复/强制生成产物接 sanitize_title_structure（与 prepare 同款接线）——
  残壳标题（实锤「Портативный вентилятор, Вт, скоростей」/「Вт, мл」）不再上卡：
  清洗合格采纳剔残壳产物，不合格丢弃保留原标题。

Fix 3 box_reviewed 闸扩面（「采集箱即权威、所见即所得」契约补全）：
  ① validate 级类目重配（_try_validate_recategorize）对 box_reviewed 草稿跳过，
     走既有 mismatch 拦截（类目错如实 failed）；
  ② prepare 标题链对 box_reviewed 草稿跳过自主重写（结构闸兜底/公式重生成/
     类目兜底标题），保留翻译。

Fix 4 A5 LLM 提案判别词交叉验证：
  apply_llm_schema_fill 对判别/类型属性（名含 Тип/тип/类型）唯一命中落卡前做
  _discriminant_conflict 检查——「桌面扇」标题被 LLM 提案 «Напольный» 时剥除 +
  skipped_discriminant_conflict 审计，绝不硬塞。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_retry_quality_gates_v0811.py -q
纯 mock（LLM/字典搜索/RU 路径/图片探测全 monkeypatch），无 PG/网络。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

import graphs.validation_retry_loop as vrl  # noqa: E402
from graphs.state import OzonValidateInput, PrepareOzonUploadInput  # noqa: E402
from graphs.validation_retry_loop import (  # noqa: E402
    ValidationRetryLoopState,
    error_repair_llm_node,
)
from graphs.nodes import ozon_validate_node as ovn  # noqa: E402
from graphs.nodes.ozon_validate_node import ozon_validate_node  # noqa: E402
from utils.attr_fill_extras import apply_llm_schema_fill  # noqa: E402
from utils.attr_defaults import (  # noqa: E402
    FACT_NEUTRAL_FREE_TEXT_DEFAULTS,
    _discriminant_conflict,
)

# ═══════════════ Fix 1：假事实兜底清退（retry 行为断言）═══════════════


def _repair_state(attr_id: int, attr_name: str, **over) -> ValidationRetryLoopState:
    base = dict(
        error_code="MISSING_REQUIRED_ATTRIBUTE",
        attribute_id=attr_id,
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id="17028746", type_id="92780",
        product_name="Тестовый товар для проверки",
        ozon_payload={"items": [
            {"name": "Оригинальный заголовок товара", "offer_id": "x", "price": "100"}
        ]},
        final_attributes=[{"id": attr_id, "value": "", "dictionary_value_id": 0}],
        attributes_schema=[{"id": attr_id, "name": attr_name, "dictionary_id": 0}],
        dictionary_values={},
    )
    base.update(over)
    return ValidationRetryLoopState(**base)


@pytest.fixture()
def _repair_mocks(monkeypatch):
    """error_repair_llm_node 全 mock：schema 拉空（回退 state.attributes_schema）、
    LLM 返回空（Step 3 无修复）、Ozon API 空。"""
    monkeypatch.setattr(vrl, "_get_attribute_schema", lambda *a, **k: [])
    monkeypatch.setattr(vrl, "_call_mxou_llm", lambda *a, **k: "")
    monkeypatch.setattr(vrl, "_call_ozon_api", lambda *a, **k: {"result": []})


@pytest.mark.usefixtures("_repair_mocks")
class TestFabricatedDefaultsRetired:
    """编造事实键（7578/8205/10350/10351/8787）不再被已知默认表注入。"""

    _CASES = [
        (7578, "Срок годности (дни)", "365"),
        (8205, "Срок годности в днях", "730"),
        (10350, "Макс. температура хранения", "40"),
        (10351, "Мин. температура хранения", "0"),
        (8787, "Условия хранения", "сухое место"),
    ]

    @pytest.mark.parametrize("attr_id,attr_name,banned", _CASES)
    def test_no_fabricated_value_injected(self, attr_id, attr_name, banned):
        state = _repair_state(attr_id, attr_name)
        out = error_repair_llm_node(state)
        attr = next(a for a in out.final_attributes if a.get("id") == attr_id)
        assert str(attr.get("value", "")) != banned, (
            f"属性{attr_id} 不得再注入编造默认值 {banned!r}"
        )
        assert not str(attr.get("value", "")).strip(), (
            f"属性{attr_id} 编造默认清退后应保持缺失（诚实失败），实际 {attr.get('value')!r}"
        )

    def test_shared_whitelist_shape(self):
        """唯一出口白名单只含事实中性条目；编造键禁入（8050 同构防线）。"""
        assert FACT_NEUTRAL_FREE_TEXT_DEFAULTS.get(8962) == "1"
        assert FACT_NEUTRAL_FREE_TEXT_DEFAULTS.get(9048) == ""
        assert FACT_NEUTRAL_FREE_TEXT_DEFAULTS.get(23487) == "Нет бренда"
        for banned_id in (7578, 8205, 10350, 10351, 8787, 8050):
            assert banned_id not in FACT_NEUTRAL_FREE_TEXT_DEFAULTS

    def test_discriminant_conflict_helper_shared(self):
        """Fix 4 复用同一辅助函数（attr_defaults 唯一事实源）：桌面标题 × 落地值 = 冲突。"""
        assert _discriminant_conflict("桌面风扇 迷你", "Напольный вентилятор") is True
        assert _discriminant_conflict("桌面风扇 迷你", "Настольный вентилятор") is False
        assert _discriminant_conflict("настольный вентилятор", "Напольный") is True
        # 无判别词源 → 不误伤
        assert _discriminant_conflict("увлажнитель воздуха", "Напольный") is False


@pytest.mark.usefixtures("_repair_mocks")
class TestFactNeutralDefaultsStillWork:
    """事实中性键（8962/23487）兜底行为不回归。"""

    def test_8962_count_one_still_filled(self):
        out = error_repair_llm_node(_repair_state(8962, "Количество предметов"))
        attr = next(a for a in out.final_attributes if a.get("id") == 8962)
        assert attr["value"] == "1", "8962 件数=1 对单件商品是事实，兜底保留"
        assert int(attr.get("dictionary_value_id") or 0) == 0

    def test_23487_no_brand_fallback(self):
        out = error_repair_llm_node(_repair_state(23487, "Производитель"))
        attr = next(a for a in out.final_attributes if a.get("id") == 23487)
        assert attr["value"] == "Нет бренда"

    def test_23487_supplier_preferred(self):
        state = _repair_state(
            23487, "Производитель", draft={"supplier": "Компания Иу"})
        out = error_repair_llm_node(state)
        attr = next(a for a in out.final_attributes if a.get("id") == 23487)
        assert attr["value"] == "Компания Иу"


# ═══════════════ Fix 2：retry 标题结构闸 ═══════════════

_ORIG_TITLE = "Оригинальный заголовок товара"


def _title_repair_state(llm_answer: str, error_code: str = "INVALID_ATTRIBUTE_VALUE"):
    st = _repair_state(9999, "Тест", error_code=error_code)
    st.final_attributes = [
        {"id": 9999, "value": "", "dictionary_value_id": 0},
        {"id": 4180, "value": _ORIG_TITLE, "dictionary_value_id": 0},
    ]
    return st


class TestRetryTitleStructureGate:
    def test_llm_shell_title_discarded_keeps_original(self, monkeypatch):
        """LLM 重生成纯残壳标题（Вт, мл — 无 ≥4 字符西里尔词）→ 结构闸丢弃，
        items[0].name/4180 保留原标题（不再上卡）。"""
        monkeypatch.setattr(vrl, "_get_attribute_schema", lambda *a, **k: [])
        monkeypatch.setattr(
            vrl, "_call_mxou_llm",
            lambda *a, **k: json.dumps(
                {"corrected_title": "Вт, мл, шт", "repair_explanation": "x"},
                ensure_ascii=False))
        monkeypatch.setattr(vrl, "_call_ozon_api", lambda *a, **k: {"result": []})
        out = error_repair_llm_node(_title_repair_state(""))
        assert out.ozon_payload["items"][0]["name"] == _ORIG_TITLE, (
            f"残壳标题必须被丢弃保留原标题: {out.ozon_payload['items'][0]['name']!r}"
        )
        _a4180 = next(a for a in out.final_attributes if a.get("id") == 4180)
        assert _a4180["value"] == _ORIG_TITLE

    def test_llm_shell_segments_stripped_and_adopted(self, monkeypatch):
        """LLM 标题含单位残壳段（…, Вт, скоростей）→ 闸清洗合格 → 采纳剔残壳
        产物（与 prepare 同语义），残壳段不上卡。"""
        monkeypatch.setattr(vrl, "_get_attribute_schema", lambda *a, **k: [])
        monkeypatch.setattr(
            vrl, "_call_mxou_llm",
            lambda *a, **k: json.dumps(
                {"corrected_title": "Портативный вентилятор, Вт, скоростей",
                 "repair_explanation": "x"}, ensure_ascii=False))
        monkeypatch.setattr(vrl, "_call_ozon_api", lambda *a, **k: {"result": []})
        out = error_repair_llm_node(_title_repair_state(""))
        assert out.ozon_payload["items"][0]["name"] == "Портативный вентилятор, скоростей"

    def test_forced_gen_shell_title_discarded(self, monkeypatch):
        """强制生成路径同样过闸：UNKNOWN + LLM 无标题 → 强制生成返回残壳 → 丢弃，
        保留原标题（repair_type 不置 title，name 不动）。"""
        monkeypatch.setattr(vrl, "_get_attribute_schema", lambda *a, **k: [])
        monkeypatch.setattr(
            vrl, "_call_mxou_llm",
            lambda *a, **k: json.dumps({"repair_explanation": "no title"},
                                       ensure_ascii=False))
        monkeypatch.setattr(vrl, "_call_ozon_api", lambda *a, **k: {"result": []})
        monkeypatch.setattr("utils.mxou_api.call_mxou_chat_api",
                            lambda *a, **k: "Вт, мл")
        out = error_repair_llm_node(_title_repair_state("", error_code="UNKNOWN"))
        assert out.ozon_payload["items"][0]["name"] == _ORIG_TITLE

    def test_forced_gen_good_title_still_applied(self, monkeypatch):
        """正面对照：强制生成合格标题照常应用（闸不误杀正常重生成）。"""
        monkeypatch.setattr(vrl, "_get_attribute_schema", lambda *a, **k: [])
        monkeypatch.setattr(
            vrl, "_call_mxou_llm",
            lambda *a, **k: json.dumps({"repair_explanation": "no title"},
                                       ensure_ascii=False))
        monkeypatch.setattr(vrl, "_call_ozon_api", lambda *a, **k: {"result": []})
        monkeypatch.setattr("utils.mxou_api.call_mxou_chat_api",
                            lambda *a, **k: "Настольный вентилятор для дома")
        out = error_repair_llm_node(_title_repair_state("", error_code="UNKNOWN"))
        assert out.ozon_payload["items"][0]["name"] == "Настольный вентилятор для дома"

    def test_box_reviewed_title_path_untouched(self, monkeypatch):
        """box_reviewed 草稿：LLM 标题重写在上游已被清空（:2020 既有闸），
        结构闸不得改变该契约——原标题保持。"""
        monkeypatch.setattr(vrl, "_get_attribute_schema", lambda *a, **k: [])
        monkeypatch.setattr(
            vrl, "_call_mxou_llm",
            lambda *a, **k: json.dumps(
                {"corrected_title": "Вт, мл", "repair_explanation": "x"},
                ensure_ascii=False))
        monkeypatch.setattr(vrl, "_call_ozon_api", lambda *a, **k: {"result": []})
        st = _title_repair_state("", error_code="UNKNOWN")
        st.extensions = {"box_reviewed": True}
        out = error_repair_llm_node(st)
        assert out.ozon_payload["items"][0]["name"] == _ORIG_TITLE


# ═══════════════ Fix 3①：validate 级重配对 box_reviewed 跳过 ═══════════════

_DC, _TP = 17028001, 91001
_POLKA_PATH = "Дом > Мебель > Полка"
_NEW_DC, _NEW_TP = 999, 888
_HOLDLE_PATH = "Стройматериалы > Сантехника > Держатель для душа"


class _Resp:
    def __init__(self, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = False
        self.is_permanent_redirect = False


def _vitem(**over):
    base = {
        "name": "Держатель для душа угловой", "offer_id": "sku1", "price": "1990",
        "old_price": "2390", "vat": "0", "weight": 300, "weight_unit": "g",
        "depth": 100, "width": 100, "height": 50, "dimension_unit": "mm",
        "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"],
        "primary_image": "https://example.com/img.jpg",
        "description_category_id": _DC, "type_id": _TP,
        "attributes": [],
    }
    base.update(over)
    return base


def _run_validate(items, extensions=None):
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c", ozon_api_key="k",
        attributes_schema=[],
        extensions=extensions,
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """图片探测 200 + RU 路径按表返回（不依赖 PG/网络）。"""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port=None, *a, **k:
        [(2, 1, 6, "", ("93.184.216.34", port or 0))])
    monkeypatch.setattr("requests.request", lambda *a, **k: _Resp())

    def _fake_ru_path(dc, tp):
        if (dc, tp) == (_DC, _TP):
            return _POLKA_PATH
        if (dc, tp) == (_NEW_DC, _NEW_TP):
            return _HOLDLE_PATH
        return ""
    monkeypatch.setattr(ovn, "_fetch_ru_category_path", _fake_ru_path, raising=False)


def _mismatch_errors(out):
    return [e for e in out.validation_errors if "标题与类目不一致" in e]


class TestValidateRecategorizeBoxReviewed:
    def test_box_reviewed_skips_recategorize(self, monkeypatch):
        """box_reviewed 草稿：零交集时跳过 validate 级重配（不触树搜索）→
        走既有 mismatch 拦截，(dc,tp) 不动（类目错如实 failed）。"""
        search_mock = patch(
            "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
            return_value=[
                {"description_category_id": _NEW_DC, "type_id": _NEW_TP,
                 "node_name": "Держатель для душа", "full_path": _HOLDLE_PATH,
                 "similarity": 0.567}])
        with search_mock as mk:
            out = _run_validate([_vitem()], extensions={"box_reviewed": True})
        errs = _mismatch_errors(out)
        assert errs, f"box_reviewed 草稿必须如实拦截: {out.validation_errors}"
        assert out.is_valid is False
        _it = out.ozon_payload["items"][0]
        assert _it["description_category_id"] == _DC and _it["type_id"] == _TP, (
            f"box_reviewed 草稿 (dc,tp) 不得被改写: {_it}"
        )
        assert mk.called is False, "重配被闸短路时不得触发树搜索"

    def test_non_box_reviewed_recategorize_unaffected(self, monkeypatch):
        """对照组：无 box_reviewed → 重配照常命中（Fix 3① 不收窄既有救场面）。"""
        monkeypatch.setattr(
            "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
            lambda self, q, **k: [
                {"description_category_id": _NEW_DC, "type_id": _NEW_TP,
                 "node_name": "Держатель для душа", "full_path": _HOLDLE_PATH,
                 "similarity": 0.567}])
        out = _run_validate([_vitem()])
        assert not _mismatch_errors(out), f"既有重配救场不得回归: {out.validation_errors}"
        assert out.is_valid is True
        _it = out.ozon_payload["items"][0]
        assert _it["description_category_id"] == _NEW_DC
        assert _it["type_id"] == _NEW_TP


# ═══════════════ Fix 3②：prepare 标题链对 box_reviewed 跳过重写 ═══════════════

def _prepare_draft(**over):
    d = {
        "item_id": "testboxtitle",
        "title": "保温杯",  # 中文标题 → 翻译分支（翻译对 box_reviewed 保留）
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {},
        "sku_id": "testboxtitle",
        "price": "1290",
        "original_price": "1490",
        "ozon_category": {"description_category_id": str(_DC), "type_id": str(_TP),
                          "source": "page"},
    }
    d.update(over)
    return d


def _make_prepare_state(draft, extensions=None):
    return PrepareOzonUploadInput(
        draft=draft,
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info={"price": 1290, "old_price": 1490, "variant_prices": []},
        description_category_id=str(_DC),
        type_id=str(_TP),
        final_attributes=[],
        attributes_schema=[],
        dictionary_values={},
        token="sk-test",
        original_images=draft["images"],
        main_image="https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/ai-main.jpg",
        extensions=extensions or {},
    )


def _prepare_patches(translation="Вт, мл"):
    """对齐 test_validate_category_source_v078 手法：翻译产出「结构残壳」标题
    （Вт, мл），公式生成/chat 全空，类目兜底标题返回固定值——观察 title_ru 去向。"""
    return [
        patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description",
              return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title",
              return_value="Товар для дома"),
        patch("utils.mxou_api.call_mxou_chat_api", return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
              return_value=translation),
    ]


def _run_prepare(draft, extensions=None, translation="Вт, мл"):
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    with ExitStack() as st:
        for p in _prepare_patches(translation):
            st.enter_context(p)
        return prepare_ozon_upload_node(
            _make_prepare_state(draft, extensions), None, None)


class TestPrepareTitleChainBoxReviewed:
    def test_box_reviewed_title_not_rewritten(self):
        """box_reviewed 草稿：翻译保留（必要转换），但残壳标题不再流入公式重生成/
        类目兜底——标题保持翻译产物，重写交 validate 诚实拦截。"""
        out = _run_prepare(_prepare_draft(), extensions={"box_reviewed": True})
        name = out.ozon_payload["items"][0]["name"]
        assert name == "Вт, мл", (
            f"box_reviewed 草稿标题不得被重写为兜底文案，实际 {name!r}"
        )

    def test_non_box_reviewed_title_regen_still_runs(self):
        """对照组：无 box_reviewed → 残壳标题照常流入重生成链（公式生成 mock 空 →
        类目兜底标题），Fix 3② 不收窄既有坏标题自救面。"""
        out = _run_prepare(_prepare_draft())
        name = out.ozon_payload["items"][0]["name"]
        assert name == "Товар для дома", (
            f"非 box_reviewed 残壳标题必须照常走兜底链，实际 {name!r}"
        )

    def test_box_reviewed_keeps_translation(self):
        """翻译步骤对 box_reviewed 保留：中文标题翻译成俄语（必要转换非重写）。"""
        out = _run_prepare(_prepare_draft(), extensions={"box_reviewed": True},
                           translation="Термос из нержавеющей стали")
        name = out.ozon_payload["items"][0]["name"]
        assert name == "Термос из нержавеющей стали", (
            f"box_reviewed 草稿的翻译步骤必须保留，实际 {name!r}"
        )


# ═══════════════ Fix 4：A5 LLM 提案判别词交叉验证 ═══════════════

def _a5_state():
    return SimpleNamespace(
        description_category_id="1", type_id="2",
        ozon_client_id="c", ozon_api_key="k",
        user_id="tenant-1", task_id="task-a5", token="tok",
    )


_A5_TODO_FAN = [{
    "id": 1225, "name": "Тип вентилятора", "desc": "", "type": "String",
    "dict": 99, "collection": False,
}]


class TestA5DiscriminantCrossCheck:
    def test_desktop_title_floor_proposal_skipped(self):
        """历史事故复现面：「桌面扇」标题 + LLM 提案 «Напольный» + 字典唯一命中 →
        判别词冲突必须剥除，绝不落卡。"""
        items = [{"attributes": [], "depth": 100, "width": 100, "height": 50}]
        raw = json.dumps({"fills": [{"id": 1225, "value": "Напольный"}]},
                         ensure_ascii=False)
        draft = {"title": "桌面风扇 USB迷你风扇", "attributes": {"类型": "桌面"}}
        audit: list = []
        with patch("utils.ozon_dict_values.search_dictionary_values",
                   return_value=[{"id": 11, "value": "Напольный вентилятор"}]), \
             patch("utils.attr_match_log.log_attr_match",
                   side_effect=lambda **kw: audit.append(kw)):
            out = apply_llm_schema_fill(items, _A5_TODO_FAN, raw, draft,
                                        _a5_state(), audit_task_id="task-a5")
        assert out[0]["attributes"] == [], (
            f"判别词冲突提案不得落卡: {out[0]['attributes']}"
        )
        skips = [a for a in audit
                 if a.get("status") == "skipped_discriminant_conflict"]
        assert skips, "冲突剥除必须打 skipped_discriminant_conflict 审计"
        assert skips[0]["attr_id"] == 1225

    def test_desktop_title_desktop_proposal_fills(self):
        """正面对照：标题桌面形态 + 提案 «Настольный»（字典命中形态一致）→ 照常落卡。"""
        items = [{"attributes": [], "depth": 100, "width": 100, "height": 50}]
        raw = json.dumps({"fills": [{"id": 1225, "value": "Настольный"}]},
                         ensure_ascii=False)
        draft = {"title": "桌面风扇 USB迷你风扇", "attributes": {}}
        with patch("utils.ozon_dict_values.search_dictionary_values",
                   return_value=[{"id": 22, "value": "Настольный вентилятор"}]):
            out = apply_llm_schema_fill(items, _A5_TODO_FAN, raw, draft,
                                        _a5_state(), audit_task_id="task-a5")
        attrs = out[0]["attributes"]
        assert len(attrs) == 1 and attrs[0]["id"] == 1225
        assert int(attrs[0]["values"][0]["dictionary_value_id"]) == 22

    def test_non_type_attr_not_gated(self):
        """非判别属性（材质）不做判别闸——标题含「桌面」不误伤材质等正常提案。"""
        items = [{"attributes": [], "depth": 100, "width": 100, "height": 50}]
        todo = [{"id": 6783, "name": "Материал", "desc": "", "type": "String",
                 "dict": 88, "collection": False}]
        raw = json.dumps({"fills": [{"id": 6783, "value": "Пластик"}]},
                         ensure_ascii=False)
        draft = {"title": "桌面风扇 USB迷你风扇", "attributes": {}}
        with patch("utils.ozon_dict_values.search_dictionary_values",
                   return_value=[{"id": 5, "value": "Пластик"}]):
            out = apply_llm_schema_fill(items, todo, raw, draft,
                                        _a5_state(), audit_task_id="task-a5")
        attrs = out[0]["attributes"]
        assert len(attrs) == 1 and attrs[0]["id"] == 6783, (
            f"非判别属性不得被闸误伤: {attrs}"
        )

    def test_no_evidence_no_conflict(self):
        """标题/已知属性零判别词时提案照常落卡（闸只拦「明确冲突」，不拦无证据）。"""
        items = [{"attributes": [], "depth": 100, "width": 100, "height": 50}]
        raw = json.dumps({"fills": [{"id": 1225, "value": "Напольный"}]},
                         ensure_ascii=False)
        draft = {"title": "电风扇 落地家用", "attributes": {}}
        with patch("utils.ozon_dict_values.search_dictionary_values",
                   return_value=[{"id": 11, "value": "Напольный вентилятор"}]):
            out = apply_llm_schema_fill(items, _A5_TODO_FAN, raw, draft,
                                        _a5_state(), audit_task_id="task-a5")
        attrs = out[0]["attributes"]
        assert len(attrs) == 1, f"证据一致时不得剥除: {attrs}"
