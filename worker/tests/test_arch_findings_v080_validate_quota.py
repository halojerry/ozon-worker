"""v0.80 arch-findings 回归：validate error_code 透出 + prepare failed_stage 归零
+ OutOfQuota 吞异常回归口三处（docs/ARCHITECTURE/09-findings.md #4/#7/#9）。

修复契约：
  A1  `OzonValidateOutput` 补 `error_code: str = ""`；validate 三个失败出口
      （空 items / critical / 异常）赋 LOCAL_VALIDATION_FAILED，成功恒空。
      GlobalState/GraphOutput 已有同名 channel——Output 声明即透传（AGENTS
      「input schema 纪律」：不声明被 channel 静默吞，wave2/0.77.2 双实证）。
  A2  `PrepareOzonUploadOutput.failed_stage` 默认值归零（v0.73「Output 默认值
      归零」纪律，防 _graph_result_is_failed 双条件误放大）；失败出口显式带
      "prepare_ozon_upload"，成功出口显式空串。
  A3  OutOfQuota 吞异常回归口：prepare A5 段双层 except + attr_fill_extras
      _translate_ru / apply_llm_schema_fill + ai_field_service 四字段 except
      均前置 `except MxouOutOfQuotaError: raise`；drafts_routes 两个调用点映射
      402（对齐 submit_task 余额 402 口径），杜绝裸 500 / 静默 skipped。
  A4  prepare A5 入口 logger.info 记录 LLM_SCHEMA_FILL kill-switch 当前态。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_arch_findings_v080_validate_quota.py -q
纯 mock（LLM/DB/图片探测全 monkeypatch），无需 PG/网络。
"""
import asyncio
import copy
import json
import logging
import os
import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from graphs.state import (
    GlobalState,
    GraphOutput,
    OzonValidateInput,
    OzonValidateOutput,
    PrepareOzonUploadOutput,
)
from graphs.nodes import ozon_validate_node as ovn
from graphs.nodes.ozon_validate_node import ozon_validate_node
from utils.mxou_api import MxouOutOfQuotaError


# ═══════════════ A1: validate error_code ═══════════════

class _Resp:
    def __init__(self, status=200, headers=None, is_redirect=False,
                 is_permanent_redirect=False):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = is_redirect
        self.is_permanent_redirect = is_permanent_redirect


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """默认：图片探测 200、RU 路径空（跳过一致性检查）——validate 用例完全离线。"""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port=None, *a, **k:
            [(2, 1, 6, "", ("93.184.216.34", port or 0))])
    monkeypatch.setattr("requests.request",
                        lambda *a, **k: _Resp(200), raising=False)
    monkeypatch.setattr(ovn, "_fetch_ru_category_path",
                        lambda dc, tp: "", raising=False)


def _run_validate(items):
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c",
        ozon_api_key="k",
        attributes_schema=[],
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


def _valid_item(**over):
    base = {
        "name": "Кружка керамическая с ручкой", "offer_id": "sku1",
        "price": "1990", "old_price": "2390", "vat": "0", "weight": 300,
        "weight_unit": "g", "depth": 100, "width": 100, "height": 50,
        "dimension_unit": "mm",
        "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/file/images/ai-main.jpg"],
        "primary_image": "",
        "description_category_id": 17028653, "type_id": 92147,
        "attributes": [],
    }
    base.update(over)
    return base


def test_validate_error_code_declared_in_output():
    """①error_code 必须声明进 OzonValidateOutput——不声明则被 channel 静默吞
    （v0.77.2 生产 125 行 failed error_code 全空串同型根因）。"""
    assert "error_code" in OzonValidateOutput.model_fields, (
        "OzonValidateOutput 缺 error_code → validate 失败码被 output_schema "
        "过滤吞掉，留存表 error_code 恒空（findings #7）"
    )


def test_validate_error_code_full_channel_chain():
    """②透传链两跳已有同名 channel（GlobalState 写入目标 + GraphOutput 终态）——
    Output 声明即全链生效，缺一跳即断。"""
    assert "error_code" in GlobalState.model_fields, (
        "GlobalState 缺 error_code → validate Output 写入被 channel 丢弃"
    )
    assert "error_code" in GraphOutput.model_fields, (
        "GraphOutput 缺 error_code → 终态看不到（upload 先例已证明两跳都在，"
        "此断言锁不回退）"
    )


def test_validate_empty_items_failure_carries_error_code():
    """③失败出口一（items 为空）：is_valid=False + LOCAL_VALIDATION_FAILED。"""
    out = _run_validate([])
    assert out.is_valid is False
    assert out.error_code == "LOCAL_VALIDATION_FAILED"
    assert out.error_message  # 失败必带人话信息


def test_validate_critical_errors_failure_carries_error_code():
    """④失败出口二（critical 错误：标题缺失）：同带码。"""
    out = _run_validate([_valid_item(name="")])
    assert out.is_valid is False
    assert out.error_code == "LOCAL_VALIDATION_FAILED"
    assert any("缺失" in e for e in out.validation_errors)


def test_validate_exception_exit_carries_error_code(monkeypatch):
    """⑤失败出口三（节点异常）：同带码（wrapper 崩溃等未走子图终态路径时
    留存表仍有码可归因）。"""
    def _boom(*a, **k):
        raise RuntimeError("mock probe failure")
    monkeypatch.setattr(ovn, "is_cos_url", _boom)
    out = _run_validate([_valid_item()])
    assert out.is_valid is False
    assert out.error_code == "LOCAL_VALIDATION_FAILED"
    assert any("预检测异常" in e for e in out.validation_errors)


def test_validate_success_error_code_empty():
    """⑥成功出口 error_code 恒空（不污染成功任务）。"""
    out = _run_validate([_valid_item()])
    assert out.is_valid is True
    assert out.error_code == ""


# ═══════════════ A2: prepare failed_stage 默认归零 ═══════════════

def test_prepare_output_failed_stage_default_zero():
    """⑦PrepareOzonUploadOutput.failed_stage 默认空串（唯一违反「默认值归零」
    纪律的 Output 收口）——默认非空 + error_message 非空会被
    _graph_result_is_failed 双条件放大成 failed。"""
    assert PrepareOzonUploadOutput.model_fields["failed_stage"].default == ""


def _prepare_draft(**over):
    d = {
        "item_id": "testarchf001",
        "title": "Набор салфеток",  # 俄语标题，避免标题翻译分支
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {},
        "sku_id": "testarchf001",
        "price": "1290",
        "original_price": "1490",
        "ozon_category": {"description_category_id": "17028653",
                          "type_id": "92147", "source": "page"},
    }
    d.update(over)
    return d


def _make_prepare_state(draft, pricing_info=None):
    from graphs.state import PrepareOzonUploadInput
    return PrepareOzonUploadInput(
        draft=draft,
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info=pricing_info if pricing_info is not None
        else {"price": 1290, "old_price": 1490, "variant_prices": []},
        description_category_id="17028653",
        type_id="92147",
        final_attributes=[],
        attributes_schema=[],
        dictionary_values={},
        token="sk-test",
        original_images=draft["images"],
        main_image="https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/ai-main.jpg",
        category_match_meta={},
    )


def _base_patches():
    """统一 mock（对齐 test_validate_category_source_v078 手法）：LLM 富文本/
    标题兜底/mxou chat/标题翻译（防网络）。"""
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


def test_prepare_failure_exit_explicit_failed_stage(monkeypatch):
    """⑧失败出口显式 failed_stage="prepare_ozon_upload"（默认归零后不靠默认值）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    monkeypatch.setenv("LLM_SCHEMA_FILL", "0")  # 跳过 A5 LLM，聚焦失败出口
    # price 缺失 → Step 7「价格无效」validation error → 失败出口
    with _patches():
        out = prepare_ozon_upload_node(
            _make_prepare_state(_prepare_draft(), pricing_info={"old_price": 1490}),
            None, None)
    assert out.failed_stage == "prepare_ozon_upload", (
        f"失败出口必须显式带 failed_stage，实际 {out.failed_stage!r}"
    )
    assert out.error_message.startswith("数据准备失败")
    assert any("价格无效" in e for e in out.validation_errors)


def test_prepare_success_exit_failed_stage_empty(monkeypatch):
    """⑨成功出口 failed_stage 恒空（显式空串，成功任务不误标失败阶段）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    monkeypatch.setenv("LLM_SCHEMA_FILL", "0")
    with _patches():
        out = prepare_ozon_upload_node(_make_prepare_state(_prepare_draft()), None, None)
    assert out.failed_stage == ""
    assert out.error_message == ""


# ═══════════════ A3: OutOfQuota 吞异常回归口 ═══════════════

def _raise_oq(*a, **k):
    raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 402)")


def _a5_discriminating_llm(calls):
    """只对 A5 段（system_prompt 含「属性专家」）抛 OutOfQuota 的 mock——
    A5 之前的 LLM 消费点（富文本/消歧/兜底）一律返回空串，保证余额错误
    只能从 A5 出口产生（隔离断言靶点）。calls 记录每次 system_prompt。"""

    def _fake(*a, **k):
        sp = str(k.get("system_prompt") or (a[1] if len(a) > 1 else ""))
        calls.append(sp)
        if "属性专家" in sp:
            raise MxouOutOfQuotaError("OUT_OF_QUOTA: MXOU chat API rejected (HTTP 402)")
        return ""

    return _fake


def test_translate_ru_out_of_quota_reraise(monkeypatch):
    """⑩_translate_ru 余额错误上抛（此前吞成空串「剥除处理」静默降级）。"""
    from utils.attr_fill_extras import _translate_ru
    monkeypatch.setattr("utils.mxou_api.call_mxou_chat_api", _raise_oq)
    with pytest.raises(MxouOutOfQuotaError):
        _translate_ru("取适量本品涂抹于手部", "tok")


def test_translate_ru_normal_failure_still_degrades(monkeypatch):
    """⑪普通异常（网络/坏响应）保持既有降级语义：空串=调用方照剥，宁缺不变。"""
    from utils.attr_fill_extras import _translate_ru

    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr("utils.mxou_api.call_mxou_chat_api", _boom)
    assert _translate_ru("取适量本品涂抹于手部", "tok") == ""


def test_apply_llm_schema_fill_out_of_quota_reraise(monkeypatch):
    """⑫apply_llm_schema_fill 对余额错误透传（其 except Exception 不再吞）。"""
    from utils.attr_fill_extras import apply_llm_schema_fill
    monkeypatch.setattr(
        "utils.attr_fill_extras._translate_ru",
        _raise_oq,
    )
    items = [{"attributes": []}]
    todo = [{"id": 8048, "name": "Применение", "type": "String",
             "dict": 0, "collection": False}]
    raw = json.dumps({"fills": [{"id": 8048, "value": "取适量涂抹"}]}, ensure_ascii=False)
    with pytest.raises(MxouOutOfQuotaError):
        apply_llm_schema_fill(items, todo, raw, {}, type("S", (), {"description_category_id": "1", "type_id": "2", "ozon_client_id": "c", "ozon_api_key": "k", "user_id": "t", "token": "tk", "task_id": "x"})())


def test_prepare_a5_out_of_quota_propagates(monkeypatch):
    """⑬prepare A5 段双层 except 前置放行：余额错误穿透整段补齐链抛出节点
      （内层 re-raise 后若被外层「必填字典属性补齐异常」吞回，任务照跑白烧）。
      隔离：基础 patch 压住 A5 之前的 LLM 消费点（富文本/标题兜底），vision 推断
      置恒等——确保 OutOfQuota 只能从 A5 出口抛出。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    monkeypatch.delenv("LLM_SCHEMA_FILL", raising=False)  # 默认开
    state = _make_prepare_state(_prepare_draft())
    # 非 schema 属性留一个未填项 → build_llm_schema_prompt 非空 → 触达 A5 LLM 调用
    state.attributes_schema = [
        {"id": 100001, "name": "Материал покрытия", "type": "String",
         "dictionary_id": 0, "description": "", "is_required": False},
    ]
    with _patches(
        patch("utils.mxou_api.call_mxou_chat_api", _a5_discriminating_llm([])),
        patch("graphs.nodes.prepare_ozon_upload_node._infer_attrs_from_vision",
              lambda items, *a, **k: items),
    ), pytest.raises(MxouOutOfQuotaError):
        prepare_ozon_upload_node(state, None, None)


# ── A3③: ai_field_service / drafts_routes 余额错误不再 500 / 不再静默 skipped ──

class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def body(self):
        return json.dumps(self._body).encode("utf-8")


_VALID_PAYLOAD = {
    "draft": {"title": "跨境爆款 宠物自动饮水器 静音循环过滤"},
    "source": {"purchase_url": "https://detail.1688.com/offer/1.html"},
}


def _auth_open(monkeypatch):
    import main
    monkeypatch.setattr(main, "get_supabase_client", lambda: None)  # 本地鉴权放行


def test_ai_field_endpoint_out_of_quota_returns_402(monkeypatch):
    """⑭单字段端点：余额错误 → 402（此前 MxouOutOfQuotaError 裸穿路由 = 500）。"""
    from fastapi import HTTPException
    from routes import drafts_routes
    from services import ai_field_service
    _auth_open(monkeypatch)
    monkeypatch.setattr(drafts_routes, "_load_draft_payload",
                        lambda draft_id, tenant_id: copy.deepcopy(_VALID_PAYLOAD))
    monkeypatch.setattr(ai_field_service, "call_mxou_chat_api", _raise_oq)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(drafts_routes.draft_ai_field(
            "11111111-1111-1111-1111-111111111111", "title",
            FakeRequest({"token": "sk-x"})))
    assert ei.value.status_code == 402
    assert "OUT_OF_QUOTA" in str(ei.value.detail) or "余额" in str(ei.value.detail)


def test_ai_field_service_assemble_out_of_quota_raises(monkeypatch):
    """⑮预组装服务层：余额错误上抛（此前四字段 except 吞成 skipped 假装成功）。"""
    from services import ai_field_service
    monkeypatch.setattr(ai_field_service, "call_mxou_chat_api", _raise_oq)
    with pytest.raises(MxouOutOfQuotaError):
        ai_field_service.assemble_draft(copy.deepcopy(_VALID_PAYLOAD), "tok")


def test_ai_field_service_assemble_normal_failure_still_skips(monkeypatch):
    """⑯预组装普通异常保持降级语义：字段 skipped，不阻断（宁缺红线不变）。"""
    from services import ai_field_service

    def _boom(*a, **k):
        raise RuntimeError("llm timeout")
    monkeypatch.setattr(ai_field_service, "call_mxou_chat_api", _boom)
    payload = copy.deepcopy(_VALID_PAYLOAD)
    result = ai_field_service.assemble_draft(payload, "tok")
    assert "title" in result["skipped"]
    assert result["assembled"] == []
    assert payload["draft"]["title"] == _VALID_PAYLOAD["draft"]["title"]  # 原值不动


def test_assemble_endpoint_out_of_quota_returns_402(monkeypatch):
    """⑰预组装端点：余额错误 → 402（服务层上抛后路由显式映射，杜绝裸 500）。"""
    from fastapi import HTTPException
    from routes import drafts_routes
    from services import draft_service
    _auth_open(monkeypatch)
    monkeypatch.setattr(draft_service, "assemble_draft", _raise_oq)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(drafts_routes.draft_assemble(
            "11111111-1111-1111-1111-111111111111", FakeRequest({"token": "sk-x"})))
    assert ei.value.status_code == 402


# ═══════════════ A4: LLM_SCHEMA_FILL 开关留痕 ═══════════════

def test_a5_entry_logs_kill_switch_state(monkeypatch, caplog):
    """⑱A5 入口 logger.info 显式记录 LLM_SCHEMA_FILL 当前态（kill-switch 语义：
    设 0 关、默认开——部署核对/排错不用猜 A5 是否生效）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    monkeypatch.delenv("LLM_SCHEMA_FILL", raising=False)  # 未设=默认开
    state = _make_prepare_state(_prepare_draft())
    state.attributes_schema = [
        {"id": 100001, "name": "Материал покрытия", "type": "String",
         "dictionary_id": 0, "description": "", "is_required": False},
    ]
    # A5 LLM 调用用判别 mock 快速出口；基础 patch 压住 A5 之前的 LLM 消费点，
    # 日志行必在 A5 入口产生。
    calls: list = []
    with caplog.at_level(logging.INFO, logger="graphs.nodes.prepare_ozon_upload_node"), _patches(
        patch("utils.mxou_api.call_mxou_chat_api", _a5_discriminating_llm(calls)),
        patch("graphs.nodes.prepare_ozon_upload_node._infer_attrs_from_vision",
              lambda items, *a, **k: items),
    ), pytest.raises(MxouOutOfQuotaError):
        prepare_ozon_upload_node(state, None, None)
    logs = [r.getMessage() for r in caplog.records if "LLM_SCHEMA_FILL" in r.getMessage()]
    assert logs, "A5 入口必须留一行开关态日志"
    assert "1" in logs[0] and "启用" in logs[0], f"日志需含当前值与态: {logs[0]}"


def test_a5_entry_logs_kill_switch_off(monkeypatch, caplog):
    """⑲kill-switch 生效（=0）时日志如实记「关闭」，且 A5 段不发起 LLM 调用。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    monkeypatch.setenv("LLM_SCHEMA_FILL", "0")
    state = _make_prepare_state(_prepare_draft())
    state.attributes_schema = [
        {"id": 100001, "name": "Материал покрытия", "type": "String",
         "dictionary_id": 0, "description": "", "is_required": False},
    ]
    calls: list = []
    with caplog.at_level(logging.INFO, logger="graphs.nodes.prepare_ozon_upload_node"), _patches(
        patch("utils.mxou_api.call_mxou_chat_api", _a5_discriminating_llm(calls)),
    ):
        out = prepare_ozon_upload_node(state, None, None)
    assert out.failed_stage == ""  # 正常走完
    logs = [r.getMessage() for r in caplog.records if "LLM_SCHEMA_FILL" in r.getMessage()]
    assert logs and "关闭" in logs[0], f"kill-switch 关闭态必须如实留痕: {logs}"
    assert not any("属性专家" in sp for sp in calls), (
        f"LLM_SCHEMA_FILL=0 时 A5 不得发起 LLM 调用，实际调用: {calls}"
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
