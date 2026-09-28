"""v0.83 批⑥ 回执真值化（实盘利润）测试 —— PLAN-v083-quality-campaign-v1 §3 批⑥。

覆盖：
- utils/profit_reality 纯公式（佣金/acquiring/FBS 物流费/fx 折算/unmodeled 单列/gap）
- learning_record `_backfill_category_commission` 扩展（同 /v5 响应补算实盘 + 多 SKU
  逐 product_id + GraphOutput channel 声明锁）
- listing_result_log 新列 + variants 对齐 + 写入口（PG）
- card_audit 第 5 不变量 E profit_reality（阈值双门 / fallback-info / 最低价门 /
  跟卖参与 / legacy 跳过 / summary 计数）
- init_data migrate_profit_reality_v083 幂等（PG）

纯函数用例不连 PG / 不发出站；须 PG 的用 `_pg_ready()` 跳过守卫。
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.profit_reality import (
    compute_profit_reality,
    compute_profit_reality_multi,
    resolve_commission_mode,
)

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


# ============================================================
# A. utils/profit_reality 纯公式
# ============================================================

def _price_item(*, pid=101, price=3000.0, marketing=2700.0,
                commissions=None, acquiring=None):
    item = {
        "product_id": pid,
        "price": {"price": price, "currency_code": "RUB"},
    }
    if marketing is not None:
        item["price"]["marketing_seller_price"] = marketing
    if commissions is not None:
        item["commissions"] = commissions
    if acquiring is not None:
        item["acquiring"] = acquiring
    return item


def test_formula_rub_full_fees():
    """RUB 店：佣金(基数=买家实付) + acquiring + FBS 物流费组，全部 /fx 折算 CNY。"""
    item = _price_item(
        commissions={
            "sales_percent_rfbs": 15.0,
            "fbs_first_mile_max_amount": 50.0,
            "fbs_direct_flow_trans_max_amount": 30.0,
            "fbs_deliv_to_customer_amount": 20.0,
        },
        acquiring=1.5,
    )
    r = compute_profit_reality(
        item, purchase_cost_cny=50.0, logistics_cost_cny=30.0,
        predicted_profit_cny=100.0, fx_rate=12.0, currency_code="RUB",
    )
    # 佣金基数 = marketing_seller_price = 2700
    assert r["real_price_rub"] == 2700.0
    assert r["seller_price"] == 3000.0
    assert r["real_commission_pct"] == pytest.approx(0.15)
    assert r["acquiring_pct"] == pytest.approx(0.015)
    assert r["fbs_fees_rub"] == pytest.approx(100.0)
    # fees = 2700*0.15 + 2700*0.015 + 100 = 405 + 40.5 + 100 = 545.5 RUB → /12 CNY
    assert r["real_fees_cny"] == pytest.approx(545.5 / 12.0, abs=1e-3)
    # revenue 2700/12=225; cost 80
    assert r["real_profit_cny"] == pytest.approx(225.0 - 545.5 / 12.0 - 80.0, abs=1e-3)
    assert r["predicted_profit_cny"] == 100.0
    assert r["gap_cny"] == pytest.approx(r["real_profit_cny"] - 100.0, abs=1e-3)
    assert r["gap_pct"] == pytest.approx(r["gap_cny"] / 100.0, abs=1e-3)
    assert r["fx_rate"] == 12.0
    assert r["commission_mode"] == "rfbs"


def test_unmodeled_fees_listed_not_dropped():
    """同通道未建模费项（min 档 / 退货费 / 未识别键）单列 unmodeled_fees。"""
    item = _price_item(commissions={
        "sales_percent_rfbs": 15.0,
        "fbs_first_mile_min_amount": 10.0,
        "fbs_return_flow_amount": 7.0,
        "weird_unknown_fee": 3.0,
    })
    r = compute_profit_reality(item, fx_rate=12.0, currency_code="RUB")
    keys = {u["key"] for u in r["unmodeled_fees"]}
    assert {"fbs_first_mile_min_amount", "fbs_return_flow_amount", "weird_unknown_fee"} <= keys
    assert r["fbs_fees_rub"] == 0.0  # 建模组为空


def test_commission_base_is_marketing_fallback_to_seller():
    """无 marketing_seller_price（无活动）→ 买家实付=卖家价，佣金基数随之。"""
    item = _price_item(marketing=None, commissions={"sales_percent_rfbs": 10.0})
    r = compute_profit_reality(item, fx_rate=12.0, currency_code="RUB")
    assert r["real_price_rub"] == 3000.0
    # fees = 3000*0.10 = 300 → /12
    assert r["real_fees_cny"] == pytest.approx(300.0 / 12.0, abs=1e-6)


def test_pct_fraction_forms_both_supported():
    """百分数（15.0）与分数（0.13）两种返回形态都归一为比例。"""
    int_form = compute_profit_reality(
        _price_item(commissions={"sales_percent_rfbs": 15.0}),
        fx_rate=12.0, currency_code="RUB")
    frac_form = compute_profit_reality(
        _price_item(commissions={"sales_percent_rfbs": 0.15}),
        fx_rate=12.0, currency_code="RUB")
    assert int_form["real_commission_pct"] == pytest.approx(0.15)
    assert frac_form["real_commission_pct"] == pytest.approx(0.15)


def test_zero_commission_not_trusted():
    """0（响应缺 commissions 块常见形态）不是真实费率 → None。"""
    r = compute_profit_reality(
        _price_item(commissions={"sales_percent_rfbs": 0.0}),
        fx_rate=12.0, currency_code="RUB")
    assert r["real_commission_pct"] is None


def test_commission_mode_fallback_chain():
    """按模式取；缺失回退链 rfbs→fbs→fbo→fbp。"""
    item = _price_item(commissions={"sales_percent_fbs": 12.0})
    r = compute_profit_reality(item, fx_rate=12.0, currency_code="RUB",
                               commission_mode="rfbs")
    assert r["real_commission_pct"] == pytest.approx(0.12)
    assert r["commission_mode"] == "fbs"  # 实际采用模式如实记


def test_cny_store_no_fx_division_price_rub_equiv():
    """非 RUB 店（CNY）：marketing 已是 CNY → 费用不折算；real_price_rub 走 RUB 当量。"""
    item = _price_item(price=100.0, marketing=90.0,
                       commissions={"sales_percent_rfbs": 10.0})
    r = compute_profit_reality(item, purchase_cost_cny=30.0, predicted_profit_cny=50.0,
                               fx_rate=12.0, currency_code="CNY")
    # fees = 90*0.10 = 9 CNY（不折算）
    assert r["real_profit_cny"] == pytest.approx(90.0 - 9.0 - 30.0, abs=1e-4)
    assert r["real_price_rub"] == pytest.approx(90.0 * 12.0)


def test_no_price_returns_none():
    assert compute_profit_reality({"price": {"price": 0}}, currency_code="RUB") is None
    assert compute_profit_reality(None) is None


def test_multi_per_product_alignment():
    items = [
        _price_item(pid=101, marketing=1200.0, commissions={"sales_percent_rfbs": 10.0}),
        _price_item(pid=202, marketing=2400.0, commissions={"sales_percent_rfbs": 20.0}),
    ]
    out = compute_profit_reality_multi(
        items, primary_product_id="101", fx_rate=12.0, currency_code="RUB")
    assert out["real_price_rub"] == 1200.0  # 主商品 = 101
    assert set(out["per_product"].keys()) == {"101", "202"}
    assert out["per_product"]["202"]["real_price_rub"] == 2400.0


def test_resolve_commission_mode():
    assert resolve_commission_mode({"sales_mode": "FBO"}) == "fbo"
    assert resolve_commission_mode({"fulfillment_mode": "fbs"}) == "fbs"
    assert resolve_commission_mode({}) == "rfbs"
    assert resolve_commission_mode(None) == "rfbs"
    assert resolve_commission_mode({"sales_mode": "bogus"}) == "rfbs"


# ============================================================
# B. learning_record 回填（同一 /v5 响应 → profit_reality）
# ============================================================

def _lr_state(**kw):
    base = dict(
        product_id="123456",
        description_category_id="17028929",
        ozon_client_id="4718259",
        ozon_api_key="mock-key",
        pricing_info={
            "currency_code": "RUB", "price": 3000, "exchange_rate": 12.0,
            "cost_cny": 50.0, "logistics_cost_cny": 30.0,
            "profit_estimation": {"profit_cny": 100.0},
        },
        envelope={"extensions": {}},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_backfill_extends_with_profit_reality(monkeypatch):
    from graphs.nodes.learning_record_node import _backfill_category_commission

    captured = {}

    def fake_ozon_post(client_id, api_key, endpoint, body, **kw):
        captured["body"] = body
        return {"items": [{
            "product_id": 123456,
            "price": {"price": 3000, "marketing_seller_price": 2700,
                      "currency_code": "RUB"},
            "commissions": {"sales_percent_rfbs": 15.0,
                            "fbs_first_mile_max_amount": 50.0,
                            "fbs_direct_flow_trans_max_amount": 30.0,
                            "fbs_deliv_to_customer_amount": 20.0},
            "acquiring": 1.5,
        }]}

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)
    monkeypatch.setattr("utils.commission_resolver.upsert_category_commission",
                        lambda *a, **k: None)

    out = _backfill_category_commission(_lr_state())
    assert out is not None
    assert out["real_price_rub"] == 2700.0
    assert out["real_commission_pct"] == pytest.approx(0.15)
    assert out["fbs_fees_rub"] == pytest.approx(100.0)
    assert out["predicted_profit_cny"] == 100.0
    assert out["fx_rate"] == 12.0
    # 单 SKU：body 保持既有形态（limit=1）
    assert captured["body"] == {"filter": {"product_id": ["123456"]}, "limit": 1}


def test_backfill_multi_sku_one_call_per_product(monkeypatch):
    """多 SKU：一次 /v5 带上 uploaded_products 的全部 product_id，逐 product_id 对齐。"""
    from graphs.nodes.learning_record_node import _backfill_category_commission

    captured = {}

    def fake_ozon_post(client_id, api_key, endpoint, body, **kw):
        captured["body"] = body
        return {"items": [
            {"product_id": 123456, "price": {"price": 1000, "currency_code": "RUB"},
             "commissions": {"sales_percent_rfbs": 10.0}},
            {"product_id": 999, "price": {"price": 2000, "currency_code": "RUB"},
             "commissions": {"sales_percent_rfbs": 20.0}},
        ]}

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)
    monkeypatch.setattr("utils.commission_resolver.upsert_category_commission",
                        lambda *a, **k: None)

    out = _backfill_category_commission(_lr_state(
        uploaded_products=[{"product_id": "999", "sku_id": "s2"}]))
    assert captured["body"]["filter"]["product_id"] == ["123456", "999"]
    assert captured["body"]["limit"] == 2  # 单次调用
    assert set(out["per_product"].keys()) == {"123456", "999"}


def test_backfill_skips_profit_without_estimation(monkeypatch):
    """无 profit_estimation（legacy/mock）→ 不算实盘、不触汇率，返回 None。"""
    from graphs.nodes.learning_record_node import _backfill_category_commission

    def fake_ozon_post(*a, **kw):
        return {"items": [{"product_id": 123456,
                           "price": {"price": 1000, "currency_code": "RUB"},
                           "commissions": {"sales_percent_rfbs": 15.0}}]}

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)
    monkeypatch.setattr("utils.commission_resolver.upsert_category_commission",
                        lambda *a, **k: None)

    out = _backfill_category_commission(_lr_state(
        pricing_info={"currency_code": "RUB", "price": 1000}))
    assert out is None


def test_channel_declarations_locked():
    """langgraph channel 纪律锁：四处声明缺一即静默吞。"""
    from graphs.state import (
        GlobalState, GraphOutput, LearningRecordInput, LearningRecordOutput,
    )
    assert "profit_reality" in GlobalState.model_fields
    assert "profit_reality" in GraphOutput.model_fields
    assert "profit_reality" in LearningRecordOutput.model_fields
    assert "uploaded_products" in LearningRecordInput.model_fields


# ============================================================
# C. listing_result_log 新列 + variants 对齐
# ============================================================

def test_listing_result_log_model_columns():
    from storage.database.shared.model import ListingResultLog
    for col in ("real_profit_cny", "gap_pct", "profit_reality"):
        assert col in ListingResultLog.__table__.columns, col


def test_merge_variants_profit_by_product_id_and_sku():
    from utils.listing_result_log import merge_variants_profit

    pr = {"per_product": {
        "111": {"real_profit_cny": 12.5, "gap_pct": 0.2},
        "222": {"real_profit_cny": -3.0, "gap_pct": -0.5},
    }}
    variants = [
        {"product_id": "111", "color": "红"},
        {"sku_id": "s2", "color": "蓝"},          # 经 uploaded_products 映射到 222
        {"sku_id": "s3", "color": "绿"},          # 无映射 → 原样
    ]
    out = merge_variants_profit(variants, pr, [{"sku_id": "s2", "product_id": "222"}])
    assert out[0]["real_profit_cny"] == 12.5 and out[0]["gap_pct"] == 0.2
    assert out[1]["real_profit_cny"] == -3.0
    assert "real_profit_cny" not in out[2]
    # 入参未被改写
    assert "real_profit_cny" not in variants[0]


def test_merge_variants_profit_empty_safe():
    from utils.listing_result_log import merge_variants_profit
    assert merge_variants_profit(None, None) == []
    assert merge_variants_profit([{"a": 1}], {}) == [{"a": 1}]


@pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")
def test_write_listing_result_log_persists_profit_reality():
    from storage.database.shared.model import ListingResultLog
    from utils.listing_result_log import write_listing_result_log

    eng = create_engine(DB_URL)
    task_id = str(uuid.uuid4())
    tenant = f"user_{uuid.uuid4().hex[:10]}"
    pr = {"real_profit_cny": 42.0, "gap_pct": 0.3, "real_price_rub": 1200.0}
    try:
        write_listing_result_log(
            payload={"ozon_client_id": "5x",
                     "envelope": {"draft": {"item_id": "i1"}, "extensions": {}}},
            graph_result={"upload_status": "success", "moderation_status": "approved",
                          "profit_reality": pr},
            task_db_id=task_id, tenant_id=tenant, retry_count=0,
        )
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT real_profit_cny, gap_pct, profit_reality FROM listing_result_log "
                "WHERE task_db_id=:t"), {"t": task_id}).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(42.0)
        assert row[1] == pytest.approx(0.3)
        assert row[2]["real_price_rub"] == 1200.0
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM listing_result_log WHERE task_db_id=:t"),
                         {"t": task_id})


# ============================================================
# D. card_audit 第 5 不变量 E
# ============================================================

def _e_state(*, facts=None, rate=12.0, mode="rfbs", follow_ids=None):
    return {
        "tenant": "t", "cid": "c", "follow_ids": follow_ids or set(),
        "profit_facts": facts or {}, "cny_rub_rate": rate, "commission_mode": mode,
        "summary": {
            "profit_checked": 0, "profit_gap": 0, "profit_reported_info": 0,
            "profit_below_price": 0, "profit_skipped_no_predicted": 0,
            "findings_open": 0, "card_errors": 0,
        },
    }


def _facts(*, predicted=10.0, cost=0.0, logistics=0.0, source=""):
    return {"101": {
        "predicted_profit_cny": predicted,
        "purchase_cost_cny": cost,
        "logistics_cost_cny": logistics,
        "currency_code": "RUB",
        "commission_source_at_submit": source,
    }}


def _patch_reality(monkeypatch, svc, *, real, gap_cny, gap_pct, price_rub=4000.0):
    monkeypatch.setattr(
        "utils.profit_reality.compute_profit_reality",
        lambda *a, **k: {
            "real_profit_cny": real, "gap_cny": gap_cny, "gap_pct": gap_pct,
            "real_price_rub": price_rub, "predicted_profit_cny": 10.0,
            "real_commission_pct": 0.15, "acquiring_pct": 0.015, "fbs_fees_rub": 0.0,
            "unmodeled_fees": [], "fx_rate": 12.0, "commission_mode": "rfbs",
        },
    )


def _run_e(monkeypatch, svc, state, *, real=333.0, gap_cny=323.0, gap_pct=32.3,
           price_rub=4000.0):
    recorded = []
    monkeypatch.setattr(svc, "_record_finding",
                        lambda *a, **k: recorded.append((a, k)))
    _patch_reality(monkeypatch, svc, real=real, gap_cny=gap_cny, gap_pct=gap_pct,
                   price_rub=price_rub)
    svc._check_profit_reality(state, "101",
                              {"price_item": {"product_id": 101}, "currency_code": "RUB"},
                              {})
    return recorded


def test_e_both_gates_exceeded_opens_finding(monkeypatch):
    from services import card_audit_service as svc
    st = _e_state(facts=_facts(predicted=10.0))
    recorded = _run_e(monkeypatch, svc, st)
    assert len(recorded) == 1
    assert recorded[0][0][3] == "profit_reality"  # invariant
    assert st["summary"]["profit_gap"] == 1
    assert st["summary"]["findings_open"] == 1


def test_e_pct_only_does_not_trigger(monkeypatch):
    from services import card_audit_service as svc
    monkeypatch.setenv("PROFIT_REALITY_GAP_ABS_CNY", "1000")  # abs 门抬高
    st = _e_state(facts=_facts(predicted=10.0))
    recorded = _run_e(monkeypatch, svc, st, gap_cny=50.0, gap_pct=5.0)
    assert recorded == []
    assert st["summary"]["profit_gap"] == 0


def test_e_abs_only_does_not_trigger(monkeypatch):
    from services import card_audit_service as svc
    monkeypatch.setenv("PROFIT_REALITY_GAP_PCT", "10.0")  # pct 门抬高
    st = _e_state(facts=_facts(predicted=100.0))
    recorded = _run_e(monkeypatch, svc, st, gap_cny=50.0, gap_pct=0.5)
    assert recorded == []


def test_e_below_min_price_skipped(monkeypatch):
    from services import card_audit_service as svc
    st = _e_state(facts=_facts(predicted=10.0))
    recorded = _run_e(monkeypatch, svc, st, price_rub=100.0, gap_cny=300.0, gap_pct=30.0)
    assert recorded == []
    assert st["summary"]["profit_below_price"] == 1


def test_e_fallback_commission_info_only(monkeypatch):
    """commission_source_at_submit=fallback:stale → 只计数，不自动开 open finding。"""
    from services import card_audit_service as svc
    st = _e_state(facts=_facts(predicted=10.0, source="fallback:stale"))
    recorded = _run_e(monkeypatch, svc, st)
    assert recorded == []
    assert st["summary"]["profit_reported_info"] == 1
    assert st["summary"]["findings_open"] == 0


def test_e_legacy_no_predicted_skipped(monkeypatch):
    from services import card_audit_service as svc
    st = _e_state(facts={})
    recorded = _run_e(monkeypatch, svc, st)
    assert recorded == []
    assert st["summary"]["profit_skipped_no_predicted"] == 1


def test_e_follow_card_participates(monkeypatch):
    """跟卖卡参与（我方定价，价格域不受 A6 内容红线约束）。"""
    from services import card_audit_service as svc
    st = _e_state(facts=_facts(predicted=10.0), follow_ids={"101"})
    recorded = _run_e(monkeypatch, svc, st)
    assert len(recorded) == 1


def test_e_summary_keys_initialized():
    from services import card_audit_service as svc
    import inspect
    src = inspect.getsource(svc.run_card_audit)
    for key in ("profit_checked", "profit_gap", "profit_reported_info",
                "profit_below_price", "profit_skipped_no_predicted"):
        assert f'"{key}"' in src, key


def test_price_map_retains_reality_fields(monkeypatch):
    """_fetch_price_map 保留 commissions/acquiring/marketing_seller_price（零新增调用）。"""
    from services import card_audit_service as svc

    def fake_ozon_post(client_id, api_key, path, body=None, **kw):
        assert path == "/v5/product/info/prices"
        return {"items": [{
            "product_id": 101,
            "price": {"price": "3000", "old_price": "3600",
                      "marketing_seller_price": "2700", "currency_code": "RUB"},
            "commissions": {"sales_percent_rfbs": 15.0,
                            "fbs_deliv_to_customer_amount": 20.0},
            "acquiring": 1.5,
        }]}

    monkeypatch.setattr("utils.ozon_client.ozon_post", fake_ozon_post)
    pm = svc._fetch_price_map("c", "k", ["101"])
    assert pm["101"]["price"] == "3000"
    assert pm["101"]["marketing_seller_price"] == "2700"
    assert pm["101"]["commissions"]["sales_percent_rfbs"] == 15.0
    assert pm["101"]["acquiring"] == 1.5
    assert pm["101"]["price_item"]["product_id"] == 101


def test_commission_source_at_submit_normalize():
    from services import card_audit_service as svc
    assert svc._commission_source_at_submit({"commission_source": "stale_fallback"}) == "fallback:stale"
    assert svc._commission_source_at_submit({"commission_source": "fallback"}) == "fallback"
    assert svc._commission_source_at_submit({}) == ""


@pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")
def test_profit_facts_join(monkeypatch):
    from services import card_audit_service as svc

    eng = create_engine(DB_URL)
    tenant = f"user_{uuid.uuid4().hex[:10]}"
    pid = str(10 ** 12 + uuid.uuid4().int % 10 ** 11)
    task_id = str(uuid.uuid4())
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO ozon_product_tasks (id, tenant_id, status, payload) "
                "VALUES (CAST(:i AS uuid), :t, 'completed', '{}'::jsonb)"
            ), {"i": task_id, "t": tenant})
            conn.execute(text(
                "INSERT INTO product_task_index (tenant_id, product_id, offer_id, task_id) "
                "VALUES (:t, :p, 'o1', CAST(:i AS uuid))"
            ), {"t": tenant, "p": pid, "i": task_id})
            conn.execute(text(
                "INSERT INTO listing_result_log (task_db_id, tenant_id, purchase_cost, pricing_info) "
                "VALUES (:i, :t, 12.5, CAST(:pi AS jsonb))"
            ), {"i": task_id, "t": tenant,
                "pi": '{"currency_code":"RUB","cost_cny":50,"logistics_cost_cny":30,'
                      '"profit_estimation":{"profit_cny":88.0},"commission_source":"fallback"}'})
        facts = svc._profit_facts(tenant, [pid])
        assert facts[pid]["predicted_profit_cny"] == 88.0
        assert facts[pid]["purchase_cost_cny"] == 50.0
        assert facts[pid]["logistics_cost_cny"] == 30.0
        assert facts[pid]["commission_source_at_submit"] == "fallback"
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM listing_result_log WHERE task_db_id=:i"), {"i": task_id})
            conn.execute(text("DELETE FROM product_task_index WHERE tenant_id=:t"), {"t": tenant})
            conn.execute(text("DELETE FROM ozon_product_tasks WHERE tenant_id=:t"), {"t": tenant})


# ============================================================
# E. init_data 迁移幂等
# ============================================================

@pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")
def test_migrate_profit_reality_idempotent():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from init_data import migrate_profit_reality_v083

    eng = create_engine(DB_URL)
    migrate_profit_reality_v083(eng)
    migrate_profit_reality_v083(eng)  # 二次 no-op
    with eng.connect() as conn:
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='listing_result_log'")).fetchall()}
    assert {"real_profit_cny", "gap_pct", "profit_reality"} <= cols
