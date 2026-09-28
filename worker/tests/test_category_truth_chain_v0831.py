"""v0.83.1 类目真值链三修回归（2026-09-28 17 单节日批实锤驱动）。

修复契约：
  B1  estimate/batch 新增 ``scid``（1688 source_category_id）——dc 缺席时经
      ``category_mapping_learn.lookup_mapping`` 反查 dc（命中才用），解锁
      discover 关键词候选佣金冷启动（此前 dc=N/A → 佣金恒 fallback →
      v0.83 佣金闸恒拦 → 关键词选品 0 达标）。
  B2  prepare CREATE 模板空键整键省略（complex_attributes/images360/
      pdf_list/barcode）——#84「绝不发空数组」口径落到 CREATE（此前只改了
      UPDATE 回显路径）；实锤：袜子类目带 pdf_list:[] 复审拒
      「Ссылка на pdf не может быть пустая」（卡 6474134917）。
  B3  graph 新增 ``category_conf_gate`` 节点——assemble 后 conf<MIN_CONF_BOX
      写 ``_blocked_exit`` 同构失败字段（error_code=LOCAL_CATEGORY_MATCH_FAILED
      + failed_stage=category_match + 尽力入箱），路由按既有 failed_stage 分支
      判定。此前低置信只在路由层改路由不写 state → 终态被 T0.4 文案劫持成
      PRODUCT_NOT_CREATED（17 单批三例实锤，诊断被带偏）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_category_truth_chain_v0831.py -q
纯 mock（LLM/映射表/入箱全 monkeypatch），无需 PG/网络。
"""
import os
import sys
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from test_arch_findings_v080_validate_quota import (  # noqa: E402
    _make_prepare_state,
    _patches,
    _prepare_draft,
)


# ═══════════════ B1: estimate scid 反查 ═══════════════

def _run_batch_with_capture(items, monkeypatch, lookup_impl):
    """跑 _run_estimate_batch：捕获传给 estimate_from_envelope 的 envelope，
    返回 (captured_envelopes, result)。"""
    from routes import estimate_routes
    from api.schemas import EstimateBatchIn, EstimateBatchItem

    captured: list = []

    def _fake_estimate(envelope, **kwargs):
        captured.append(envelope)
        return {"price": 100, "old_price": 120, "promo_price": 90,
                "profit_cny": 10.0, "profit_rate": 0.1,
                "commission_rate": 0.1, "commission_source": "cache",
                "logistics_source": "table", "weight_suspect": "",
                "exchange_rate_source": ""}

    monkeypatch.setattr(estimate_routes.estimate_service,
                        "estimate_from_envelope", _fake_estimate)
    monkeypatch.setattr("utils.category_mapping_learn.lookup_mapping", lookup_impl)

    body = EstimateBatchIn(items=[EstimateBatchItem(**i) for i in items])
    result = estimate_routes._run_estimate_batch(body, "tenant-t")
    return captured, result


def test_b1_scid_resolves_dc_via_mapping(monkeypatch):
    """scid 在场、dc 缺席、映射命中 → envelope 带 ozon_category.dc。"""
    def _hit(source_category_id=None, leaf_name=""):
        assert int(source_category_id) == 201303723
        return {"dc": "17028733", "tp": "91672", "confidence": 0.9}

    captured, result = _run_batch_with_capture(
        [{"purchase_cost": 9.5, "scid": "201303723"}], monkeypatch, _hit)
    assert result["failed"] == []
    assert captured, "estimate_from_envelope 未被调用"
    oz = captured[0]["draft"].get("ozon_category") or {}
    assert oz.get("description_category_id") == "17028733"


def test_b1_scid_mapping_miss_keeps_dc_absent(monkeypatch):
    """映射未命中（None）→ 不写 ozon_category（降级旧口径，不编造 dc）。"""
    captured, _ = _run_batch_with_capture(
        [{"purchase_cost": 9.5, "scid": "999999999"}], monkeypatch,
        lambda **k: None)
    assert "ozon_category" not in captured[0]["draft"]


def test_b1_dc_direct_wins_no_lookup(monkeypatch):
    """dc 直给时 scid 不触发反查（explicit 优先，反查零调用）。"""
    calls = []

    def _spy(**k):
        calls.append(k)
        return {"dc": "1", "tp": "1", "confidence": 1.0}

    captured, _ = _run_batch_with_capture(
        [{"purchase_cost": 9.5, "dc": "17027938", "scid": "201303723"}],
        monkeypatch, _spy)
    assert calls == [], "dc 在场不应触发 lookup_mapping"
    oz = captured[0]["draft"].get("ozon_category") or {}
    assert oz.get("description_category_id") == "17027938"


def test_b1_invalid_scid_degrades_safely(monkeypatch):
    """scid 非数字 → 不炸、不写 ozon_category（单条失败不整体 4xx）。"""
    captured, result = _run_batch_with_capture(
        [{"purchase_cost": 9.5, "scid": "not-a-number"}], monkeypatch,
        lambda **k: {"dc": "1", "tp": "1", "confidence": 1.0})
    assert result["failed"] == []
    assert "ozon_category" not in captured[0]["draft"]


def test_b1_lookup_exception_does_not_break_estimate(monkeypatch):
    """反查抛异常 → warning 降级，预估照常（无 dc 旧口径）。"""
    def _boom(**k):
        raise RuntimeError("db down")

    captured, result = _run_batch_with_capture(
        [{"purchase_cost": 9.5, "scid": "201303723"}], monkeypatch, _boom)
    assert result["failed"] == []
    assert "ozon_category" not in captured[0]["draft"]


# ═══════════════ B2: CREATE 模板空键省略 ═══════════════

def test_b2_create_payload_omits_empty_doc_keys(monkeypatch):
    """CREATE items[0] 不含 complex_attributes/images360/pdf_list/barcode
    （#84 口径落到 CREATE 模板——空键对 schema 严格类目是复审地雷）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node

    monkeypatch.setenv("LLM_SCHEMA_FILL", "0")
    with _patches():
        out = prepare_ozon_upload_node(_make_prepare_state(_prepare_draft()), None, None)
    assert not out.error_message, f"prepare 意外失败: {out.error_message}"
    items = (out.ozon_payload or {}).get("items") or []
    assert items, "payload 无 items"
    it = items[0]
    for bad in ("pdf_list", "images360", "complex_attributes", "barcode"):
        assert bad not in it, f"CREATE items 不得携带空键 {bad}（袜子类目实锤拒单）"
    # promotions 是 Ozon 要求字段，保留
    assert "promotions" in it


# ═══════════════ B3: category_conf_gate 归因 ═══════════════

def _gate_state(conf, user_id=""):
    return SimpleNamespace(match_confidence=conf, user_id=user_id,
                           envelope={"draft": {"item_id": "x1", "title": "т"}})


def test_b3_gate_blocks_low_conf_with_category_code(monkeypatch):
    """conf 0.18 → error_code=LOCAL_CATEGORY_MATCH_FAILED + failed_stage。"""
    from graphs.graph import category_conf_gate_node

    monkeypatch.setattr(
        "graphs.nodes.assemble_ozon_product_node._blocked_exit",
        lambda state, draft, cands, msg, match_confidence=None: {
            "error_message": msg, "error_code": "LOCAL_CATEGORY_MATCH_FAILED",
            "failed_stage": "category_match", "match_confidence": match_confidence})
    out = category_conf_gate_node(_gate_state(0.1818))
    assert out.get("error_code") == "LOCAL_CATEGORY_MATCH_FAILED"
    assert out.get("failed_stage") == "category_match"
    assert "0.1818" in out.get("error_message", "")


def test_b3_gate_passes_healthy_conf():
    """conf ≥ MIN_CONF_BOX → 空更新直通（零副作用）。"""
    from graphs.graph import category_conf_gate_node

    assert category_conf_gate_node(_gate_state(0.5)) == {}
    assert category_conf_gate_node(_gate_state(None)) == {}


def test_b3_gate_exception_fallback_still_attributed(monkeypatch):
    """_blocked_exit 抛异常 → 手写最小失败字段，归因不丢。"""
    from graphs.graph import category_conf_gate_node

    def _boom(*a, **k):
        raise RuntimeError("box service down")

    monkeypatch.setattr(
        "graphs.nodes.assemble_ozon_product_node._blocked_exit", _boom)
    out = category_conf_gate_node(_gate_state(0.18))
    assert out.get("error_code") == "LOCAL_CATEGORY_MATCH_FAILED"
    assert out.get("failed_stage") == "category_match"


def test_b3_router_routes_gate_failure_to_end():
    """闸写 failed_stage 后路由走既有分支 → 失败（END），不再漏到成功。"""
    from graphs.graph import route_after_assemble

    st = SimpleNamespace(failed_stage="category_match", error_message="x",
                         match_confidence=0.18)
    assert route_after_assemble(st) == "失败"
