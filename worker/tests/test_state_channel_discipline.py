#!/usr/bin/env python3
"""State 通道纪律检测器（W1 治理，根修「字段静默丢失」类事故）。

机制（实证，见 graphs/state.py 各「channel 过滤」注释）：
  1. langgraph 按节点第一参数的 Input model **静默过滤**流入节点/路由的 state——
     节点 body 里 `state.X` / `getattr(state, "X")` 读到未声明字段时拿到的恒是
     None/默认值，无任何运行时告警。历史上至少两起生产死代码由此而来：
     - learning_record 的 `source`（v0.66 才补声明，写侧此前整段失效）
     - ozon_upload 的 `import_submitted`（v0.69 T2.2 才补声明，守卫恒 False）
  2. 条件边路由函数收到的是 **source 节点 Input 过滤后的 state**（v0.25
     OzonStatusInput 实证、v0.69 AuthInput 实证）——路由要读的字段必须声明进
     source 节点的 Input。

本文件把上述两条纪律变成 CI 硬闸：
  - 扫描全部 StateGraph builder（graphs/graph.py 主图 + validation_retry_loop
    子图）的 add_node / add_conditional_edges 注册；
  - 节点函数第一参数必须有 Input/State model 注解（裸 `state` = 违规）；
  - AST 收集节点/路由 body 内对 state 参数的字段读取，逐一核对声明；
  - 路由按其 source 节点的 Input 校验，节点按自身 Input，GlobalState 注解按
    GlobalState（宽松语义不变）。

已知边界（v1 刻意不覆盖，避免误报）：
  - 节点把 state 传给 helper 函数后的二次读取（如 `_blocked_exit(state, …)` 内部）
    不在本扫描内——helper 读取应在其所属节点的 Input 补声明，靠 review 兜底；
  - 未注册为 path 的独立函数（如 graph.route_by_sell_type 被 route_after_early_quota
    内部调用）不被扫描；它的读取语义上等于宿主路由的读取，新增此类调用时
    需人工确认字段在 source Input 中。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_state_channel_discipline.py -q
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

from pydantic import BaseModel

from graphs import graph as graph_module
from graphs import validation_retry_loop as retry_module
from graphs.state import GlobalState

BUILDER_MODULES = (graph_module, retry_module)


# ────────────────────────── 检测器核心 ──────────────────────────

def _annotation_of(fn) -> Any:
    """解析函数第一参数注解（兼容 from __future__ import annotations 的字符串形态）。

    eval_str 解析失败时回落裸注解——字符串注解会被 _classify_node 判为
    ANNOTATION_NOT_MODEL（fail-closed，人工介入），绝不静默放行。
    """
    try:
        sig = inspect.signature(fn, eval_str=True)
    except Exception:
        sig = inspect.signature(fn)
    first = list(sig.parameters)[0]
    return sig.parameters[first].annotation


def _first_param_name(fn) -> str:
    return list(inspect.signature(fn).parameters)[0]


def _collect_state_reads(fn) -> set[str]:
    """收集函数体内对 state 参数的字段读取：state.X / getattr(state, "X") / hasattr(state, "X")。"""
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    state_name = _first_param_name(fn)
    reads: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == state_name
            and not node.attr.startswith("_")
            # pydantic 方法调用（state.model_dump() 等）不是字段读取
            and not hasattr(BaseModel, node.attr)
        ):
            reads.add(node.attr)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("getattr", "hasattr")
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == state_name
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ):
            field = str(node.args[1].value)
            if not field.startswith("_"):
                reads.add(field)
    return reads


def _allowed_fields(model) -> set[str]:
    return set(getattr(model, "model_fields", {}).keys())


def _classify_node(fn) -> tuple[Any, str | None]:
    """返回 (Input model 或 None, 违规类别)。类别为 None 表示节点注解合法。"""
    ann = _annotation_of(fn)
    if ann is inspect.Parameter.empty:
        return None, "UNANNOTATED_NODE"
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return ann, None
    return None, "ANNOTATION_NOT_MODEL"


def _scan_builder_module(mod) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """AST 扫描 builder 模块：返回 (节点名→函数名, [(路由函数名, source 节点名)])。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(mod)))
    nodes: dict[str, str] = {}
    routers: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        if method == "add_node":
            if len(node.args) >= 2 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[1], ast.Name):
                nodes[node.args[0].value] = node.args[1].id
        elif method == "add_conditional_edges":
            args = node.args
            kwargs = {k.arg: k.value for k in node.keywords}
            source = None
            path = None
            if "source" in kwargs and isinstance(kwargs["source"], ast.Constant):
                source = kwargs["source"].value
            elif args and isinstance(args[0], ast.Constant):
                source = args[0].value
            if "path" in kwargs and isinstance(kwargs["path"], ast.Name):
                path = kwargs["path"].id
            elif len(args) >= 2 and isinstance(args[1], ast.Name):
                path = args[1].id
            if source is not None and path is not None:
                routers.append((path, source))
    return nodes, routers


def collect_violations() -> list[str]:
    """全量扫描两个 builder，返回人类可读的违规清单（空 = 通过）。"""
    out: list[str] = []
    for mod in BUILDER_MODULES:
        mod_name = mod.__name__
        node_defs, routers = _scan_builder_module(mod)
        node_models: dict[str, Any] = {}
        # 1) 节点：注解检查 + 读取核对
        for node_name, fn_name in sorted(node_defs.items()):
            fn = getattr(mod, fn_name, None)
            if fn is None:
                out.append(f"[RESOLVE_FAIL] {mod_name}: 节点 {node_name} 的函数 {fn_name} 不可解析")
                continue
            model, kind = _classify_node(fn)
            if kind is not None:
                out.append(f"[{kind}] {mod_name}: 节点 {node_name}（{fn_name}）第一参数缺 Input/State model 注解")
                continue
            node_models[node_name] = model
            if model is GlobalState:
                continue  # 宽松语义：全量 state，逐字段核对无意义
            undeclared = _collect_state_reads(fn) - _allowed_fields(model)
            for field in sorted(undeclared):
                out.append(
                    f"[UNDECLARED_READ] {mod_name}: 节点 {node_name}（{fn_name}）读 state.{field}，"
                    f"未声明进 {model.__name__}（运行时被 channel 静默剥掉，恒 None）"
                )
        # 2) 路由：按 source 节点的 Input 校验
        for path_name, source_node in sorted(routers):
            fn = getattr(mod, path_name, None)
            if fn is None:
                out.append(f"[RESOLVE_FAIL] {mod_name}: 路由 {path_name} 不可解析")
                continue
            source_model = node_models.get(source_node)
            if source_model is None:
                # source 节点未注解/非 model（已在上一步报过）或本身是 GlobalState → 跳过字段核对
                continue
            undeclared = _collect_state_reads(fn) - _allowed_fields(source_model)
            for field in sorted(undeclared):
                out.append(
                    f"[ROUTER_UNDECLARED_READ] {mod_name}: 路由 {path_name}（source={source_node}）"
                    f"读 state.{field}，未声明进 {source_model.__name__}"
                )
    return out


# ────────────────────────── 硬闸 ──────────────────────────

def test_graph_state_channel_discipline():
    """全部节点/路由的 state 读取必须声明进对应 Input model（CI 硬闸）。

    新增违规的处理方式：把字段补进对应节点的 Input model（行为影响 = 字段从
    恒 None 变真值，需过全量测试）；若字段在 GlobalState 也不存在，则该读取
    是死代码，应删除读取而非补声明。
    """
    violations = collect_violations()
    assert not violations, (
        "\n═══ state 通道纪律违规（channel 静默过滤会把未声明字段剥成 None）═══\n"
        + "\n".join(violations)
        + "\n═══ 修法：字段补进对应节点 Input（graphs/state.py）；GlobalState 也没有的字段 = 死代码，删读取 ═══"
    )


def test_builder_surface_is_pinned():
    """钉住扫描面：builder 模块增减时本闸必须知情（防新 builder 绕过检测）。"""
    counts = {}
    for mod in BUILDER_MODULES:
        nodes, routers = _scan_builder_module(mod)
        counts[mod.__name__] = (len(nodes), len(routers))
    # graph.py: 26 节点 / 9 条件边；validation_retry_loop: 11 节点 / 4 条件边。
    # 变更这两个数时请同步更新——这是「检测器覆盖面」的知情闸，不是行为断言。
    assert counts == {
        "graphs.graph": (26, 9),
        "graphs.validation_retry_loop": (11, 4),
    }, f"builder 注册面变化，请核对检测器覆盖面：{counts}"


# ────────────────────────── 回归锚：历史事故必须会被抓 ──────────────────────────
# 夹具用真实函数（非 exec）——检测器的三个核心件（注解解析/读取收集/集合比对）
# 直接吃真函数对象，与生产扫描同一代码路径。


class _FakeLearningRecordInput(BaseModel):
    """v0.66 前 LearningRecordInput 的残缺形态（缺 source）。"""

    token: str = ""
    product_id: str = ""


class _FakeOzonUploadInput(BaseModel):
    """v0.69 T2.2 前 OzonUploadInput 的残缺形态（缺 import_submitted）。"""

    ozon_payload: dict = {}


def _incident_learning_node(state: _FakeLearningRecordInput, config=None) -> dict:
    # v0.66 前的真实形态：source 未声明进 Input → getattr 恒 None → 写侧整段失效
    source = getattr(state, "source", None)
    return {"source": source}


def _incident_upload_node(state: _FakeOzonUploadInput, config=None) -> dict:
    # v0.69 T2.2 前的真实形态：import_submitted 未声明 → 守卫恒 False（死代码）
    import_submitted = state.import_submitted
    if import_submitted:
        return {"skipped": True}
    return {}


def _incident_bare_node(state, config=None) -> dict:
    # check_quota 事故形态：第一参数无注解
    return {"client_id": getattr(state, "ozon_client_id", "")}


class _FakeOzonStatusInput(BaseModel):
    status: str = ""


def _incident_status_node(state: _FakeOzonStatusInput, config=None) -> dict:
    return {}


def _incident_status_router(state):
    # v0.25 事故形态：moderation_status 不在 OzonStatusInput → 路由永远看不到 approved
    mod = getattr(state, "moderation_status", "")
    return "成功" if mod == "approved" else "失败"


class _FakeWellFormedInput(BaseModel):
    token: str = ""
    error_message: str = ""


def _wellformed_node(state: _FakeWellFormedInput, config=None) -> dict:
    err = getattr(state, "error_message", "") or ""
    ok = state.token
    return {"err": err, "ok": ok}


def test_anchor_learning_record_source_incident_would_be_caught():
    """回归锚①：learning_record `source` 事故（v0.66）在今天会被红灯。"""
    model, kind = _classify_node(_incident_learning_node)
    assert kind is None, "注解存在，不应报 UNANNOTATED"
    reads = _collect_state_reads(_incident_learning_node)
    assert "source" in reads
    assert reads - _allowed_fields(model) == {"source"}


def test_anchor_ozon_upload_import_submitted_incident_would_be_caught():
    """回归锚②：ozon_upload `import_submitted` 事故（v0.69 T2.2）在今天会被红灯。"""
    model, kind = _classify_node(_incident_upload_node)
    assert kind is None
    reads = _collect_state_reads(_incident_upload_node)
    assert "import_submitted" in reads
    assert reads - _allowed_fields(model) == {"import_submitted"}


def test_anchor_bare_state_signature_is_flagged():
    """回归锚③：裸 state 签名（check_quota 事故形态）必须报 UNANNOTATED_NODE。"""
    _, kind = _classify_node(_incident_bare_node)
    assert kind == "UNANNOTATED_NODE"


def test_anchor_router_checked_against_source_node_input():
    """回归锚④：路由按 source 节点 Input 校验（v0.25 路由版事故形态）。"""
    model, _ = _classify_node(_incident_status_node)
    reads = _collect_state_reads(_incident_status_router)
    assert reads - _allowed_fields(model) == {"moderation_status"}


def test_anchor_declared_reads_pass():
    """反例守卫：字段已声明时不许误报（检测器自身假阳性防线）。"""
    model, kind = _classify_node(_wellformed_node)
    assert kind is None
    assert _collect_state_reads(_wellformed_node) - _allowed_fields(model) == set()
