"""批C Q7（v0.78 fix/guard-precision-v1）— 权威类目来源豁免零交集预检（TDD RED→GREEN）。

背景（「前门豁免后门杀」误伤实锤）：assemble 侧一致性检查对权威来源
（page/what_to_sell/manual/mapping → match_layer=Skill，`_is_skill_authoritative`）
豁免；ozon_validate 的标题-类目零交集预检（v0.69 T2.1）却从不看类目来源——
权威来源（面包屑=竞品在售真实类目=最高信任源）被零交集闸杀，盆/篮/筛家族
8+ 卡误伤。

修复契约：
  Q7-1  `OzonValidateInput` 新增 `category_source: str = ""`（langgraph channel
        过滤防回归：`"category_source" in OzonValidateInput.model_fields`）。
  Q7-2  prepare 侧计算：`category_match_meta.match_layer == "Skill"` 或
        `draft.ozon_category.source ∈ {page, mapping, what_to_sell, manual}`
        → category_source="authoritative"，否则空串。经 PrepareOzonUploadOutput
        → GlobalState → OzonValidateInput 每一跳声明（AGENTS「input schema 纪律」）。
  Q7-3  validate 预检：`category_source == "authoritative"` 时零交集**降级为
        warning**（带来源+标题头40字+类目路径头80字留痕）不进 item_errors；
        非权威来源行为逐字保持（错误文案含「标题与类目不一致」——retry 子图按该
        关键字归 LOCAL_TITLE_CATEGORY_MISMATCH 入箱）；UPDATE（product_id）与
        缺 RU 路径的既有豁免不动。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_validate_category_source_v078.py -q
纯 mock（RU 路径 + LLM 全 monkeypatch），无需 PG/网络。
"""
import logging
import os
import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from graphs.state import (  # noqa: E402
    OzonValidateInput,
    PrepareOzonUploadOutput,
)
from graphs.nodes import ozon_validate_node as ovn  # noqa: E402
from graphs.nodes.ozon_validate_node import ozon_validate_node  # noqa: E402

# 固定西里尔类目路径（monkeypatch _fetch_ru_category_path，完全离线）
_DC, _TP = 17028653, 92147
_RU_PATH = "Строительство и ремонт > Инструменты для ремонта и строительства > Трещотка"
_ZERO_OVERLAP_TITLE = "Носки женские теплые"  # 与 Трещотка 零公共西里尔词


class _Resp:
    def __init__(self, status=200):
        self.status_code = status


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """默认：图片探测 200、RU 路径固定西里尔（不依赖 PG/网络）。"""
    monkeypatch.setattr("requests.head", lambda *a, **k: _Resp(200), raising=False)
    monkeypatch.setattr(ovn, "_fetch_ru_category_path",
                        lambda dc, tp: _RU_PATH if (dc, tp) == (_DC, _TP) else "",
                        raising=False)


def _run_validate(items, category_source=None):
    """构造 OzonValidateInput 跑 validate 节点（category_source=None=不传，走默认）。"""
    kwargs = {}
    if category_source is not None:
        kwargs["category_source"] = category_source
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c",
        ozon_api_key="k",
        attributes_schema=[],
        **kwargs,
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


def _item(**over):
    base = {
        "name": _ZERO_OVERLAP_TITLE, "offer_id": "sku1", "price": "1990",
        "old_price": "2390", "vat": "0", "weight": 300, "weight_unit": "g",
        "depth": 100, "width": 100, "height": 50, "dimension_unit": "mm",
        "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"],
        "primary_image": "https://example.com/img.jpg",
        "description_category_id": _DC, "type_id": _TP,
        "attributes": [],
    }
    base.update(over)
    return base


# ═══════════════ Q7-1：channel 声明防回归 ═══════════════

def test_category_source_declared_in_validate_input():
    """①category_source 必须声明进 OzonValidateInput——否则 langgraph 按节点
    Input model 过滤 channel，prepare 写了 validate 也拿不到（静默空串）。"""
    assert "category_source" in OzonValidateInput.model_fields, (
        "OzonValidateInput 缺 category_source 字段 → channel 过滤会静默剥掉，"
        "权威豁免永不生效（AGENTS「input schema 纪律」红线）"
    )


def test_category_source_declared_in_prepare_output():
    """②prepare 输出模型必须声明 category_source（prepare→validate 的第一跳）。"""
    assert "category_source" in PrepareOzonUploadOutput.model_fields


def test_category_source_full_channel_chain_declared():
    """②b全跳声明锁定（简报硬约束 7：漏一跳被 langgraph channel 静默过滤）——
    GlobalState 必须有 category_source（prepare Output 写入的目标 channel，
    缺了写入静默丢弃 → validate 恒空串 → 豁免永不生效）；
    PrepareOzonUploadInput 必须有 category_match_meta（assemble→prepare 读跳，
    缺了 prepare 恒拿不到 match_layer → 第一判定分支失效）。"""
    from graphs.state import GlobalState, PrepareOzonUploadInput
    assert "category_source" in GlobalState.model_fields, (
        "GlobalState 缺 category_source → prepare Output 写入被 channel 静默丢弃"
    )
    assert "category_match_meta" in PrepareOzonUploadInput.model_fields, (
        "PrepareOzonUploadInput 缺 category_match_meta → prepare 读不到 assemble 的 "
        "match_layer，权威判定第一分支（match_layer=Skill）恒失效"
    )


# ═══════════════ Q7-3：validate 预检权威豁免 ═══════════════

def test_authoritative_zero_overlap_downgraded_to_warning(caplog):
    """③权威来源 + 零交集 → 不进 item_errors（不阻断），仅 warning 留痕
    （带来源 + 标题 + 类目路径，可查）。"""
    with caplog.at_level(logging.WARNING, logger="graphs.nodes.ozon_validate_node"):
        out = _run_validate([_item()], category_source="authoritative")
    assert not any("标题与类目不一致" in e for e in out.validation_errors), (
        f"权威来源零交集必须降级放行，实际仍拦截: {out.validation_errors}"
    )
    assert out.is_valid is True, "权威来源零交集降级后不得阻断"
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "零交集" in r.getMessage()]
    assert warns, "降级必须有 warning 留痕（宁放行不留痕=静默，排查不可得）"
    msg = warns[0].getMessage()
    assert "authoritative" in msg, f"warning 需带来源标记: {msg}"
    assert _ZERO_OVERLAP_TITLE[:40] in msg, f"warning 需带标题头40字: {msg}"
    assert _RU_PATH[:80] in msg, f"warning 需带类目路径头80字: {msg}"


def test_non_authoritative_zero_overlap_still_blocks():
    """④非权威来源（category_source=""）零交集 → 仍追加错误逐字保持——错误文案
    含「标题与类目不一致」（retry 子图按该关键字归 LOCAL_TITLE_CATEGORY_MISMATCH
    入箱，v0.73 拦截语义不受本批影响）。"""
    out = _run_validate([_item()], category_source="")
    errs = [e for e in out.validation_errors if "标题与类目不一致" in e]
    assert errs, f"非权威零交集必须仍拦截: {out.validation_errors}"
    assert "DESCRIPTION_DECLINE 风险" in errs[0], f"文案逐字保持: {errs[0]}"
    assert out.is_valid is False


def test_default_category_source_empty_blocks():
    """⑤不传 category_source（默认空串=非权威/未知）→ 行为与现状一致仍拦截
    （向后兼容：旧路径/直接调用零变化）。"""
    out = _run_validate([_item()])
    assert any("标题与类目不一致" in e for e in out.validation_errors)
    assert out.is_valid is False


def test_update_product_id_exempt_regression():
    """⑥UPDATE（带 product_id）豁免回归——既有豁免不动（权威/非权威均不检）。"""
    out = _run_validate([_item(name=_ZERO_OVERLAP_TITLE, product_id=123456)],
                        category_source="")
    assert not any("标题与类目不一致" in e for e in out.validation_errors)
    assert out.is_valid is True


def test_missing_ru_path_still_skips(monkeypatch):
    """⑦RU 路径缺失豁免回归——非权威来源也跳过（宁松勿严不因数据缺失误拦）。"""
    monkeypatch.setattr(ovn, "_fetch_ru_category_path", lambda dc, tp: "")
    out = _run_validate([_item()], category_source="")
    assert not any("标题与类目不一致" in e for e in out.validation_errors)
    assert out.is_valid is True


# ═══════════════ Q7-2：prepare 侧来源判定 ═══════════════

# 纯函数：来源判定唯一事实源（validate 侧不做重复推断，只消费 prepare 结论）

def test_resolve_authoritative_by_match_layer_skill():
    """⑧match_layer=Skill（assemble 对 page/what_to_sell/manual/mapping 定稿标记）
    → authoritative。"""
    from graphs.nodes.prepare_ozon_upload_node import _resolve_category_source
    assert _resolve_category_source({"match_layer": "Skill"}, {}) == "authoritative"


def test_resolve_authoritative_by_draft_source_whitelist():
    """⑨draft.ozon_category.source ∈ {page, mapping, what_to_sell, manual} →
    authoritative（含 manual 语义；meta 缺失时靠信封兜底判定）。"""
    from graphs.nodes.prepare_ozon_upload_node import _resolve_category_source
    for src in ("page", "mapping", "what_to_sell", "manual"):
        draft = {"ozon_category": {"description_category_id": _DC, "type_id": _TP,
                                   "source": src}}
        assert _resolve_category_source({}, draft) == "authoritative", f"source={src}"


def test_resolve_non_authoritative_sources():
    """⑩search_kw（关键词模糊）/ 空 meta / 空 draft / widget（顾客空间 ID 可能非
    权威，简报白名单不含）→ 空串（非权威，零交集照拦）。"""
    from graphs.nodes.prepare_ozon_upload_node import _resolve_category_source
    assert _resolve_category_source({}, {"ozon_category": {"source": "search_kw"}}) == ""
    assert _resolve_category_source({}, None) == ""
    assert _resolve_category_source(None, None) == ""
    assert _resolve_category_source({}, {"ozon_category": {"source": "widget"}}) == ""
    assert _resolve_category_source({"match_layer": "L1"}, {}) == ""
    assert _resolve_category_source({"match_layer": "R2b"}, {}) == ""


# ── prepare 节点接线（mock 组装产物，不真跑全图）──

def _prepare_draft(**over):
    d = {
        "item_id": "testcatsrc",
        "title": "Набор салфеток",  # 俄语标题，避免标题翻译分支
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {},
        "sku_id": "testcatsrc",
        "price": "1290",
        "original_price": "1490",
        "ozon_category": {"description_category_id": str(_DC), "type_id": str(_TP),
                          "source": "page"},
    }
    d.update(over)
    return d


def _make_prepare_state(draft, category_match_meta=None):
    from graphs.state import PrepareOzonUploadInput
    return PrepareOzonUploadInput(
        draft=draft,
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        # price/old_price 键是 prepare 真正读取的定价字段（:1926 pricing_info.get("price")）
        pricing_info={"price": 1290, "old_price": 1490, "variant_prices": []},
        description_category_id=str(_DC),
        type_id=str(_TP),
        final_attributes=[],
        attributes_schema=[],
        dictionary_values={},
        token="sk-test",
        original_images=draft["images"],
        main_image="https://img.test/ai-main.jpg",  # AI 主图 → 营销图非空 → 走成功出口
        category_match_meta=category_match_meta or {},
    )


def _base_patches():
    """统一 mock：LLM 富文本/标题兜底/mxou chat（防网络）——对齐
    test_numeric_attr_sanitize_v069 的 prepare 节点 mock 手法。"""
    return [
        patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title", return_value="Товар для дома"),
        patch("utils.mxou_api.call_mxou_chat_api", return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm", return_value=""),
    ]


@contextmanager
def _patches(*extra):
    with ExitStack() as st:
        for p in _base_patches() + list(extra):
            st.enter_context(p)
        yield


def test_prepare_outputs_authoritative_for_page_source():
    """⑪权威信封（draft.ozon_category.source=page）→ prepare 输出 state 的
    category_source == "authoritative"（不真跑全图，mock 组装产物）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    with _patches():
        output = prepare_ozon_upload_node(_make_prepare_state(_prepare_draft()), None, None)
    assert output.category_source == "authoritative", (
        f"page 权威来源必须输出 authoritative，实际 {output.category_source!r}"
    )


def test_prepare_outputs_authoritative_for_match_layer_skill():
    """⑫category_match_meta.match_layer=Skill（信封 source 缺失时）→
    prepare 输出 authoritative。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    draft = _prepare_draft()
    draft.pop("ozon_category")
    with _patches():
        output = prepare_ozon_upload_node(
            _make_prepare_state(draft, category_match_meta={"match_layer": "Skill"}),
            None, None)
    assert output.category_source == "authoritative"


def test_prepare_outputs_empty_for_non_authoritative():
    """⑬search_kw 信封 → prepare 输出空串（零交集照拦，Q7 只收窄触发面）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    draft = _prepare_draft(ozon_category={"description_category_id": str(_DC),
                                          "type_id": str(_TP), "source": "search_kw"})
    with _patches():
        output = prepare_ozon_upload_node(
            _make_prepare_state(draft, category_match_meta={"match_layer": "L1"}),
            None, None)
    assert output.category_source == "", (
        f"search_kw 非权威必须输出空串，实际 {output.category_source!r}"
    )


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
