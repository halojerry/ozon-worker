# -*- coding: utf-8 -*-
"""
v0.77.2 — failed 终态出口 error_code 接线（纯 mock，无 PG / 无网络 / 无 LLM）。

生产实证（2026-09-18）：listing_result_log 表 239 条 failed 行里 **226 条（94.6%）
error_code 为空**——错误消息 Top2「类目匹配失败-LLM fallback 无可靠结果」78 条 +
「低置信经 LLM 仍无匹配」53 条（failed_stage=category_match、upload_status=blocked、
商品已入采集箱——设计性阻断但没有错误码）；价差守卫入箱、敏感/受限类目阻断等出口
同样无码。此前已有先例 LOCAL_TITLE_CATEGORY_MISMATCH 是有码阻断（说明通道可用，
只是大部分出口没接），但该码生产也从未落库——根因见下。

根因（LOCAL_TITLE_CATEGORY_MISMATCH 空串）：该码只写进 validation_retry_loop 子图
state.error_code；子图 output_schema=ValidationRetryLoopOutput 与包装器节点 output
=ValidationRetryWrapperOutput **都未声明 error_code 字段**，且 final_result /
_final_result_blocked_to_box 两个 return 与 wrapper 都不透传该字段——langgraph 按
output_schema 过滤 channel，字段被静默吞掉，GlobalState/GraphOutput.error_code 恒空
→ listing_result_log.error_code 恒空。与 v0.77.1 OzonUploadOutput.error_code 同类根因。

契约（本文件锁定）：
- 每个终态 failed/blocked 出口的返回必须带非空 error_code（LOCAL_ 前缀自由字符串），
  经 GlobalState.error_code → GraphOutput.error_code → listing_result_log.error_code 落库；
- 需要透传的节点 Output model 必须声明 error_code（GlobalState/GraphOutput 已有同名
  channel——加字段即透传，参照 v0.77.1 OzonUploadOutput 先例）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_error_code_wiring_v0772.py -q
"""
import inspect
import contextlib
import os
import sys
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from graphs.nodes import assemble_ozon_product_node as asm  # noqa: E402
from graphs import validation_retry_loop as vrl  # noqa: E402
from graphs import state as gstate  # noqa: E402


# ═══════════════ A. Output schema 声明（根因 #3：channel 过滤吞 error_code）═══════════════

def test_retry_and_pricing_output_schemas_declare_error_code():
    """子图/包装器/Pricing 的 Output model 必须声明 error_code——否则 langgraph
    按 output_schema 过滤 channel，节点写的 error_code 永远到不了 GlobalState。"""
    for model in (vrl.ValidationRetryLoopOutput,
                  gstate.ValidationRetryWrapperOutput,
                  gstate.PricingOutput,
                  gstate.OzonUploadOutput):
        assert "error_code" in model.model_fields, \
            f"{model.__name__} 必须声明 error_code（channel 过滤根因）"


# ═══════════════ B. assemble 阻断 funnel（纯函数行为）═══════════════

class _FakeState:
    """模拟 GlobalState — 只含 assemble 主路径 + 入箱读取的字段。"""

    def __init__(self, title, envelope=None):
        self.draft = {"item_id": "gz-1", "sku_id": "gz-1", "title": title,
                      "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
                      "weight": 500, "dimensions": {"length": 200, "width": 120, "height": 80},
                      "purchase_cost": 10.0, "purchase_url": "https://detail.1688.com/1.html"}
        self.token = ""
        self.ozon_client_id = "c"
        self.ozon_api_key = "k"
        self.currency_code = "RUB"
        self.pricing_info = {"price": "990", "old_price": "1290"}
        self.envelope = envelope if envelope is not None else {
            "draft": self.draft, "source": {}, "extensions": {}}
        self.source = {}
        self.user_id = "u-1"
        self.task_id = ""  # 空 → _log_match_attempt 直接跳过（不触 PG）
        self.assembly_retry_count = 0


class _FakeQuery:
    """search_nodes 返回预置候选；其余方法不触网/不触 PG。"""

    def __init__(self, candidates):
        self._candidates = candidates
        self.schema_calls = 0

    def search_nodes(self, *_a, **_k):
        return list(self._candidates)

    def score_candidates_by_fingerprint(self, candidates, _kw):
        return candidates

    def get_attribute_schema(self, *_a, **_k):
        self.schema_calls += 1
        return None

    def get_dictionary_values(self, *_a, **_k):
        return []


_CAN_CAND = {"description_category_id": 99990001, "type_id": 88880001,
             "node_name": "汽油桶", "full_path": "汽车用品 > 汽车油品容器 > 汽油桶",
             "similarity": 0.9, "matcher": "jieba"}
_PLAIN_CAND = {"description_category_id": 99990002, "type_id": 88880002,
               "node_name": "手提桶", "full_path": "日用杂货 > 塑料容器 > 手提桶",
               "similarity": 0.9, "matcher": "jieba"}


def test_blocked_exit_carries_category_match_failed():
    """_blocked_exit（类目匹配阻断统一 funnel，覆盖无候选/LLM fallback/低置信等
    6+ 出口）默认 error_code=LOCAL_CATEGORY_MATCH_FAILED。"""
    state = _FakeState("测试商品")
    with mock.patch("utils.blocked_draft_box.create_blocked_draft", return_value=None):
        out = asm._blocked_exit(state, state.draft, [], "类目匹配失败：无候选类目")
    assert out["error_code"] == "LOCAL_CATEGORY_MATCH_FAILED", \
        f"_blocked_exit 必须带 LOCAL_CATEGORY_MATCH_FAILED，实际 {out.get('error_code')!r}"
    assert out["failed_stage"] == "category_match"


def test_restricted_category_exit_carries_restricted_code():
    """受限/需资质品类双命中出口（_restricted_category_exit）→ LOCAL_RESTRICTED_CATEGORY
    （与 R1 敏感线独立，勿混）。"""
    state = _FakeState("汽油桶 20升")
    with mock.patch("utils.blocked_draft_box.create_blocked_draft",
                    return_value={"draft_id": "d-9", "reused": False,
                                  "top1_name": "", "top1_confidence": 0.0}):
        out = asm._restricted_category_exit(state, state.draft, [_CAN_CAND],
                                            ["汽油"], ["汽油"], match_confidence=0.9)
    assert out["error_code"] == "LOCAL_RESTRICTED_CATEGORY", \
        f"受限品类出口必须带 LOCAL_RESTRICTED_CATEGORY，实际 {out.get('error_code')!r}"
    assert out["failed_stage"] == "category_match"


# ═══════════════ C. assemble 主节点各阻断出口（全节点 + mock 依赖）═══════════════

def _run_assemble(title, candidates, fake_state, extra_patches=()):
    query = _FakeQuery(candidates)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(asm, "get_category_query", return_value=query))
        stack.enter_context(
            mock.patch.object(asm, "_fetch_attribute_schema_from_ozon", return_value=[]))
        stack.enter_context(mock.patch.object(asm, "_llm_rank_categories", return_value=None))
        stack.enter_context(
            mock.patch("utils.blocked_draft_box.create_blocked_draft", return_value=None))
        for p in extra_patches:
            stack.enter_context(p)
        return asm.assemble_ozon_product_node(fake_state, {}, None)


def test_title_empty_exit_carries_local_title_empty():
    """标题为空阻断（assemble 最早出口）→ LOCAL_TITLE_EMPTY。"""
    state = _FakeState("")
    out = _run_assemble("", [], state)
    assert out.get("failed_stage") == "category_match"
    assert out.get("error_code") == "LOCAL_TITLE_EMPTY", \
        f"标题为空出口必须带 LOCAL_TITLE_EMPTY，实际 {out.get('error_code')!r}"


def test_no_candidates_exit_carries_category_match_failed():
    """类目搜索零候选（Ozon API 也无数据）→ LOCAL_CATEGORY_MATCH_FAILED。"""
    state = _FakeState("某不存在的商品")
    out = _run_assemble("某不存在的商品", [], state,
                        extra_patches=[mock.patch.object(
                            asm, "_fetch_category_tree_from_ozon", return_value=None)])
    assert out.get("failed_stage") == "category_match"
    assert out.get("error_code") == "LOCAL_CATEGORY_MATCH_FAILED", \
        f"零候选出口必须带 LOCAL_CATEGORY_MATCH_FAILED，实际 {out.get('error_code')!r}"


def test_restricted_double_hit_exit_carries_restricted_code():
    """受限双命中全链路（节点内调用 _restricted_category_exit）→ LOCAL_RESTRICTED_CATEGORY。"""
    state = _FakeState("汽油桶 20升 加油桶")
    out = _run_assemble("汽油桶 20升 加油桶", [_CAN_CAND], state)
    assert out.get("failed_stage") == "category_match"
    assert out.get("error_code") == "LOCAL_RESTRICTED_CATEGORY", \
        f"受限双命中出口必须带 LOCAL_RESTRICTED_CATEGORY，实际 {out.get('error_code')!r}"


def test_r1_veto_exit_carries_sensitive_code():
    """R1 敏感类目无敏感源词 veto（定稿采纳点）→ LOCAL_SENSITIVE_CATEGORY。
    用 patch 令 _r1_veto 命中（避免构造真实敏感候选链），验证出口本身的 error_code。"""
    state = _FakeState("普通塑料手提桶")
    out = _run_assemble("普通塑料手提桶", [_PLAIN_CAND], state,
                        extra_patches=[mock.patch.object(asm, "_r1_veto", return_value=True)])
    assert out.get("failed_stage") == "category_match"
    assert out.get("error_code") == "LOCAL_SENSITIVE_CATEGORY", \
        f"敏感类目 veto 出口必须带 LOCAL_SENSITIVE_CATEGORY，实际 {out.get('error_code')!r}"


def test_follow_type_id_invalid_exit_carries_code():
    """跟卖 CREATE 无有效 type_id（无法建卡）→ LOCAL_CATEGORY_MATCH_FAILED +
    failed_stage=category_match（此前两者皆无 → 路由误判成功继续空跑）。"""
    class _FollowState:
        description_category_id = "17028830"
        type_id = "0"
        ozon_client_id = "1"
        ozon_api_key = "k"
        currency_code = "RUB"
        product_id = None

    state = _FollowState()
    draft = {"item_id": "offer-x", "weight": 300,
             "dimensions": {"length": 100, "width": 100, "height": 50}}

    class P:
        def log_node_action(self, *a, **k): pass

    with mock.patch("utils.ozon_category_query.get_category_query"):
        out = asm._assemble_follow_sell(state, draft, "Тест товар",
                                        ["http://img/1.jpg"], {"price": "1990", "old_price": "2390"}, P())
    assert out.get("error_code") == "LOCAL_CATEGORY_MATCH_FAILED", \
        f"跟卖 type_id 无效出口必须带 LOCAL_CATEGORY_MATCH_FAILED，实际 {out.get('error_code')!r}"
    assert out.get("failed_stage") == "category_match"


def test_inline_assembly_exit_codes_source_locked():
    """属性 schema 全失败 / 确定性组装空 items 两个内联出口——源级锁定 error_code
    （两者当前未带 failed_stage，非终态直达；补码为取证留痕）。"""
    src = inspect.getsource(asm)
    for marker, code in (
        ('"error_message": f"属性 Schema 获取失败', "LOCAL_ATTRIBUTE_SCHEMA_FAILED"),
        ('"error_message": "确定性组装失败：未生成有效的 items"', "LOCAL_ASSEMBLY_EMPTY_ITEMS"),
    ):
        i = src.index(marker)
        region = src[max(0, i - 120):i + 300]
        assert f'"error_code": "{code}"' in region, \
            f"出口「{marker}」region 缺 error_code={code}"


# ═══════════════ D. pricing 价差守卫 + 定价异常 ═══════════════

def _pg():
    import test_price_sanity_guard_v073 as pg
    return pg


def test_price_gap_block_carries_code(monkeypatch):
    """价差守卫 block 入箱出口（PricingOutput，failed_stage=pricing）→
    LOCAL_PRICE_GAP_BLOCKED。"""
    pg = _pg()
    monkeypatch.setattr("utils.blocked_draft_box.create_blocked_draft",
                        lambda *a, **k: {"draft_id": "box-1", "reused": False,
                                         "top1_name": "", "top1_confidence": 0.0})
    ctrl = pg._call_pricing(monkeypatch, pg._make_state())
    anchor = int(ctrl.price) * 12
    out = pg._call_pricing(monkeypatch, pg._make_state(envelope=pg._envelope_with_anchor(anchor)))
    assert out.failed_stage == "pricing"
    assert out.error_code == "LOCAL_PRICE_GAP_BLOCKED", \
        f"价差守卫出口必须带 LOCAL_PRICE_GAP_BLOCKED，实际 {out.error_code!r}"


def test_pricing_exception_exit_carries_code(monkeypatch):
    """定价计算异常兜底出口（[PRICING_FAILED]）→ LOCAL_PRICING_FAILED。"""
    pg = _pg()
    import utils.pricing_estimate as pe

    def _boom(*a, **k):
        raise RuntimeError("fx boom")

    monkeypatch.setattr(pe, "compute_price", _boom)
    out = pg._call_pricing(monkeypatch, pg._make_state())
    assert out.failed_stage == "pricing"
    assert out.error_code == "LOCAL_PRICING_FAILED", \
        f"定价异常出口必须带 LOCAL_PRICING_FAILED，实际 {out.error_code!r}"


# ═══════════════ E. validation_retry_loop 终态 + wrapper 透传 ═══════════════

def _loop_state(**kw):
    base = dict(
        ozon_payload={}, validation_errors=[], errors=[], error_message="",
        draft={}, token="", ozon_client_id="", ozon_api_key="",
        description_category_id="", type_id="", task_id="",
        final_attributes=[], attributes_schema=[], category_match_meta={},
        decline_errors=[], retry_count=0, max_retries=3,
        is_valid=False, upload_status="", product_id="", moderation_status="",
        error_code="", error_type="", user_id="u-1", envelope={},
        needs_recategorization=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_final_result_blocked_to_box_carries_title_mismatch_code():
    """LOCAL_TITLE_CATEGORY_MISMATCH 入箱拦截出口 → 输出 error_code 必须等于该码
    （根因：ValidationRetryLoopOutput 未声明 error_code，字段被子图 output_schema 吞掉）。"""
    state = _loop_state(error_code="LOCAL_TITLE_CATEGORY_MISMATCH", upload_status="blocked")
    with mock.patch.object(asm, "_maybe_create_blocked_draft", return_value=None):
        out = vrl._final_result_blocked_to_box(state)
    assert out.error_code == "LOCAL_TITLE_CATEGORY_MISMATCH", \
        f"入箱拦截出口必须透出 LOCAL_TITLE_CATEGORY_MISMATCH，实际 {out.error_code!r}"


def test_final_result_passes_through_state_error_code():
    """常规 retry 终态失败（如 DESCRIPTION_DECLINE）→ final_result 透出 state.error_code。"""
    state = _loop_state(error_code="DESCRIPTION_DECLINE", error_message="declined",
                        upload_status="failed", error_type="unfixable")
    with mock.patch.object(vrl, "_mark_category_negative_feedback"):
        out = vrl.final_result(state)
    assert out.error_code == "DESCRIPTION_DECLINE", \
        f"final_result 必须透出 state.error_code，实际 {out.error_code!r}"


def test_final_result_success_clears_error_code():
    """上传成功终态 → error_code 清空（不得把历史错误码带到成功任务）。"""
    state = _loop_state(error_code="DESCRIPTION_DECLINE", upload_status="success", is_valid=True)
    with mock.patch.object(vrl, "_mark_category_negative_feedback"):
        out = vrl.final_result(state)
    assert out.error_code == "", f"成功终态 error_code 必须清空，实际 {out.error_code!r}"


def test_needs_recategorization_sets_local_code():
    """R4 重配无解硬收敛终态失败 → LOCAL_CATEGORY_RECATEGORIZE_FAILED。"""
    state = _loop_state(needs_recategorization=True)
    verdict = vrl.should_continue(state)
    assert verdict == "exit"
    assert getattr(state, "error_code", "") == "LOCAL_CATEGORY_RECATEGORIZE_FAILED", \
        f"needs_recategorization 终态必须带码，实际 {getattr(state, 'error_code', '')!r}"


def test_wrapper_forwards_subgraph_error_code():
    """包装器节点必须把子图 output 的 error_code 透传到 ValidationRetryWrapperOutput
    （第二个吞字段边界）。"""
    from graphs.nodes import validation_retry_wrapper_node as vw

    state = gstate.ValidationRetryWrapperInput()
    subgraph_out = {"is_valid": False, "upload_status": "blocked",
                    "error_code": "LOCAL_TITLE_CATEGORY_MISMATCH",
                    "error_message": "标题与类目不符", "errors": [], "decline_errors": []}
    fake_graph = mock.Mock()
    fake_graph.invoke.return_value = subgraph_out
    with mock.patch.object(vw, "validation_retry_loop", fake_graph):
        out = vw.validation_retry_wrapper_node(state, None, SimpleNamespace(context=None))
    assert out.error_code == "LOCAL_TITLE_CATEGORY_MISMATCH", \
        f"wrapper 必须透传子图 error_code，实际 {out.error_code!r}"


# ═══════════════ F. retry 子图上传/轮询基础设施失败出口 ═══════════════

def test_recheck_status_no_task_id_carries_code():
    """recheck_status task_id 为空 → upload_status=failed + LOCAL_UPLOAD_NO_TASK_ID。"""
    out = vrl.recheck_status_node(_loop_state(task_id="", upload_status="uploaded"))
    assert out.upload_status == "failed"
    assert out.error_code == "LOCAL_UPLOAD_NO_TASK_ID", \
        f"task_id 为空出口必须带码，实际 {out.error_code!r}"


def test_recheck_status_uuid_task_id_carries_code():
    """recheck_status 仍为系统 UUID（上传失败未覆盖）→ LOCAL_UPLOAD_NO_TASK_ID。"""
    out = vrl.recheck_status_node(_loop_state(
        task_id="550e8400-e29b-41d4-a716-446655440000", upload_status="uploaded"))
    assert out.upload_status == "failed"
    assert out.error_code == "LOCAL_UPLOAD_NO_TASK_ID"


def test_full_import_create_failure_carries_code():
    """全量 CREATE 重传异常 → LOCAL_REUPLOAD_FAILED（成功子图前置码保留优先）。"""
    state = _loop_state(ozon_payload={"items": [{"price": "100"}]}, upload_status="")
    with mock.patch.object(vrl, "ozon_post", side_effect=RuntimeError("ozon down")):
        out = vrl._full_import_create(state)
    assert out.upload_status == "failed"
    assert out.error_code == "LOCAL_REUPLOAD_FAILED", \
        f"重传失败出口必须带码，实际 {out.error_code!r}"


def test_repair_pricing_no_price_carries_code():
    """repair_pricing 无有效定价阻断（[PRICING_FAILED]）→ 空码时补 LOCAL_PRICING_FAILED。"""
    state = _loop_state(ozon_payload={"items": [{"price": "0"}]}, pricing_info={},
                        error_code="")
    out = vrl.repair_pricing_node(state)
    assert out.failed_stage == "pricing"
    assert out.error_code == "LOCAL_PRICING_FAILED", \
        f"无有效定价阻断必须带码，实际 {out.error_code!r}"


# ═══════════════ G. 端到端契约：GlobalState/GraphOutput 通道在位 ═══════════════

def test_error_code_channel_reaches_graph_output():
    """error_code 通道：GlobalState → GraphOutput 同名透出在位（无 merge reducer，
    last-write-wins）。"""
    assert "error_code" in gstate.GlobalState.model_fields
    assert "error_code" in gstate.GraphOutput.model_fields
