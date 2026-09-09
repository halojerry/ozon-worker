"""v0.69 P-B + P-D(worker 半) — follow_sell_import_node 类目门控仲裁 + discover 变体跳过 import-by-sku。

生产取证（archive/docs/legacy/TEST-v067-wave-plan.md Wave D，10 单实锤）：
  - discover 信封（follow_sell=True 无 follow_type）的类目解析发生在本节点内部，
    完全绕过 assemble 的 R2b 四段闸/vision/R1：pg_trgm sim≥0.5 直采（俄语源词
    「Тепловое оборудование」→ 医用 Рециркулятор sim=0.500 恰好过线）、1688 类目
    jieba 单字「取暖」sim=0.70 → 配件类。三单跨域错放全经此通道。
  - 面包屑 category_path 的 ID 是 Web 前台 ID（非 Seller dc），get_node_by_full_path
    精确匹配永远不中——但路径末段词是有效的名称搜索先验。
  - hand 模式类目解析失败自动降级 import-by-sku：真实写请求 + 每单 180s 白等，
    discover 场景有百害无一利。

修复契约：
  P-B  ①保留高置信直采：_verify_category_schema 200 / 确定性树命中（路径精配+唯一 type）；
       ②其余模糊场景（pg_trgm ILIKE / 1688 jieba 兜底直采）改为「候选池 → R1 剔除 →
         R2b 仲裁池 → LLM vision 仲裁 → _r2b_confirm_adoption 四段判定 → R1 定稿 veto」，
         任一环不过 → 类目置空，绝不保底直采；单字核心 token 直接不搜；
       ③真 follow_type=api 的 import-by-sku 复制带回类目语义不变。
  P-D  follow_type="discover" 绝不 import-by-sku/api 复制；类目解析失败置空继续走管
       （不写「类目解析失败」触发词 → 不进 retry，由 assemble 全闸链定稿）；
       旧信封（无 follow_type）保持 hand 语义零变化。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_follow_discover_gate_v069.py -q
全部纯 mock（LLM/PG/HTTP 全 mock），不触网。
"""
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import graphs.nodes.follow_sell_import_node as fsin
import utils.attr_defaults as attr_defaults_mod
import utils.category_mapping_learn as cml_mod
import utils.ozon_category_query as ocq_mod
from graphs.nodes import assemble_ozon_product_node as asm

# ── wave D 取证场景锚（面包屑 = Web 前台空间，dc 非 Seller 树 ID）──
_BREADCRUMB = "Для дома > Отопление > Тепловое оборудование"
_WIDGET_DC = "144713251"
_DISCOVER_DRAFT = {
    "ozon_product_id": "1543201447",
    "title": "Тепловая завеса электрическая 2000Вт обогреватель",
    "images": ["https://cdn.ozon.ru/img1.jpg"],
    "ozon_category": {
        "description_category_id": _WIDGET_DC,
        "type_id": "",
        "category_path": _BREADCRUMB,
    },
    "competitor_price": "2490.00",
    "purchase_cost": 85.0,
    "purchase_url": "https://detail.1688.com/offer/123.html",
    "currency": "CNY",
    "weight": 3000,
    "dimensions": {"length": 200, "width": 150, "height": 120},
    "item_id": "12345678",
}


class FakeState:
    """模拟 GlobalState — 只含 follow_sell_import_node 读取的字段。"""

    def __init__(self, envelope, ozon_client_id="123", ozon_api_key="key",
                 currency_code="CNY", token="sk-test"):
        self.envelope = envelope
        self.ozon_client_id = ozon_client_id
        self.ozon_api_key = ozon_api_key
        self.currency_code = currency_code
        self.token = token


def _discover_envelope(follow_type="discover", draft_extra=None):
    draft = dict(_DISCOVER_DRAFT)
    if draft_extra:
        draft.update(draft_extra)
    ext = {"follow_sell": True}
    if follow_type is not None:
        ext["follow_type"] = follow_type
    return {"draft": draft, "extensions": ext}


class _PostRecorder:
    """follow_sell_import_node.ozon_post 打桩：计数 import-by-sku / api 写调用，
    其余 200 空结果。（F-F01: transport 收敛后 patch 点从 requests.post 切来）"""

    def __init__(self, import_unmatched=False, import_info_product=False):
        self.calls = []
        self.import_unmatched = import_unmatched
        self.import_info_product = import_info_product

    def __call__(self, client_id, api_key, endpoint, body=None, timeout=30, **kw):
        self.calls.append(endpoint)
        if "import-by-sku" in endpoint:
            unmatched = [111] if self.import_unmatched else []
            return {"result": {"task_id": "555", "unmatched_sku_list": unmatched}}
        if "import/info" in endpoint:
            items = [{"product_id": 999888777, "status": "imported"}] \
                if self.import_info_product else []
            return {"result": {"items": items}}
        return {}


def _run_node(envelope, post_recorder=None, gate_return=("", ""),
              record_gate_args=None, resolve_by_id=("", "")):
    """跑 follow_sell_import_node：类目确定性解析失败 + 门控仲裁打桩。"""
    recorder = post_recorder or _PostRecorder()

    def _gate(terms, source_words, draft, state, query=None):
        if record_gate_args is not None:
            record_gate_args.append({"terms": list(terms or []),
                                     "source_words": str(source_words or "")})
        return gate_return

    with mock.patch.object(ocq_mod, "get_category_query", return_value=None), \
         mock.patch.object(fsin, "_verify_category_schema", return_value=False), \
         mock.patch.object(fsin, "_resolve_category_by_id",
                           return_value=resolve_by_id), \
         mock.patch.object(fsin, "_gated_category_arbitration", side_effect=_gate), \
         mock.patch.object(cml_mod, "lookup_mapping", return_value=None), \
         mock.patch.object(attr_defaults_mod, "build_follow_attr_merge",
                           return_value=[]), \
         mock.patch("time.sleep", lambda s: None), \
         mock.patch.object(fsin, "ozon_post", recorder):
        result = fsin.follow_sell_import_node(FakeState(envelope=envelope))
    return result, recorder


# ═══════════════════════════════════════════════════════════════════════
# P-D：discover 变体绝不 import-by-sku
# ═══════════════════════════════════════════════════════════════════════
def test_pd_discover_never_import_by_sku_and_empty_category_continues():
    """discover 变体：类目解析失败 → 零 import-by-sku 调用 + 类目置空继续管线。"""
    args = []
    result, rec = _run_node(_discover_envelope(), gate_return=("", ""),
                            record_gate_args=args)
    ibs = [u for u in rec.calls if "import-by-sku" in u]
    assert not ibs, f"discover 变体绝不触发 import-by-sku: {ibs}"
    assert not result.get("description_category_id")
    assert not result.get("type_id")
    assert not result.get("error_message"), "discover 类目空不写 error_message"
    assert not result.get("failed_stage")
    assert result.get("upload_status") == "pending"
    # 门控确实被调用过（仲裁先于置空）
    assert args, "类目门控仲裁应被调用"


def test_pd_discover_fail_not_retry_route():
    """discover 类目失败信息不带「类目解析失败」触发词 → 路由进 pricing 不进 retry。"""
    result, _rec = _run_node(_discover_envelope(), gate_return=("", ""))
    err = result.get("error_message") or ""
    assert "类目解析失败" not in err
    from graphs.graph import route_after_follow_sell_import
    route = route_after_follow_sell_import(
        SimpleNamespace(error_message=err, product_id=None))
    assert route == "pricing", f"discover 类目空应走 pricing，实际: {route}"


def test_pd_discover_gate_adopted_category_used():
    """discover 门控仲裁通过 → 采纳 dc/tp，仍不触发 import-by-sku。"""
    result, rec = _run_node(_discover_envelope(),
                            gate_return=("17039635", "90414"))
    assert result.get("description_category_id") == "17039635"
    assert result.get("type_id") == "90414"
    assert not [u for u in rec.calls if "import-by-sku" in u]
    assert not result.get("error_message")


def test_pd_discover_schema_trusted_direct_adopt_no_gate():
    """红线①对 discover 同样生效：schema 200 直采，不进门控、不二次解析。"""
    envelope = _discover_envelope(draft_extra={
        "ozon_category": {"description_category_id": "17028990",
                          "type_id": "93418",
                          "category_path": _BREADCRUMB},
    })
    gate_args = []
    resolve_args = []

    def _gate(*a, **k):
        gate_args.append(a)
        return ("", "")

    with mock.patch.object(fsin, "_verify_category_schema", return_value=True), \
         mock.patch.object(fsin, "_resolve_category_by_id",
                           side_effect=lambda *a, **k: resolve_args.append(a) or ("", "")), \
         mock.patch.object(fsin, "_gated_category_arbitration", side_effect=_gate), \
         mock.patch.object(attr_defaults_mod, "build_follow_attr_merge",
                           return_value=[]), \
         mock.patch("requests.post", _PostRecorder()):
        result = fsin.follow_sell_import_node(FakeState(envelope=envelope))
    assert result.get("description_category_id") == "17028990"
    assert result.get("type_id") == "93418"
    assert not resolve_args, "schema 200 权威直采不二次解析"
    assert not gate_args, "schema 200 权威直采不进门控"


def test_pd_discover_gate_terms_use_tail_segments():
    """门控搜索词只取面包屑末段+倒数第二段（整段路径已实证喂噪音）。"""
    args = []
    _run_node(_discover_envelope(), gate_return=("", ""), record_gate_args=args)
    assert args, "门控应被调用"
    terms = args[0]["terms"]
    assert "Тепловое оборудование" in terms, f"末段词应在搜索词内: {terms}"
    assert "Отопление" in terms, f"倒数第二段应在搜索词内: {terms}"
    assert _BREADCRUMB not in terms, "整段路径不得作为搜索词"
    assert args[0]["source_words"], "source_words（仲裁源词）不应为空"


# ═══════════════════════════════════════════════════════════════════════
# P-D：存量语义回归（hand / 旧信封 / api 复制）
# ═══════════════════════════════════════════════════════════════════════
def test_pd_hand_category_fail_still_downgrades_to_api():
    """存量回归：hand 类目解析失败仍自动降级 api import-by-sku（不丢单）。"""
    rec = _PostRecorder(import_info_product=True)
    result, _ = _run_node(_discover_envelope(follow_type="hand"),
                          post_recorder=rec, gate_return=("", ""))
    assert len([u for u in rec.calls if "import-by-sku" in u]) == 1, "hand 缺类目应降级 api 复制"
    assert result.get("product_id") == "999888777"
    assert result.get("category_missing") is True


def test_pd_legacy_envelope_without_follow_type_is_hand():
    """旧信封（extensions 无 follow_type）保持 hand 语义零变化：类目+货源齐 → 不复制。"""
    rec = _PostRecorder(import_info_product=True)
    result, _ = _run_node(_discover_envelope(follow_type=None),
                          post_recorder=rec,
                          resolve_by_id=("17027918", "971311385"))
    assert not [u for u in rec.calls if "import-by-sku" in u], "旧信封默认 hand 不复制"
    assert result.get("description_category_id") == "17027918"


def test_pd_hand_import_fail_gate_fail_returns_retry_error():
    """存量回归：hand 降级 api 后复制失败 + 门控不过 → 报「类目解析失败」进 retry。"""
    rec = _PostRecorder(import_unmatched=True)
    result, _ = _run_node(_discover_envelope(follow_type="hand"),
                          post_recorder=rec, gate_return=("", ""))
    err = result.get("error_message") or ""
    assert "类目解析失败" in err
    from graphs.graph import route_after_follow_sell_import
    route = route_after_follow_sell_import(
        SimpleNamespace(error_message=err, product_id=None))
    assert route == "retry"


def test_pd_deterministic_resolution_skips_gate():
    """红线②：确定性树命中直采不进门控（无 LLM）。"""
    gate_args = []

    def _gate(*a, **k):
        gate_args.append(a)
        return ("", "")

    envelope = _discover_envelope(follow_type="hand")
    with mock.patch.object(fsin, "_verify_category_schema", return_value=False), \
         mock.patch.object(fsin, "_resolve_category_by_id",
                           return_value=("17027918", "971311385")), \
         mock.patch.object(fsin, "_gated_category_arbitration", side_effect=_gate), \
         mock.patch.object(attr_defaults_mod, "build_follow_attr_merge",
                           return_value=[]), \
         mock.patch("requests.post", _PostRecorder()):
        result = fsin.follow_sell_import_node(FakeState(envelope=envelope))
    assert result.get("description_category_id") == "17027918"
    assert not gate_args, "确定性直采不得进门控"


# ═══════════════════════════════════════════════════════════════════════
# P-B：门控仲裁单元（候选池 → R1 → R2b 四段判定 → R1 veto）
# ═══════════════════════════════════════════════════════════════════════
_REcircULATOR = {  # wave D 错放锚：医用 Рециркулятор（sim 恰好过线的噪音候选）
    "description_category_id": 975001, "type_id": 975002,
    "node_name": "Рециркулятор", "full_path": "Медицина и здоровье > Рециркулятор",
    "similarity": 0.50, "matcher": "pg_trgm",
}
_HEATER_ZH = {  # 域内正确候选：叶子名与源词「暖风机」子串命中
    "description_category_id": 17039635, "type_id": 90415,
    "node_name": "暖风机", "full_path": "家用电器 > 空调设备 > 暖风机",
    "similarity": 0.42, "matcher": "jieba",
}
_CANDY = {  # 敏感锚：成人糖果 18+（源词零敏感信号）
    "description_category_id": 200001462, "type_id": 971363842,
    "node_name": "成人糖果", "full_path": "成人用品 > 成人的糖果点心 > 成人糖果",
    "similarity": 0.88, "matcher": "jieba",
}


class _FakeGateQuery:
    """search_nodes 按 term 返回预置候选；get_node 提供 RU 树路径（判据 b）。"""

    def __init__(self, results_by_term=None, ru_paths=None):
        self.results_by_term = results_by_term or {}
        self.ru_paths = ru_paths or {}
        self.search_calls = []

    def search_nodes(self, term, top_k=5, node_type="type", language="ZH_HANS"):
        self.search_calls.append((term, language))
        return [dict(r) for r in self.results_by_term.get(term, [])]

    def get_node(self, dc, tp, language="ZH_HANS"):
        # 对齐真实 OzonCategoryQuery.get_node：返回节点 dict（含 full_path）
        path = self.ru_paths.get((int(dc), int(tp), language))
        return {"full_path": path} if path else None


def test_pb_gate_adopt_via_llm_confirm_overlap():
    """仲裁通过：LLM 选中叶子与源词子串命中的候选 → 采纳（真实四段判定接线）。"""
    q = _FakeGateQuery(results_by_term={"暖风机": [_HEATER_ZH]})
    with mock.patch.object(asm, "_llm_rank_categories",
                           return_value=dict(_HEATER_ZH)):
        dc, tp = fsin._gated_category_arbitration(
            ["暖风机"], "暖风机 取暖器 обогреватель", {"images": []},
            SimpleNamespace(token="t"), query=q)
    assert (dc, tp) == ("17039635", "90415")
    assert ("暖风机", "ZH_HANS") in q.search_calls


def test_pb_gate_adopt_via_cyrillic_ru_path():
    """判据 b 接线：西里尔源词 → RU 树路径 overlap 放行（路径含源词字面）。"""
    q = _FakeGateQuery(
        results_by_term={"Тепловое оборудование": [dict(_HEATER_ZH)]},
        ru_paths={(17039635, 90415, "RU"):
                  "Бытовая техника > Климатическая техника > Тепловое оборудование"},
    )
    with mock.patch.object(asm, "_llm_rank_categories",
                           return_value=dict(_HEATER_ZH)):
        dc, tp = fsin._gated_category_arbitration(
            ["Тепловое оборудование"],
            "Тепловое оборудование обогреватель",
            {"images": []}, SimpleNamespace(token="t"), query=q)
    assert (dc, tp) == ("17039635", "90415")


def test_pb_gate_abstain_blocks_no_fallback():
    """LLM abstain → 不采纳不保底，返回空类目（绝不静默取 top1 噪音候选）。"""
    q = _FakeGateQuery(results_by_term={
        "Тепловое оборудование": [_REcircULATOR, dict(_HEATER_ZH)]})
    with mock.patch.object(asm, "_llm_rank_categories", return_value=None) as llm:
        dc, tp = fsin._gated_category_arbitration(
            ["Тепловое оборудование"], "Тепловое оборудование обогреватель",
            {"images": []}, SimpleNamespace(token="t"), query=q)
    assert (dc, tp) == ("", "")
    assert llm.call_count == 1, "仲裁只做一次 LLM 调用"


def test_pb_gate_suggest_keywords_blocks():
    """LLM 建议词标记（_llm_suggest）同 abstain：不采纳。"""
    q = _FakeGateQuery(results_by_term={"暖风机": [dict(_HEATER_ZH)]})
    with mock.patch.object(asm, "_llm_rank_categories",
                           return_value={"_llm_suggest": True,
                                         "suggest_keywords": "обогреватель"}):
        dc, tp = fsin._gated_category_arbitration(
            ["暖风机"], "暖风机", {"images": []},
            SimpleNamespace(token="t"), query=q)
    assert (dc, tp) == ("", "")


def test_pb_gate_r1_veto_blocks_sensitive_adoption():
    """R1 veto 在门控内生效：overlap 成立但候选落敏感子树且源无敏感信号 → 拦。"""
    q = _FakeGateQuery(results_by_term={"成人糖果": [dict(_CANDY)]})
    with mock.patch.object(asm, "_llm_rank_categories",
                           return_value=dict(_CANDY)):
        dc, tp = fsin._gated_category_arbitration(
            ["成人糖果"], "成人糖果 18+", {"images": []},
            SimpleNamespace(token="t"), query=q)
    assert (dc, tp) == ("", ""), "敏感子树候选必须被 R1 veto 拦下"
    # 同场景但源词含白名单敏感信号 → R1 放行（语义不倒灌）
    with mock.patch.object(asm, "_llm_rank_categories",
                           return_value=dict(_CANDY)):
        dc2, tp2 = fsin._gated_category_arbitration(
            ["成人糖果"], "成人糖果 情趣用品", {"images": []},
            SimpleNamespace(token="t"), query=q)
    assert (dc2, tp2) == ("200001462", "971363842")


def test_pb_gate_empty_pool_skips_llm():
    """池为空（搜索无结果）→ 不调 LLM，直接置空。"""
    q = _FakeGateQuery()
    with mock.patch.object(asm, "_llm_rank_categories") as llm:
        dc, tp = fsin._gated_category_arbitration(
            ["非existent类目"], "x", {"images": []},
            SimpleNamespace(token="t"), query=q)
    assert (dc, tp) == ("", "")
    assert llm.call_count == 0


def test_pb_gate_language_routing_zh_vs_ru():
    """搜索语言路由：中文词走 ZH_HANS，西里尔词走 RU。"""
    q = _FakeGateQuery()
    with mock.patch.object(asm, "_llm_rank_categories"):
        fsin._gated_category_arbitration(
            ["暖风机", "Тепловое оборудование"], "暖风机 обогреватель",
            {"images": []}, SimpleNamespace(token="t"), query=q)
    langs = dict(q.search_calls)
    assert langs.get("暖风机") == "ZH_HANS"
    assert langs.get("Тепловое оборудование") == "RU"


# ═══════════════════════════════════════════════════════════════════════
# P-B：搜索词构造 + 单字 token 纪律
# ═══════════════════════════════════════════════════════════════════════
def test_pb_search_terms_tail_segments_and_brand_leaf():
    """strip 整段路径只取末段+倒数第二段；1688 来源类目同理；品牌末段保留（树查自然落空）。"""
    terms = fsin._gate_search_terms(
        {"category_path": "Дом и сад > Посуда > Тарелки > Canevia"},
        {"source_category": "居家保暖 > 取暖电器 > 暖风机"},
    )
    assert "Тарелки" in terms and "Canevia" in terms
    assert "暖风机" in terms and "取暖电器" in terms
    assert not any(">" in t for t in terms), "整段路径不得进搜索词"
    assert len(terms) == len(set(terms)), "搜索词去重"


def test_pb_search_terms_extra_hint():
    """文本类目名（非数字 dc_raw）作为补充搜索词传入。"""
    terms = fsin._gate_search_terms({}, {}, extra=["取暖电器"])
    assert terms == ["取暖电器"]


def test_pb_term_searchable_single_char_blocked():
    """单字核心 token 直接不搜（wave D「单字兜底直采」通道关闭）。"""
    assert fsin._term_searchable("帽") is False
    assert fsin._term_searchable("") is False
    # 修饰词全剥离（成人帽 → 无核心 token）也不搜
    assert fsin._term_searchable("成人帽") is False
    assert fsin._term_searchable("取暖器") is True
    assert fsin._term_searchable("Тепловое оборудование") is True


def test_pb_gate_no_direct_adoption_wiring():
    """接线断言：本节点不再有 pg_trgm/1688 jieba 无条件直采——模糊解析只经门控。"""
    import inspect
    src = inspect.getsource(fsin)
    assert "_gated_category_arbitration" in src
    assert "_r2b_confirm_adoption" in inspect.getsource(
        fsin._gated_category_arbitration)
    assert "_r1_veto" in inspect.getsource(fsin._gated_category_arbitration)
    assert "sensitive_candidate_filter" in inspect.getsource(
        fsin._gated_category_arbitration)
    assert "_llm_rank_categories" in inspect.getsource(
        fsin._gated_category_arbitration)
    assert "_build_r2b_confirm_pool" in inspect.getsource(
        fsin._gated_category_arbitration)
    # 旧的模糊直采入口必须已移除/不再被节点主流程调用
    assert "_translate_to_russian" not in src, "LLM 翻译直采通道应随 pg_trgm 直采一起移除"
    body = inspect.getsource(fsin.follow_sell_import_node)
    assert "1688 来源类目兜底成功" not in body, "jieba 兜底直采日志不应存在"
    assert "_resolve_category(" not in body.replace("_resolve_category_by_id", ""), \
        "pg_trgm 文本直采 _resolve_category 不应再被节点调用"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except Exception:
            traceback.print_exc()
            print(f"  FAIL {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
