"""v0.83.2 类目文档硬要求闸测试（纯 mock，无需 PG）。

覆盖四个面：
1. utils/category_doc_gate —— curated 热加载/通配/非法行、学习表读（命中/fail-open）、
   学习 upsert（参数形状/evidence cap/非致命）；
2. assemble —— _doc_gate_exempt 豁免阶梯、_doc_required_exit 出口形状
   （error_code=LOCAL_CATEGORY_REQUIRES_DOCUMENT / failed_stage=category_match / notice）；
3. ozon_status —— _learn_doc_requirement 触发条件（PDF_SRC_URL_IS_EMPTY）与
   dc/tp 缺失跳过；
4. draft_service —— 空 token 边界 401（submit_draft + schedule_listing 四路
   消费方单一咽点，主链 /submit_task 既有闸的同构补口）。

背景（2026-10-02 生产实锤，task da284d0e）：袜子类目 CREATE 在 v0.83.1 整键
省略 pdf_list 后仍被 Ozon validation 拒 PDF_SRC_URL_IS_EMPTY——证明类目级
文档硬要求，预检闸 + decline 学习是根治路径（试错 = 白烧 import+生图配额）。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import utils.category_doc_gate as catdoc
from utils.category_doc_gate import (
    doc_gate_notice,
    record_doc_requirement,
    requires_document,
)


# ── 测试基建：假 engine / conn（exec_driver_sql 参数化形态，零真 PG）──

class _FakeConn:
    def __init__(self, row=None, exc=None):
        self._row = row
        self._exc = exc
        self.calls: list[tuple[str, tuple]] = []

    def exec_driver_sql(self, sql, params=()):
        self.calls.append((str(sql), tuple(params)))
        if self._exc:
            raise self._exc
        return self

    def fetchone(self):
        return self._row


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return _Ctx(self._conn)

    def begin(self):
        return _Ctx(self._conn)


class _Ctx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        return False


def _write_config(tmp_path, rows, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "requires_doc_categories.json").write_text(
        json.dumps({"categories": rows}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    return cfg


# ═══ 1. curated 层 ═══

def test_curated_exact_and_wildcard(tmp_path, monkeypatch):
    _write_config(tmp_path, [
        {"description_category_id": 90, "type_id": 91, "note": "袜子"},
        {"description_category_id": 80, "type_id": 0, "note": "整类目"},
    ], monkeypatch)
    hit = requires_document(90, 91)
    assert hit and hit["source"] == "curated" and hit["times_seen"] == 0
    wild = requires_document(80, 999)
    assert wild and wild["source"] == "curated"  # (dc,0) 通配
    assert requires_document(70, 71) is None


def test_curated_invalid_rows_ignored(tmp_path, monkeypatch):
    _write_config(tmp_path, [
        {"description_category_id": "x", "type_id": 1},   # 非数字
        {"description_category_id": 0, "type_id": 1},     # dc<=0
        "not-a-dict",                                      # 非法行
        {"description_category_id": 90, "type_id": 91},   # 合法（note 缺省）
    ], monkeypatch)
    hit = requires_document(90, 91)
    assert hit and hit["source"] == "curated"
    assert "合规文档" in hit["note"]  # note 缺省文案


def test_curated_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))  # 无 config 文件
    assert requires_document(90, 91) is None


# ═══ 2. 学习表读侧（fail-open）═══

def test_learned_row_hit(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))  # curated 空
    conn = _FakeConn(row=("decline_learned", {"codes": ["PDF_SRC_URL_IS_EMPTY"]}, 3))
    monkeypatch.setattr(catdoc, "get_engine", lambda: _FakeEngine(conn))
    hit = requires_document(90, 91)
    assert hit and hit["source"] == "decline_learned" and hit["times_seen"] == 3
    assert "times_seen=3" in hit["note"]
    # 参数化形状：静态 SQL + 参数元组
    sql, params = conn.calls[0]
    assert "%s" in sql and "category_doc_requirements" in sql
    assert params == (90, 91)


def test_learned_db_error_fail_open(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    conn = _FakeConn(exc=RuntimeError("table missing"))
    monkeypatch.setattr(catdoc, "get_engine", lambda: _FakeEngine(conn))
    assert requires_document(90, 91) is None  # fail-open 放行


def test_invalid_dc_tp_short_circuit(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_WORKSPACE_PATH", str(tmp_path))
    conn = _FakeConn()
    monkeypatch.setattr(catdoc, "get_engine", lambda: _FakeEngine(conn))
    assert requires_document(0, 91) is None
    assert requires_document(90, 0) is None
    assert requires_document("abc", 1) is None
    assert not conn.calls  # 非法入参不打 DB


# ═══ 3. 学习表写侧（upsert + cap + 非致命）═══

def test_record_upsert_params_and_nonfatal(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(catdoc, "get_engine", lambda: _FakeEngine(conn))
    assert record_doc_requirement(90, 91, {"codes": ["PDF_SRC_URL_IS_EMPTY"]}) is True
    sql, params = conn.calls[0]
    assert "ON CONFLICT" in sql and "times_seen + 1" in sql
    assert params[:3] == (90, 91, "decline_learned")
    assert json.loads(params[3]) == {"codes": ["PDF_SRC_URL_IS_EMPTY"]}
    # 非致命：DB 异常 → False，不抛
    monkeypatch.setattr(catdoc, "get_engine",
                        lambda: _FakeEngine(_FakeConn(exc=RuntimeError("down"))))
    assert record_doc_requirement(90, 91) is False


def test_record_evidence_cap(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(catdoc, "get_engine", lambda: _FakeEngine(conn))
    big = {f"k{i}": "x" * 500 for i in range(20)}
    big["codes"] = "PDF_SRC_URL_IS_EMPTY"
    assert record_doc_requirement(90, 91, big) is True
    ev = json.loads(conn.calls[0][1][3])
    assert len(ev) <= 12 and all(len(v) <= 300 for v in ev.values() if isinstance(v, str))


# ═══ 4. assemble 豁免 + 出口 ═══

def _assemble_mod():
    from graphs.nodes import assemble_ozon_product_node as m
    return m


def test_doc_gate_exempt_ladder():
    m = _assemble_mod()
    st = SimpleNamespace()
    # 可信来源豁免（manual/page/what_to_sell/widget）；mapping 刻意不豁免
    for src in ("manual", "page", "what_to_sell", "widget"):
        assert m._doc_gate_exempt(st, {"ozon_category": {"source": src}}) is True
    assert m._doc_gate_exempt(st, {"ozon_category": {"source": "mapping"}}) is False
    assert m._doc_gate_exempt(st, {"ozon_category": {}}) is False
    # 采集箱复核豁免
    assert m._doc_gate_exempt(st, {}, {"box_reviewed": True}) is True
    # 编辑更新豁免
    assert m._doc_gate_exempt(st, {}, {"update_product_id": "12345"}) is True
    assert m._doc_gate_exempt(st, {}, {"update_product_id": ""}) is False
    # 无任何豁免
    assert m._doc_gate_exempt(st, {}, {}) is False


def test_doc_required_exit_shape(monkeypatch):
    m = _assemble_mod()
    st = SimpleNamespace(assembly_retry_count=0)
    monkeypatch.setattr(m, "_maybe_create_blocked_draft",
                        lambda *a, **k: {"draft_id": "d-1", "recommended": []})
    out = m._doc_required_exit(st, {"title": "袜子"}, [], 90, 91,
                               {"source": "decline_learned", "times_seen": 2})
    assert out["error_code"] == "LOCAL_CATEGORY_REQUIRES_DOCUMENT"
    assert out["failed_stage"] == "category_match"
    assert out["assembly_retry_count"] == 1
    assert "合规文档" in out["notice"] and "d-1" in out["notice"]
    assert "90/91" in out["error_message"]
    assert "match_confidence" not in out  # 类目匹配本身是对的，不压置信度


def test_curated_doc_requirement_layer(tmp_path, monkeypatch):
    """curated 单层判定（零 DB）：命中/通配/未登记/非法入参 fail-open。"""
    _write_config(tmp_path, [
        {"description_category_id": 90, "type_id": 91},
        {"description_category_id": 80, "type_id": 0},
    ], monkeypatch)
    assert catdoc.curated_doc_requirement(90, 91)["source"] == "curated"
    assert catdoc.curated_doc_requirement(80, 555)["source"] == "curated"  # (dc,0) 通配
    assert catdoc.curated_doc_requirement(70, 71) is None
    assert catdoc.curated_doc_requirement(0, 1) is None
    assert catdoc.curated_doc_requirement("x", 1) is None


def test_curated_overrides_exemption_ladder(tmp_path, monkeypatch):
    """验收修复核心口径：curated 人工登记对 what_to_sell 等可信来源照样拦。

    discover 主流源是 what_to_sell/page——豁免阶梯若先于 curated，则需文档
    类目每单白烧，且「运营人工登记 config 兜底」的升级指引对该源静默无效。
    """
    _write_config(tmp_path, [
        {"description_category_id": 90, "type_id": 0, "note": "袜子级通配"},
    ], monkeypatch)
    m = _assemble_mod()
    st = SimpleNamespace()
    # 豁免阶梯本身不回退（口径不变）：阶梯只管辖学习表层
    assert m._doc_gate_exempt(st, {"ozon_category": {"source": "what_to_sell"}}) is True
    # 但 assemble 主流程 curated 判定先于豁免阶梯（源码锚断言）
    node_src = open(m.__file__, encoding="utf-8").read()
    assert node_src.index("curated_doc_requirement(_dc_i, _tp_i)") < \
        node_src.index("not _doc_gate_exempt(state, draft, extensions)")


def test_learned_table_stays_behind_exemption(monkeypatch, tmp_path):
    """学习表不越豁免阶梯：学习行对可信来源不生效（自动链路保护口径不变）。"""
    _write_config(tmp_path, [], monkeypatch)  # curated 空
    conn = _FakeConn(row=("decline_learned", {}, 2))
    monkeypatch.setattr(catdoc, "get_engine", lambda: _FakeEngine(conn))
    assert requires_document(90, 91) is not None  # 学习表对自动链（非豁免）生效
    m = _assemble_mod()
    assert m._doc_gate_exempt(SimpleNamespace(),
                              {"ozon_category": {"source": "page"}}) is True


def test_doc_gate_in_main_flow_after_category(monkeypatch, tmp_path):
    """闸在主路径生效：curated 先于豁免阶梯、二者都在 Step7 前（源码锚断言）。"""
    m = _assemble_mod()
    node_src = open(m.__file__, encoding="utf-8").read()
    curated_text = "curated_doc_requirement(_dc_i, _tp_i)"
    exc_text = "not _doc_gate_exempt(state, draft, extensions)"
    learned_text = "from utils.category_doc_gate import requires_document"
    assert curated_text in node_src and exc_text in node_src and learned_text in node_src
    step7_idx = node_src.index("# Step 7: 返回结果 dict")
    cur_idx = node_src.index(curated_text)
    exc_idx = node_src.index(exc_text)
    assert cur_idx < exc_idx < step7_idx  # curated → 豁免 → Step 7


# ═══ 5. ozon_status 学习钩子 ═══

def test_learn_doc_requirement_triggers(monkeypatch):
    from graphs.nodes import ozon_status_node as osn
    called = {}
    monkeypatch.setattr(catdoc, "record_doc_requirement",
                        lambda dc, tp, evidence=None, source="decline_learned":
                        called.update(dc=dc, tp=tp, ev=evidence))
    st = SimpleNamespace(description_category_id="90", type_id="91", product_id="777")
    osn._learn_doc_requirement(st, [{"code": "PDF_SRC_URL_IS_EMPTY"}])
    assert called.get("dc") == 90 and called.get("tp") == 91
    assert "PDF_SRC_URL_IS_EMPTY" in called["ev"]["codes"]

    # 非文档码不触发
    called.clear()
    osn._learn_doc_requirement(st, [{"code": "SOME_OTHER"}])
    assert not called

    # dc/tp 缺失 → 跳过
    osn._learn_doc_requirement(SimpleNamespace(description_category_id="", type_id="91"),
                               [{"code": "PDF_SRC_URL_IS_EMPTY"}])
    assert not called


def test_learn_doc_requirement_nonfatal(monkeypatch):
    from graphs.nodes import ozon_status_node as osn

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(catdoc, "record_doc_requirement", _boom)
    st = SimpleNamespace(description_category_id="90", type_id="91", product_id="1")
    osn._learn_doc_requirement(st, [{"code": "PDF_SRC_URL_IS_EMPTY"}])  # 不抛


def test_ozon_status_input_declares_dc_tp():
    """channel 过滤纪律：Input 不声明 dc/tp → 学习钩子恒拿不到（v0.27 教训）。"""
    from graphs.state import OzonStatusInput
    fields = OzonStatusInput.model_fields
    assert "description_category_id" in fields and "type_id" in fields


# ═══ 6. draft_service 空 token 边界 401 ═══

def test_submit_draft_empty_token_401(monkeypatch):
    from services import draft_service

    def _no_db(*a, **k):  # 闸必须先于任何 DB 触达
        raise AssertionError("guard must fire before DB access")

    monkeypatch.setattr(draft_service, "get_draft", _no_db)
    for bad in ("", "   ", None):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(draft_service.submit_draft("u1", "d1", bad))
        assert ei.value.status_code == 401 and ei.value.detail == "Token is required"


def test_schedule_listing_empty_token_401(monkeypatch):
    from services import draft_service

    def _no_cipher(*a, **k):
        raise AssertionError("guard must fire before cipher/DB")

    monkeypatch.setattr("utils.credential_cipher.encrypt", _no_cipher)
    with pytest.raises(HTTPException) as ei:
        draft_service.schedule_listing("u1", "d1", "cred", "", "2099-01-01T00:00:00+00:00")
    assert ei.value.status_code == 401


def test_notice_text():
    assert "合规文档" in doc_gate_notice()
