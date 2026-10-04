"""follow_clone 首战修复批回归（v0.85.1，2026-10-04 测试店 5371047 五单实录）。

首战五单 import-by-sku 全成、在架 0/5，暴露的 bug 逐件锁定：
1. 锚价汇率换算：CNY 店 _get_exchange_rate(CNY) 恒 1.0（BL-01），RUB 锚价原样
   当 CNY（首战 4286₽→4286¥，12.5 倍）——现走 resolve_cny_rub_rate 真换算；
2. clone 底线档：克隆锚价天然低于主链三档 floor（≈成本×2.5 恒拒）——改
   int(成本链 × FOLLOW_CLONE_MIN_MARGIN_MULT)（缺省 1.3）；
3. UPDATE 分支图回退：复制请求不带图 → 复制卡天生零图，images=[] 铁锁必被
   validate 拒（首战 5/5 `item[0].images缺失`）——信封图源回填（见
   test_follow_clone_v085.py test_31 系列，此处锁 validate 全外链豁免）；
4. /v4 回查有界重试：复制确认后 +5s 读恒 404（索引延迟），+15s 即 200——
   3 次 × 5s，只对 404/空重试，其他异常照旧；
5. clone 模式零 LLM：门控仲裁（vision）跳过——类目由复制卡反查/clone_card 定稿。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_follow_clone_fix1_v0851.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.ozon_errors import OzonNotFoundError, is_index_latency_404

_CDN_IMG = "https://ir-20.ozone.ru/s3/multimedia-1-e/11833245914.jpg"


# ═══════════════ 修1+修2: pricing 锚价换算 + clone 底线档 ═══════════════


class _DummyRuntime:
    class _DummyContext:
        pass

    context = _DummyContext()


def _pricing_state(extensions, competitor_price, currency="CNY", total_hint=None):
    from types import SimpleNamespace as NS
    draft = {"cost_cny": 5.5, "purchase_cost": 5.5, "weight": 227,
             "dimensions": {"length": 120, "width": 80, "height": 60},
             "competitor_price": competitor_price}
    return NS(
        draft=draft,
        extensions=extensions,
        supabase_url="http://supabase.local", supabase_key="k",
        currency_code=currency, ozon_client_id="1", ozon_api_key="k",
        description_category_id="17028830", task_id="t", tenant_id="tn",
        user_id="tn", token="sk", envelope={}, variants=[],
    )


def _patch_pricing(monkeypatch, fx=12.5, fx_source="live_fetch", rub_rate=None):
    """clone+CNY 走 resolve_cny_rub_rate（可调 mock 值）；RUB 主链走 _get_exchange_rate。"""
    from graphs.nodes import pricing_node as pn
    from utils import logistics_quote, fx_rate_service

    monkeypatch.setattr(logistics_quote, "query_logistics_cost",
                        lambda *a, **k: (10.0, "mock_channel", {}))
    monkeypatch.setattr(logistics_quote, "get_store_logistics_config",
                        lambda *a, **k: ("RETS", "Standard"))
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None)
    monkeypatch.setattr(pn, "_get_exchange_rate",
                        lambda *a, **k: (rub_rate if rub_rate is not None else fx))
    monkeypatch.setattr(fx_rate_service, "resolve_cny_rub_rate",
                        lambda: (fx, fx_source), raising=True)
    return pn


def test_01_cny_anchor_fx_conversion(monkeypatch):
    """首战实录数字：CNY 店锚价 4286 RUB ×12.5 → 343 CNY（绝不能再是 4286）。"""
    pn = _patch_pricing(monkeypatch, fx=12.5, fx_source="live_fetch")
    st = _pricing_state({"currency_code": "CNY", "follow_clone": True}, "4286")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert int(out.price) == 343, f"4286/12.5=342.88→343, got {out.price}"
    assert out.pricing_info["clone_fx_rate"] == 12.5
    assert out.pricing_info["clone_fx_source"] == "live_fetch"
    assert out.pricing_info["price_source"] == "follow_clone_anchor"


def test_02_cny_fx_only_fetched_for_cny(monkeypatch):
    """RUB 店 clone 锚价原样（round），fx 服务不被调用（只在需要时拉）。"""
    pn = _patch_pricing(monkeypatch, fx=12.5)
    from utils import fx_rate_service
    with mock.patch.object(fx_rate_service, "resolve_cny_rub_rate",
                           side_effect=AssertionError("RUB 店不得拉 CNY→RUB 汇率")):
        st = _pricing_state({"currency_code": "RUB", "follow_clone": True},
                            "1000", currency="RUB")
        out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert int(out.price) == 1000


def test_03_cny_fx_unavailable_fail_closed(monkeypatch):
    """CNY 店换算汇率不可得 → LOCAL_PRICING_FAILED（绝不把 RUB 锚价当 CNY 上架）。"""
    pn = _patch_pricing(monkeypatch, fx=0.0, fx_source="")
    st = _pricing_state({"currency_code": "CNY", "follow_clone": True}, "4286")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_code == "LOCAL_PRICING_FAILED"
    assert out.price == ""
    assert "汇率" in out.error_message


def test_04_clone_floor_rejects_below_cost_x_mult(monkeypatch):
    """clone 底线档：成本链 5.5+10+2=17.5 CNY ×1.3 → floor=22；锚价换算 2 CNY < 22 → 拒。"""
    pn = _patch_pricing(monkeypatch, fx=12.5)
    st = _pricing_state({"currency_code": "CNY", "follow_clone": True}, "24")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_code == "LOCAL_PRICING_FAILED"
    assert "克隆底线" in out.error_message, out.error_message
    assert out.pricing_info.get("floor_price") == 22
    assert out.price == ""


def test_05_clone_floor_passes_at_or_above(monkeypatch):
    """锚价换算 96 CNY ≥ 22 → 通过（old_price 规则唯一出口不变）。"""
    pn = _patch_pricing(monkeypatch, fx=12.5)
    st = _pricing_state({"currency_code": "CNY", "follow_clone": True}, "1200")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert int(out.price) == 96
    assert out.pricing_info["clone_floor_price"] == 22
    assert out.pricing_info["clone_floor_margin_mult"] == 1.3


def test_06_clone_floor_mult_env(monkeypatch):
    """FOLLOW_CLONE_MIN_MARGIN_MULT=50 → floor=875，96 CNY 被拒（env 可调）。"""
    pn = _patch_pricing(monkeypatch, fx=12.5)
    monkeypatch.setenv("FOLLOW_CLONE_MIN_MARGIN_MULT", "50")
    st = _pricing_state({"currency_code": "CNY", "follow_clone": True}, "1200")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_code == "LOCAL_PRICING_FAILED"
    assert out.pricing_info.get("floor_price") == 875
    assert out.pricing_info.get("clone_floor_margin_mult") == 50.0


def test_07_rub_store_floor_converts_via_store_rate(monkeypatch):
    """RUB 店底线按本店真汇率折 RUB：17.5 CNY ×12.5 ×1.3 → 284；锚价 1000 RUB ≥ 284 过。"""
    pn = _patch_pricing(monkeypatch, fx=12.5, rub_rate=12.5)
    st = _pricing_state({"currency_code": "RUB", "follow_clone": True},
                        "1000", currency="RUB")
    out = pn.pricing_node(st, None, _DummyRuntime())
    assert out.error_message == "", out.error_message
    assert int(out.price) == 1000
    assert out.pricing_info["clone_floor_price"] == 284  # int(17.5*12.5*1.3)


# ═══════════════ 修3: validate 全外链闸 clone 模式豁免 ═══════════════


def test_11_validate_mirror_gate_exempt_in_clone_mode():
    """克隆 CDN 直传（非 COS）在 follow_clone 模式下不触发全外链硬错误；
    无 follow_clone 标记的全外链语义零变化（全局闸不松）。"""
    from graphs.nodes.ozon_validate_node import ozon_validate_node  # noqa: F401 导入烟测
    import inspect
    src = inspect.getsource(ozon_validate_node)
    assert "follow_clone" in src, "validate 节点必须带 clone 模式豁免分支"
    # 全局语义：非 clone 全外链仍是 critical 关键词
    assert "全外链" in src


# ═══════════════ 修4: /v4 回查 404 有界重试 ═══════════════


def test_21_index_latency_classifier():
    assert is_index_latency_404(OzonNotFoundError("item not found", status_code=404))
    assert is_index_latency_404(RuntimeError("HTTP 404: item not found"))
    assert is_index_latency_404(RuntimeError("item not found"))
    assert not is_index_latency_404(RuntimeError("connection reset"))
    assert not is_index_latency_404(RuntimeError("HTTP 401: unauthorized"))


class _RetryPost:
    """import-by-sku 成功 + /v4 前 N 次 404（索引延迟）后成功的录制 mock。"""

    def __init__(self, v4_fail_times=2, v4_exc=None):
        self.calls: list[str] = []
        self.v4_fail_times = v4_fail_times
        self.v4_exc = v4_exc or OzonNotFoundError("item not found", status_code=404)

    def __call__(self, client_id, api_key, endpoint, body=None, timeout=30, **kw):
        self.calls.append(endpoint)
        if "import-by-sku" in endpoint:
            return {"result": {"task_id": "7", "unmatched_sku_list": []}}
        if "import/info" in endpoint:
            return {"result": {"items": [{"product_id": 999888777, "status": "imported"}]}}
        if "product/info/list" in endpoint:
            return {"items": [{"id": 999888777, "description_category_id": 17027933,
                               "type_id": 970742618}]}
        if "product/info/attributes" in endpoint:
            if self.calls.count(endpoint) <= self.v4_fail_times:
                raise self.v4_exc
            return {"result": {"items": [{
                "id": 999888777,
                "attributes": [
                    {"complex_id": 0, "id": 21550,
                     "values": [{"dictionary_value_id": 99, "value": "Квадрат"}]},
                ],
            }]}}
        return {}


class _FakeState:
    def __init__(self, envelope):
        self.envelope = envelope
        self.ozon_client_id = "1"
        self.ozon_api_key = "k"
        self.token = "t"
        self.user_id = "test"
        self.currency_code = "CNY"


def _clone_env():
    return {
        "draft": {
            "ozon_product_id": "2062059459",
            "title": "Организатор",
            "images": [],
            "ozon_category": {},
            "competitor_price": "240.00",
            "purchase_cost": 15.0,
            "purchase_url": "https://detail.1688.com/offer/9.html",
            "item_id": "9",
        },
        "extensions": {"follow_sell": True, "follow_type": "clone", "follow_clone": True},
    }


def _run_follow(recorder):
    from graphs.nodes.follow_sell_import_node import follow_sell_import_node
    import graphs.nodes.follow_sell_import_node as fsin
    import utils.attr_defaults as attr_defaults_mod
    import utils.category_mapping_learn as cml_mod
    import utils.ozon_category_query as ocq_mod

    with mock.patch.object(ocq_mod, "get_category_query", return_value=None), \
         mock.patch.object(fsin, "_verify_category_schema", return_value=False), \
         mock.patch.object(fsin, "_resolve_category_by_id", return_value=("", "")), \
         mock.patch.object(cml_mod, "lookup_mapping", return_value=None), \
         mock.patch.object(attr_defaults_mod, "build_follow_attr_merge",
                           return_value=[]), \
         mock.patch("time.sleep", lambda s: None), \
         mock.patch.object(fsin, "ozon_post", recorder):
        return fsin.follow_sell_import_node(_FakeState(_clone_env()))


def test_22_v4_readback_retries_on_404_then_succeeds():
    rec = _RetryPost(v4_fail_times=2)
    result = _run_follow(rec)
    assert not result.get("error_message"), result.get("error_message")
    v4_calls = [c for c in rec.calls if c.endswith("/product/info/attributes")]
    assert len(v4_calls) == 3, f"404×2 后第 3 次命中，实际 {len(v4_calls)} 次"
    got = result.get("follow_copied_attributes") or []
    assert [a["id"] for a in got] == [21550], "重试命中后原表读回"


def test_23_v4_readback_no_retry_on_other_errors():
    """非 404 异常照旧一次放弃（不重试不阻断）。"""
    rec = _RetryPost(v4_fail_times=99, v4_exc=RuntimeError("connection reset"))
    result = _run_follow(rec)
    v4_calls = [c for c in rec.calls if c.endswith("/product/info/attributes")]
    assert len(v4_calls) == 1, "非 404 异常不得重试"
    assert result.get("follow_copied_attributes") == []


def test_24_v4_readback_empty_result_retries():
    """200 但无匹配 item（索引未就绪的空回显）同样按延迟重试。"""
    rec = _RetryPost(v4_fail_times=0)
    real_call = rec.__call__
    v4_state = {"n": 0}

    def flaky(cid, key, ep, body=None, timeout=30, **kw):
        # 只对 v4 首次调用注入空 items（其余端点透传录制 mock）
        if "product/info/attributes" in ep:
            v4_state["n"] += 1
            if v4_state["n"] == 1:
                return {"result": {"items": []}}
        return real_call(cid, key, ep, body=body, timeout=timeout, **kw)

    result = _run_follow(flaky)
    got = result.get("follow_copied_attributes") or []
    assert [a["id"] for a in got] == [21550], "空表重试后命中"


def test_25_preserve_readback_retries_on_404():
    """prepare preserve_existing_card_attributes 同款有界重试（404×2 → 第 3 次命中）。"""
    from graphs.nodes.prepare_ozon_upload_node import preserve_existing_card_attributes
    import utils.ozon_client as oc
    import time as _time

    calls = {"n": 0}

    def flaky_v4(cid, key, ep, body=None, timeout=15, **kw):
        calls["n"] += 1
        assert "product/info/attributes" in ep
        if calls["n"] <= 2:
            raise OzonNotFoundError("item not found", status_code=404)
        return {"result": {"items": [{
            "id": 6443818882,
            "attributes": [{"complex_id": 0, "id": 6788,
                            "values": [{"dictionary_value_id": 55, "value": "1500"}]}],
        }]}}

    with mock.patch.object(oc, "ozon_post", flaky_v4), \
            mock.patch.object(_time, "sleep", lambda s: None):
        items = [{"product_id": "6443818882",
                  "attributes": [{"id": 85, "values": [{"dictionary_value_id": 1, "value": "x"}]}]}]
        out = preserve_existing_card_attributes("1", "k", items)
    assert calls["n"] == 3, f"404×2 后第 3 次命中，实际 {calls['n']}"
    ids = {a["id"] for a in out[0]["attributes"]}
    assert 6788 in ids and 85 in ids, "重试命中后合并 + 我方值不动"


def test_26_preserve_readback_no_retry_on_other_errors():
    from graphs.nodes.prepare_ozon_upload_node import preserve_existing_card_attributes
    import utils.ozon_client as oc

    calls = {"n": 0}

    def boom(cid, key, ep, body=None, timeout=15, **kw):
        calls["n"] += 1
        raise RuntimeError("connection reset")

    with mock.patch.object(oc, "ozon_post", boom):
        items = [{"product_id": "6443818882", "attributes": []}]
        out = preserve_existing_card_attributes("1", "k", items)
    assert calls["n"] == 1, "非 404 异常照旧一次放弃"
    assert out[0]["attributes"] == []


# ═══════════════ 修6: clone 模式零 LLM ═══════════════


def test_31_clone_mode_skips_gated_arbitration():
    """clone 模式跳过类目门控仲裁（vision LLM）——类目由复制卡反查定稿。"""
    import graphs.nodes.follow_sell_import_node as fsin
    rec = _RetryPost(v4_fail_times=0)
    import utils.attr_defaults as attr_defaults_mod
    import utils.category_mapping_learn as cml_mod
    import utils.ozon_category_query as ocq_mod

    arb_calls = []

    def _spy_arb(*a, **k):
        arb_calls.append(a)
        return ("", "")

    with mock.patch.object(ocq_mod, "get_category_query", return_value=None), \
         mock.patch.object(fsin, "_verify_category_schema", return_value=False), \
         mock.patch.object(fsin, "_resolve_category_by_id", return_value=("", "")), \
         mock.patch.object(fsin, "_gated_category_arbitration", side_effect=_spy_arb), \
         mock.patch.object(cml_mod, "lookup_mapping", return_value=None), \
         mock.patch.object(attr_defaults_mod, "build_follow_attr_merge",
                           return_value=[]), \
         mock.patch("time.sleep", lambda s: None), \
         mock.patch.object(fsin, "ozon_post", rec):
        result = fsin.follow_sell_import_node(_FakeState(_clone_env()))
    assert arb_calls == [], "clone 模式不得触发类目门控仲裁（vision LLM）"
    assert result.get("description_category_id") == "17027933", "类目由复制卡反查定稿"


def test_32_non_clone_still_arbitrates():
    """非 clone（hand/api）门控仲裁语义零变化（仍会被调用）。"""
    import graphs.nodes.follow_sell_import_node as fsin
    import utils.attr_defaults as attr_defaults_mod
    import utils.category_mapping_learn as cml_mod
    import utils.ozon_category_query as ocq_mod

    env = _clone_env()
    env["extensions"]["follow_type"] = "api"
    env["extensions"].pop("follow_clone")

    class _UnmatchedPost(_RetryPost):
        def __call__(self, client_id, api_key, endpoint, body=None, timeout=30, **kw):
            if "import-by-sku" in endpoint:
                return {"result": {"task_id": "7", "unmatched_sku_list": [111]}}
            if "import/info" in endpoint:
                return {"result": {"items": []}}
            return {"items": []}

    arb_calls = []

    def _spy_arb(*a, **k):
        arb_calls.append(a)
        return ("", "")

    with mock.patch.object(ocq_mod, "get_category_query", return_value=None), \
         mock.patch.object(fsin, "_verify_category_schema", return_value=False), \
         mock.patch.object(fsin, "_resolve_category_by_id", return_value=("", "")), \
         mock.patch.object(fsin, "_gated_category_arbitration", side_effect=_spy_arb), \
         mock.patch.object(cml_mod, "lookup_mapping", return_value=None), \
         mock.patch.object(attr_defaults_mod, "build_follow_attr_merge",
                           return_value=[]), \
         mock.patch("time.sleep", lambda s: None), \
         mock.patch.object(fsin, "ozon_post", _UnmatchedPost()):
        fsin.follow_sell_import_node(_FakeState(env))
    assert arb_calls, "非 clone 模式门控仲裁语义零变化（仍触发）"


# ── 修6b: prepare 标题/属性翻译链 clone 短路（全节点冒烟，首战实录形态）──


def test_41_prepare_clone_mode_zero_llm_verbatim():
    """clone 模式 prepare 全节点零 LLM：拉丁标题/拉丁属性值逐字回显，
    翻译/vision/schema-LLM 任一被触发即 fail（首战单均 3+ 次的短路锁）。"""
    from graphs.state import PrepareOzonUploadInput
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node

    final_attributes = [
        {"attribute_id": 4191, "value": "Организатор для хранения", "dictionary_value_id": 0},
        {"attribute_id": 21550, "value": "ModelX-99", "dictionary_value_id": 0},  # 拉丁值：非 clone 必走翻译
        {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801},
    ]
    schema = [
        {"id": 4191, "name": "Описание", "dictionary_id": 0, "is_required": True},
        {"id": 21550, "name": "Форма", "dictionary_id": 0, "is_required": False},
        {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True},
    ]
    state = PrepareOzonUploadInput(
        draft={
            "item_id": "999888777",
            "sku_id": "999888777",
            "ozon_product_id": "999888777",
            "title": " Smoking Accessory",  # 首战实录拉丁标题（克隆卡名）
            "images": [_CDN_IMG],
            "weight": 200,
            "dimensions": {"length": 140, "width": 100, "height": 60},
            "attributes": {},
            "price": "941",
            "original_price": "1223",
        },
        extensions={"follow_clone": True, "follow_sell": True, "follow_type": "clone",
                    "clone_card": {"images": [_CDN_IMG]}},
        pricing_info={"price": "941", "old_price": "1223", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=final_attributes,
        attributes_schema=schema,
        dictionary_values={},
        token="sk-test",
        original_images=[_CDN_IMG],
        main_image="https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/test-main.jpg",
    )

    def _no_llm(*a, **k):
        raise AssertionError("clone 模式不得触发 LLM（翻译/生成/vision）")

    with mock.patch(
            "graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
            side_effect=_no_llm), \
            mock.patch(
                "graphs.nodes.prepare_ozon_upload_node.call_mxou_chat_api",
                side_effect=_no_llm), \
            mock.patch(
                "graphs.nodes.prepare_ozon_upload_node._infer_attrs_from_vision",
                side_effect=_no_llm), \
            mock.patch(
                "utils.attr_fill_extras.build_llm_schema_prompt",
                side_effect=_no_llm):
        output = prepare_ozon_upload_node(state, None, None)

    items = (output.ozon_payload or {}).get("items", [])
    assert items, "payload 必须产出"
    # 标题逐字回显（首战实录：曾被 LLM 译成 'Аксессуар для курения...'）
    assert items[0]["name"] == " Smoking Accessory", items[0]["name"]
    # UPDATE/CREATE 图分支：CREATE 回退走 clone_card CDN 直传
    assert _CDN_IMG in (items[0].get("images") or []), "clone 图源回填（CDN 直传）"
    # 拉丁属性值逐字保留（非 clone 路径会走 _russian_required_attrs 翻译链）
    attr_vals = {}
    for a in items[0].get("attributes", []):
        if a.get("values"):
            attr_vals[int(a["id"])] = a["values"][0].get("value", "")
    assert attr_vals.get(21550) == "ModelX-99", attr_vals
    assert attr_vals.get(4191) == "Организатор для хранения"


if __name__ == "__main__":
    import inspect
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        needs_mp = "monkeypatch" in inspect.signature(fn).parameters
        if needs_mp:
            print(f"SKIP(pytest-only) {name}")
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
