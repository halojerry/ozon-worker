# -*- coding: utf-8 -*-
"""v0.73 Task8 — failed_stage 粘连根治：Output 默认值归零 + 失败显式声明。

生产实证：所有任务 result.failed_stage 都是 "ozon_uploadpricingvideo_gen"（成功任务
也一样）——根因：GlobalState.failed_stage 是 Annotated[str, operator.add]（LangGraph
reducer 对 str 是拼接），而 Output 模型（PricingOutput/OzonUploadOutput/
SceneGenerationOutput/OzonStatusOutput）的 failed_stage 默认值非空（"pricing"/
"ozon_upload"/"video_gen"/"ozon_status"），对应节点成功路径 return 不传该字段 →
LangGraph 用默认值填充 → reducer 每次运行都按拓扑序拼接。
（OzonStatusOutput 为终审 I1 补齐：state.py:765 默认值归零 + 失败出口显式带。）

修复契约：
- 四 Output 默认值归零：成功路径归并后 failed_stage == ""；
- 失败出口显式携带（pricing_node 失败 return / ozon_upload_node 全部 failed return /
  scene_generation_llm_node 降级 return / ozon_status_node 全部 failed return）；
- reducer operator.add 语义不变（"" 是拼接恒等元）；
- _graph_result_is_failed 双形状判定不变（error_message+failed_stage 与
  upload_status=failed 独立通道，锁 Task2/v0.69 语义）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_failed_stage_v073.py -q
"""
import functools
import json
import operator
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import requests

os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import (  # noqa: E402
    OzonStatusOutput,
    OzonUploadInput,
    OzonUploadOutput,
    PricingOutput,
    SceneGenerationInput,
    SceneGenerationOutput,
)
from graphs.nodes import pricing_node as pn  # noqa: E402
from graphs.nodes import scene_generation_llm_node as sg  # noqa: E402
from graphs.nodes.ozon_status_node import ozon_status_node  # noqa: E402
from graphs.nodes.ozon_upload_node import ozon_upload_node  # noqa: E402
from utils.ozon_errors import OzonError, OzonNotFoundError  # noqa: E402
from utils.task_processor import _graph_result_is_failed  # noqa: E402

TASK_ID = "7312849091234"
_OFFER = "offer-failed-stage-v073"


# ══════════════ 1. Output 默认实例归零（RED：现状默认非空恒粘连） ══════════════

def test_output_defaults_are_empty():
    """四个 Output 默认实例 failed_stage 必须 == ""——默认值非空经 operator.add
    每次运行拼接（生产 "ozon_uploadpricingvideo_gen" 粘连根因）。"""
    for model in (PricingOutput, OzonUploadOutput, SceneGenerationOutput, OzonStatusOutput):
        out = model()
        assert out.failed_stage == "", (
            f"{model.__name__}.failed_stage 默认值必须为空（reducer operator.add 会让"
            f"成功路径也拼接默认值），实际 {out.failed_stage!r}"
        )


def test_three_defaults_merge_clean_after_reduce():
    """生产粘连形状的单元级模拟：四 Output 各贡献一段 → reducer 归并后必须为 ""。"""
    merged = functools.reduce(
        operator.add,
        (m().failed_stage for m in (PricingOutput, OzonUploadOutput, SceneGenerationOutput, OzonStatusOutput)),
    )
    assert merged == ""


# ══════════════ 2. reducer 语义：operator.add 对 "" 恒等（归零不改累加行为） ══════════════

def test_reducer_add_semantics_unchanged():
    assert operator.add("", "auth") == "auth"
    assert operator.add("auth", "") == "auth"
    # 成功全链：所有节点贡献 "" → 归并恒 ""
    assert functools.reduce(operator.add, ["", "", "", ""]) == ""
    # 失败链：显式值原样累加（auth 失败出口显式带值的行为不变）
    assert functools.reduce(operator.add, ["", "auth", ""]) == "auth"
    # 双节点失败仍如实并列（有意保留 operator.add 通道语义）
    assert functools.reduce(operator.add, ["pricing", ""]) == "pricing"


# ══════════════ 3. pricing_node：失败显式带 "pricing"，成功恒空 ══════════════

_UNSET = object()


def _make_pricing_state(draft=_UNSET, envelope=None, user_id="u-1"):
    """pricing_node 可直接消费的 state（SimpleNamespace，对齐 test_price_sanity_guard_v073）。

    draft=_UNSET → 默认合法草稿；显式传 None 才走 draft=None 失败分支（勿用默认参数
    表达 None——会被「缺省补默认」吞掉）。"""
    return SimpleNamespace(
        draft={
            "cost_cny": 5.5,
            "purchase_cost": 5.5,
            "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        } if draft is _UNSET else draft,
        extensions={},
        supabase_url="http://supabase.local",
        supabase_key="k",
        currency_code="RUB",
        ozon_client_id="1",
        ozon_api_key="k",
        description_category_id="17028830",
        task_id="",
        tenant_id="",
        token="sk-test",
        user_id=user_id,
        envelope=envelope or {},
        variants=[],
        error_message="",
        pricing_info={},
    )


class _DummyRuntime:
    context = None


def _call_pricing(monkeypatch, state):
    """mock 重依赖后调用 pricing_node（纯内存，无 PG/HTTP）。"""
    from utils import logistics_quote

    monkeypatch.setattr(
        logistics_quote, "query_logistics_cost",
        lambda *a, **k: (10.0, "mock_channel", {}),
    )
    monkeypatch.setattr(
        logistics_quote, "get_store_logistics_config",
        lambda *a, **k: ("RETS", "Standard"),
    )
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: 12.0)
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(pn, "ozon_post", lambda *a, **k: {}, raising=False)
    return pn.pricing_node(state, None, _DummyRuntime())


def test_pricing_success_failed_stage_empty(monkeypatch):
    """成功定价 → failed_stage == ""（RED：现状恒 "pricing" 随成功 return 粘进 state）。"""
    out = _call_pricing(monkeypatch, _make_pricing_state())
    assert out.error_message == ""
    assert out.price.strip() != ""
    assert out.failed_stage == "", (
        f"成功路径 failed_stage 必须为空，实际 {out.failed_stage!r}"
    )


def test_pricing_none_draft_failure_carries_stage():
    """draft=None 失败出口显式带 failed_stage="pricing"。

    ⚠️ 该形状（error_message 无 "[" 前缀、无 upload_status）此前靠默认值非空才被
    _graph_result_is_failed 判 failed——默认值归零后若不显式带 stage 即假 completed。"""
    out = pn.pricing_node(_make_pricing_state(draft=None), None, _DummyRuntime())
    assert out.error_message == "Draft data is None"
    assert out.failed_stage == "pricing"
    # 终态防线锁：显式 stage + error_message 双通道仍判 failed
    assert _graph_result_is_failed({
        "error_message": out.error_message, "failed_stage": out.failed_stage,
    }) is True


def test_pricing_exception_failure_carries_stage(monkeypatch):
    """计算异常（[PRICING_FAILED] 出口）显式带 failed_stage="pricing"。"""
    from utils import logistics_quote

    def _boom(*a, **k):
        raise RuntimeError("logistics boom")

    monkeypatch.setattr(logistics_quote, "get_store_logistics_config", _boom)
    out = pn.pricing_node(_make_pricing_state(), None, _DummyRuntime())
    assert "[PRICING_FAILED]" in out.error_message
    assert out.failed_stage == "pricing"


def test_pricing_gap_block_stage_locked(monkeypatch):
    """价差守卫 block 出口（Task7 已显式带）保持 failed_stage="pricing" 不回归。"""
    ctrl = _call_pricing(monkeypatch, _make_pricing_state())
    anchor = int(ctrl.price) * 12
    envelope = {"extensions": {"discovery_meta": {"ozon_price": anchor}}}
    out = _call_pricing(monkeypatch, _make_pricing_state(envelope=envelope))
    assert "[PRICING_FAILED]" in out.error_message
    assert out.failed_stage == "pricing"


# ══════════════ 4. ozon_upload_node：失败显式带 "ozon_upload"，成功/pending 恒空 ══════════════

def _payload():
    """最小合法 payload（节点对缺字段只告警不阻断）。"""
    return {"items": [{"name": "Тест", "offer_id": _OFFER, "price": "100", "old_price": "120"}]}


def _upload_state(import_submitted=False, ozon_payload=None, client_id="123", api_key="key"):
    return OzonUploadInput(
        ozon_payload=ozon_payload if ozon_payload is not None else _payload(),
        ozon_client_id=client_id,
        ozon_api_key=api_key,
        sku_id=_OFFER,
        import_submitted=import_submitted,
    )


def _run_upload(state, ozon_post_return=None, ozon_post_exc=None):
    """跑 upload 节点：mock 配额 + offer 查找 + import POST（对齐 test_upload_silent_v073）。"""
    with patch("graphs.nodes.ozon_upload_node.ozon_check_quota",
               return_value={"ok": True, "daily_used": 1, "daily_limit": 100,
                             "total_used": 1, "total_limit": 1000,
                             "remaining_daily": 99, "remaining_total": 999}), \
         patch("graphs.nodes.ozon_upload_node.find_product_by_offer", return_value=None), \
         patch("graphs.nodes.ozon_upload_node.session"), \
         patch("graphs.nodes.ozon_upload_node.ozon_post") as ozon_post_mock:
        if ozon_post_exc is not None:
            ozon_post_mock.side_effect = ozon_post_exc
        else:
            ozon_post_mock.return_value = ozon_post_return
        out = ozon_upload_node(state, None, SimpleNamespace(context=None))
    return out


def test_upload_success_failed_stage_empty():
    out = _run_upload(_upload_state(), ozon_post_return={"result": {"task_id": TASK_ID}})
    assert out.upload_status == "success"
    assert out.failed_stage == "", f"成功路径 failed_stage 必须为空，实际 {out.failed_stage!r}"


def test_upload_pending_failed_stage_empty():
    """import-by-sku pending 合法等待态（Task6 锁定）不携带失败标记。"""
    out = _run_upload(_upload_state(import_submitted=True),
                      ozon_post_return={"result": {"task_id": TASK_ID}})
    assert out.upload_status == "pending"
    assert out.failed_stage == ""


def test_upload_request_exception_carries_stage():
    out = _run_upload(_upload_state(),
                      ozon_post_exc=requests.exceptions.RequestException("conn reset"))
    assert out.upload_status == "failed"
    assert out.failed_stage == "ozon_upload"
    # 双通道锁：upload_status 通道独立判 failed（不依赖 failed_stage）
    assert _graph_result_is_failed({
        "upload_status": out.upload_status,
        "error_message": out.error_message,
        "failed_stage": out.failed_stage,
    }) is True


def test_upload_ozon_error_carries_stage():
    out = _run_upload(_upload_state(), ozon_post_exc=OzonError("BOOM", status_code=500))
    assert out.upload_status == "failed"
    assert out.failed_stage == "ozon_upload"


def test_upload_200_without_task_id_carries_stage():
    """Task6 收口的显式 failed 出口同步携带 failed_stage。"""
    out = _run_upload(_upload_state(), ozon_post_return={"result": {}})
    assert out.upload_status == "failed"
    assert "未返回 import task_id" in (out.error_message or "")
    assert out.failed_stage == "ozon_upload"


def test_upload_empty_payload_carries_stage():
    """payload 为空失败出口（无网络路径）显式带 failed_stage。"""
    out = ozon_upload_node(_upload_state(ozon_payload={}), None, SimpleNamespace(context=None))
    assert out.upload_status == "failed"
    assert out.error_message == "Prepared payload is required"
    assert out.failed_stage == "ozon_upload"


def test_upload_missing_credentials_carries_stage():
    """缺凭证失败出口（无网络路径）显式带 failed_stage。"""
    out = ozon_upload_node(_upload_state(client_id="", api_key=""), None,
                           SimpleNamespace(context=None))
    assert out.upload_status == "failed"
    assert out.error_message == "Missing Ozon API credentials"
    assert out.failed_stage == "ozon_upload"


# ══════════════ 5. scene_generation_llm_node：降级显式带 "video_gen"，成功恒空 ══════════════

def _scene_cfg(monkeypatch, tmp_path):
    """APP_WORKSPACE_PATH 指向 tmp + 最小 scene_generation_llm_cfg.json（节点按
    config['metadata']['llm_cfg'] 相对路径读文件）。"""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "scene_generation_llm_cfg.json").write_text(json.dumps({
        "config": {"model": "test-model", "temperature": 0.1, "max_tokens": 64},
        "sp": "sp",
        "up": "up",
    }), encoding="utf-8")
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    return {"metadata": {"llm_cfg": "config/scene_generation_llm_cfg.json"}}


def _scene_state():
    return SceneGenerationInput(
        draft={"title": "测试防晒帽", "description": "", "category": "", "images": []},
        token="sk-test",
    )


def test_scene_success_failed_stage_empty(monkeypatch, tmp_path):
    cfg = _scene_cfg(monkeypatch, tmp_path)
    with patch.object(sg, "call_mxou_chat_api",
                      return_value=json.dumps({"scene_1": "沙滩", "scene_2": "办公室", "scene_3": "户外"})):
        out = sg.scene_generation_llm_node(_scene_state(), cfg, SimpleNamespace(context=None))
    assert out.scene_context_1 == "沙滩"
    assert out.failed_stage == "", f"成功路径 failed_stage 必须为空，实际 {out.failed_stage!r}"


def test_scene_empty_content_degraded_carries_stage(monkeypatch, tmp_path):
    """LLM 返回空内容 → 默认场景降级出口显式带 failed_stage="video_gen"。"""
    cfg = _scene_cfg(monkeypatch, tmp_path)
    with patch.object(sg, "call_mxou_chat_api", return_value=""):
        out = sg.scene_generation_llm_node(_scene_state(), cfg, SimpleNamespace(context=None))
    assert out.scene_context_1 == "户外运动场景"
    assert out.failed_stage == "video_gen"
    # 降级不翻失败：无 error_message → _graph_result_is_failed 不命中（bool(err and stage) 双条件）
    assert _graph_result_is_failed({
        "failed_stage": out.failed_stage, "error_message": out.error_message,
    }) is False


def test_scene_exception_degraded_carries_stage(monkeypatch, tmp_path):
    """瞬时异常 → 默认场景降级出口显式带 failed_stage="video_gen"
    （MxouOutOfQuotaError 在节点内 re-raise 不落此分支，v0.63.1 语义不动）。"""
    cfg = _scene_cfg(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("transient network")

    with patch.object(sg, "call_mxou_chat_api", side_effect=_boom):
        out = sg.scene_generation_llm_node(_scene_state(), cfg, SimpleNamespace(context=None))
    assert out.scene_context_2 == "办公室桌面场景"
    assert out.failed_stage == "video_gen"


# ══════════════ 6. _graph_result_is_failed 双形状判定不变（锁 Task2/v0.69 语义） ══════════════

def test_gate_error_plus_stage_shape_still_failed():
    """error_message + failed_stage 显式 → failed（pricing draft=None 出口形状）。"""
    assert _graph_result_is_failed({
        "error_message": "Draft data is None", "failed_stage": "pricing",
    }) is True


def test_gate_upload_status_only_shape_still_failed():
    """纯 upload_status 通道独立判 failed（Task2 blocked 枚举语义一并锁）。"""
    assert _graph_result_is_failed({"upload_status": "failed"}) is True
    assert _graph_result_is_failed({"upload_status": "blocked"}) is True


def test_gate_success_and_degraded_shapes_not_failed():
    """成功归零形状 + 修复收益 + 降级形状都不翻失败。"""
    # 成功：归并后 failed_stage == ""、无错误
    assert _graph_result_is_failed({
        "upload_status": "success", "failed_stage": "", "error_message": "",
    }) is False
    # 修复收益：非 "[" 前缀 error_message + 空 stage 不再误判 failed
    # （粘连时代 stage 恒非空，warning 文案会被误翻 failed）
    assert _graph_result_is_failed({
        "error_message": "Ozon API查询失败: timeout", "failed_stage": "",
    }) is False
    # 降级：stage 有值但无 error → 不翻失败（bool(err and stage) 双条件）
    assert _graph_result_is_failed({"failed_stage": "video_gen"}) is False


# ══════════════ 7. ozon_status_node（终审 I1）：失败显式带，成功/pending/timeout 恒空 ══════════════

_EP_IMPORT_INFO = "/v1/product/import/info"
_EP_INFO_LIST = "/v3/product/info/list"


def _mute_sleep():
    import time
    _orig = time.sleep
    time.sleep = lambda s: None
    return _orig


def _status_state(ozon_task_id="", product_id=""):
    """ozon_status_node 可直接消费的 state（SimpleNamespace，对齐 test_ozon_status_validation）。"""
    return SimpleNamespace(
        ozon_task_id=ozon_task_id,
        product_id=product_id,
        ozon_client_id="1",
        ozon_api_key="k",
        purchase_url="",
        purchase_cost="",
        sku_id="",
        profit_estimation={},
        pricing_info={},
    )


def _run_status(state, import_info=None, info_list=None, import_info_exc=None):
    """按 endpoint 分发 mock ozon_post 后跑节点（静音 sleep，纯内存）。"""
    def _fake(client_id, api_key, endpoint, body, timeout=60, **kw):
        if endpoint == _EP_IMPORT_INFO:
            resp = import_info_exc if import_info_exc is not None else import_info
        else:
            resp = info_list
        if isinstance(resp, Exception):
            raise resp
        return resp

    orig_sleep = _mute_sleep()
    try:
        with patch("graphs.nodes.ozon_status_node.ozon_post", side_effect=_fake):
            return ozon_status_node(state, None, SimpleNamespace(context=None))
    finally:
        import time
        time.sleep = orig_sleep


def test_status_output_default_empty():
    """OzonStatusOutput 默认实例 failed_stage == ""（终审 I1：默认 "ozon_status" 恒粘连，
    CREATE 成功路径也把 "ozon_status" 拼进 GlobalState.failed_stage）。"""
    out = OzonStatusOutput()
    assert out.failed_stage == "", (
        f"OzonStatusOutput.failed_stage 默认值必须为空，实际 {out.failed_stage!r}"
    )


def test_status_success_approved_failed_stage_empty():
    """CREATE 成功（imported → approved）→ failed_stage == ""（粘连根因场景）。"""
    import_info = {"result": {"items": [{"offer_id": "x", "product_id": 5812496806, "status": "imported"}]}}
    info_list = {"items": [{
        "id": 5812496806, "offer_id": "x",
        "statuses": {"validation_status": "success", "is_created": True, "moderate_status": "approved"},
        "errors": [],
    }]}
    out = _run_status(_status_state(ozon_task_id="123"), import_info=import_info, info_list=info_list)
    assert out.status == "imported"
    assert out.moderation_status == "approved"
    assert out.failed_stage == "", f"成功路径 failed_stage 必须为空，实际 {out.failed_stage!r}"


def test_status_missing_product_id_failure_carries_stage():
    """product_id 缺失失败出口显式带 failed_stage="ozon_status"。"""
    out = _run_status(_status_state())
    assert out.status == "failed"
    assert out.error_message == "product_id缺失"
    assert out.failed_stage == "ozon_status"
    assert _graph_result_is_failed({
        "upload_status": out.upload_status,
        "error_message": out.error_message,
        "failed_stage": out.failed_stage,
    }) is True


def test_status_404_without_fallback_failure_carries_stage():
    """import/info 404 且 product_id 非纯数字（无法回退）失败出口显式带 stage。"""
    out = _run_status(
        _status_state(ozon_task_id="123", product_id="123.5"),
        import_info_exc=OzonNotFoundError("task not found"),
    )
    assert out.status == "failed"
    assert "404" in out.error_message
    assert out.failed_stage == "ozon_status"


def test_status_ozon_error_failure_carries_stage():
    """OzonError（非 404）失败出口显式带 failed_stage="ozon_status"。"""
    out = _run_status(
        _status_state(ozon_task_id="123", product_id="123"),
        import_info_exc=OzonError("BOOM", status_code=500),
    )
    assert out.status == "failed"
    assert "500" in out.error_message
    assert out.failed_stage == "ozon_status"


def test_status_exception_failure_carries_stage():
    """未预期异常失败出口显式带 failed_stage="ozon_status"。"""
    out = _run_status(
        _status_state(ozon_task_id="123", product_id="123"),
        import_info_exc=RuntimeError("poll boom"),
    )
    assert out.status == "failed"
    assert out.failed_stage == "ozon_status"


def test_status_timeout_normal_flow_failed_stage_empty():
    """import 轮询超时（零变体导入）→ moderation pending 正常流转（路由层重试），
    不携带失败标记——timeout 不是终态失败。"""
    out = _run_status(_status_state(ozon_task_id="123", product_id=""), import_info={"result": {"items": []}})
    assert out.status == "timeout"
    assert out.moderation_status == "pending"
    assert out.error_message == ""
    assert out.failed_stage == ""


def test_status_non_numeric_pid_pending_failed_stage_empty():
    """product_id 非数字 → 标记 pending 交 validation_retry_loop，正常流转不带失败标记。"""
    out = _run_status(_status_state(ozon_task_id="", product_id="uuid-like"))
    assert out.status == "pending"
    assert out.failed_stage == ""


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"  OK {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
