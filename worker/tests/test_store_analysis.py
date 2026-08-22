"""店铺分析端点单测 — 有成本商品精确利润 / 无成本不编造 / 趋势读历史。

锁定（harness-store-analysis 计划 todo 6）：
1. 返回结构完整：summary + profit_trend + low_margin_products + out_of_stock_products
   + promo_ready_products
2. low_margin 只含有成本商品，无成本商品不带 profit_rate 字段（不编造）
3. profit_trend 从 store_metrics_history 读（snapshot_at/sales_amount/profit_rate 透传）
4. stock <= 0 商品进 out_of_stock_products
5. 空店铺返回空结构

成本数据源：product_task_index → ozon_product_tasks.payload.envelope（本系统上架商品才有）。
有成本的商品经 estimate_service.estimate_from_envelope（compute_price + commission_resolver
provisional band pass + 物流费率唯一入口）算 profit_rate；无成本商品不填 profit_rate。

PG 不可达时 skip（对齐 test_store_metrics_sync）。PG 可用时断言真值（非「无报错」）。
运行：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
        PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_store_analysis.py -q
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import store_analysis_service as svc
from storage.database.shared.model import Base

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

TENANT = "tenant-analysis"
CRED = uuid.uuid4()


@pytest.fixture(scope="module")
def engine():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过店铺分析测试")
    Base.metadata.create_all(bind=eng)
    yield eng


def _cleanup(engine):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ozon_products_cache"))
        conn.execute(text("DELETE FROM product_task_index"))
        conn.execute(text("DELETE FROM ozon_product_tasks"))
        conn.execute(text("DELETE FROM store_metrics_history"))
        conn.execute(text("DELETE FROM credentials"))


def _insert_credential(engine):
    """product_task_index.credential_id 有 FK → credentials，先插入店铺凭证行（id=固定 CRED，幂等）。"""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO credentials (id, tenant_id, ozon_client_id, ozon_api_key_enc, "
            "api_key_masked, status) VALUES (:id, :t, :cid, :enc, :masked, 'active') "
            "ON CONFLICT (id) DO NOTHING"
        ), {"id": CRED, "t": TENANT, "cid": "4718259", "enc": bytes.fromhex("00") * 16, "masked": "****abcd"})


def _insert_product(
    engine, product_id, name="", price=None, old_price=None, stock=None, currency="RUB"
):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ozon_products_cache (tenant_id, credential_id, product_id, offer_id, "
            "name, image, price, old_price, stock, currency, archived, synced_at) VALUES "
            "(:t, :c, :pid, :oid, :name, NULL, :price, :old_price, :stock, :cur, FALSE, NOW())"
        ), {
            "t": TENANT, "c": str(CRED), "pid": product_id,
            "oid": f"offer-{product_id}", "name": name,
            "price": price, "old_price": old_price, "stock": stock, "cur": currency,
        })


def _insert_task_with_envelope(engine, task_id, product_id, purchase_cost, margin_rate=0.05):
    """插入任务（payload 含 envelope 有 purchase_cost）→ 供 _load_cost_payloads 恢复成本。"""
    envelope = {
        "draft": {
            "item_id": product_id,
            "purchase_cost": purchase_cost,
            "weight": 200,
            "dimensions": {"length": 50, "width": 30, "height": 20},
        },
        "source": {"purchase_cost": purchase_cost},
        "extensions": {"margin_rate": margin_rate, "commission_rate": 0.10},
    }
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (id, tenant_id, status, payload) VALUES "
            "(:id, :t, 'completed', CAST(:payload AS jsonb))"
        ), {"id": task_id, "t": TENANT, "payload": json.dumps({"envelope": envelope})})
        conn.execute(text(
            "INSERT INTO product_task_index (product_id, tenant_id, offer_id, task_id, credential_id) "
            "VALUES (:pid, :t, :oid, :tid, :c)"
        ), {"pid": product_id, "t": TENANT, "oid": f"offer-{product_id}",
            "tid": task_id, "c": str(CRED)})


def _insert_task_item_index_mismatch(engine, task_id, product_id, item_id, purchase_cost, margin_rate=2.0):
    """真值回归：draft.item_id（1688 ID）与 product_task_index.product_id（Ozon ID）**刻意不一致**。

    生产数据中两者永不相等：1688 item_id（如 "993789876"）对应一个 Ozon product_id
    （如 "5476361418"）。此用例锁定「按 Ozon product_id 匹配成本」——
    item_id 只作草稿标识，绝不承担成本键职能（曾致全部商品落 has_cost=False）。
    """
    envelope = {
        "draft": {
            "item_id": item_id,
            "purchase_cost": purchase_cost,
            "weight": 200,
            "dimensions": {"length": 50, "width": 30, "height": 20},
        },
        "source": {"purchase_cost": purchase_cost},
        "extensions": {"margin_rate": margin_rate, "commission_rate": 0.10},
    }
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (id, tenant_id, status, payload) VALUES "
            "(:id, :t, 'completed', CAST(:payload AS jsonb))"
        ), {"id": task_id, "t": TENANT, "payload": json.dumps({"envelope": envelope})})
        conn.execute(text(
            "INSERT INTO product_task_index (product_id, tenant_id, offer_id, task_id, credential_id) "
            "VALUES (:pid, :t, :oid, :tid, :c)"
        ), {"pid": product_id, "t": TENANT, "oid": f"offer-{product_id}",
            "tid": task_id, "c": str(CRED)})


def _insert_history(engine, snapshot_at, sales_amount, profit_rate):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO store_metrics_history (tenant_id, credential_id, store_id, snapshot_at, "
            "sales_amount, profit_rate) VALUES (:t, :c, :sid, :snap, :sa, :pr)"
        ), {"t": TENANT, "c": str(CRED), "sid": str(CRED),
            "snap": snapshot_at, "sa": sales_amount, "pr": profit_rate})


def _run(engine):
    """装载数据 + 调 analyze_store（mock credential 归属校验）。"""
    _insert_credential(engine)
    with patch.object(svc.credential_service, "get_decrypted", return_value=("4718259", "sk-abc")):
        return svc.analyze_store(TENANT, str(CRED))


# ──────────────────────────────────────────────
# 结构 + 真值断言
# ──────────────────────────────────────────────


def test_analysis_returns_structure(engine):
    """有成本 + 无成本混合 → 返回 summary/profit_trend/low_margin/out_of_stock/promo_ready。"""
    _cleanup(engine)
    _insert_credential(engine)
    # 有成本：margin -0.5 → profit_rate 远低于 0.15 → low_margin
    _insert_task_with_envelope(engine, uuid.uuid4(), "1001", purchase_cost=20.0, margin_rate=-0.5)
    _insert_product(engine, "1001", name="低利润商品A", price=26, stock=50)
    # 有成本：margin 2.0 → 利润充足 → promo_ready
    _insert_task_with_envelope(engine, uuid.uuid4(), "1002", purchase_cost=5.0, margin_rate=2.0)
    _insert_product(engine, "1002", name="高利润商品B", price=37, stock=50)
    # 无成本：只展示当前价+库存，不进 low_margin/promo_ready
    _insert_product(engine, "1003", name="无成本商品C", price=40, old_price=50, stock=15)
    _insert_product(engine, "1004", name="缺货商品D", price=30, stock=0)
    _insert_history(engine, datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc), 500.0, 0.12)
    _insert_history(engine, datetime.datetime(2026, 8, 21, tzinfo=datetime.timezone.utc), 600.0, 0.15)

    result = _run(engine)

    assert "summary" in result
    assert "profit_trend" in result
    assert "low_margin_products" in result
    assert "out_of_stock_products" in result
    assert "promo_ready_products" in result

    s = result["summary"]
    assert s["product_count"] == 4
    assert s["low_stock_count"] == 0
    assert s["active_discount_count"] == 1  # 仅 1003（old_price>price）
    assert s["avg_profit_rate"] is not None  # 有成本商品可算平均

    # profit_trend 从历史读（2 条透传）
    assert len(result["profit_trend"]) == 2
    assert result["profit_trend"][0]["snapshot_at"] == "2026-08-20T00:00:00+00:00"
    assert result["profit_trend"][0]["sales_amount"] == 500.0
    assert result["profit_trend"][0]["profit_rate"] == 0.12
    assert result["profit_trend"][1]["profit_rate"] == 0.15

    # out_of_stock：stock<=0 商品
    oos = {p["product_id"] for p in result["out_of_stock_products"]}
    assert "1004" in oos

    # low_margin：有成本商品 A（margin 0.05 → 低利润）
    lm = {p["product_id"]: p for p in result["low_margin_products"]}
    assert "1001" in lm
    assert lm["1001"]["profit_rate"] is not None

    # promo_ready：有成本高利润 B
    pr = {p["product_id"] for p in result["promo_ready_products"]}
    assert "1002" in pr


def test_low_margin_only_cost_products(engine):
    """无成本商品不出现在 low_margin_products（或不带 profit_rate）。"""
    _cleanup(engine)
    _insert_credential(engine)
    # 有成本低利润
    _insert_task_with_envelope(engine, uuid.uuid4(), "2001", purchase_cost=30.0, margin_rate=-0.5)
    _insert_product(engine, "2001", name="有成本低利", price=32, stock=50)
    # 无成本同价但无成本 → 不得进 low_margin
    _insert_product(engine, "2002", name="无成本", price=32, stock=50)

    result = _run(engine)
    lm = {p["product_id"] for p in result["low_margin_products"]}
    assert "2001" in lm
    assert "2002" not in lm, "无成本商品不能进 low_margin（不编造利润）"

    # 无成本商品若出现在低档，绝无 profit_rate 字段
    for p in result["low_margin_products"]:
        if p["product_id"] == "2002":
            assert "profit_rate" not in p


def test_profit_trend_from_history(engine):
    """profit_trend 从 store_metrics_history 读（snapshot_at/sales_amount/profit_rate）。"""
    _cleanup(engine)
    _insert_product(engine, "3001", name="商品", price=20, stock=5)
    _insert_history(engine, datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc), 100.0, 0.10)
    _insert_history(engine, datetime.datetime(2026, 8, 19, tzinfo=datetime.timezone.utc), 150.0, 0.20)

    result = _run(engine)
    trend = result["profit_trend"]
    assert len(trend) == 2
    assert [t["sales_amount"] for t in trend] == [100.0, 150.0]
    assert [t["profit_rate"] for t in trend] == [0.10, 0.20]
    assert [t["snapshot_at"] for t in trend] == [
        "2026-08-18T00:00:00+00:00", "2026-08-19T00:00:00+00:00",
    ]


def test_out_of_stock_detected(engine):
    """stock<=0 商品进 out_of_stock_products；正库存不进。"""
    _cleanup(engine)
    _insert_product(engine, "4001", name="缺货", price=20, stock=0)
    _insert_product(engine, "4002", name="负库存", price=20, stock=-3)
    _insert_product(engine, "4003", name="有货", price=20, stock=7)

    result = _run(engine)
    oos = {p["product_id"] for p in result["out_of_stock_products"]}
    assert "4001" in oos
    assert "4002" in oos
    assert "4003" not in oos


def test_empty_store(engine):
    """无数据（缓存无商品）→ 返回空结构，不报错。"""
    _cleanup(engine)
    result = _run(engine)
    assert result["summary"]["product_count"] == 0
    assert result["summary"]["low_stock_count"] == 0
    assert result["summary"]["active_discount_count"] == 0
    assert result["summary"]["avg_profit_rate"] is None
    assert result["profit_trend"] == []
    assert result["low_margin_products"] == []
    assert result["out_of_stock_products"] == []
    assert result["promo_ready_products"] == []


def test_cost_match_by_ozon_product_id_not_item_id(engine):
    """真值回归（P0 F2）：成本键必须按 Ozon product_id 匹配，**不是** 1688 item_id。

    生产数据中 draft.item_id（1688 ID）与 product_task_index.product_id（Ozon ID）永不相等。
    此前 `_load_cost_payloads` 用 draft.item_id 作 dict key，而 analyze_store 用
    ozon_products_cache.product_id 去匹配 → 健不一致 → 该商品落 has_cost=False →
    profit_rate 永不填充、low_margin/promo_ready 恒空、avg_profit_rate 恒 None。

    此用例刻意让两者不一致：
        product_task_index.product_id = "5476361418"（Ozon ID）
        draft.item_id              = "993789876"（1688 ID）
    断言：成本**仍**按 Ozon product_id 匹配到该商品 → 进入 low_margin（margin 2.0 高利）
    → promo_ready。修复前该商品落 has_cost=False，本测试 FAIL。
    """
    _cleanup(engine)
    _insert_credential(engine)
    ozon_pid = "5476361418"      # Ozon product_id（product_task_index 主键）
    item_id = "993789876"        # 1688 item_id（draft.item_id，与 Ozon ID 刻意不同）
    _insert_task_item_index_mismatch(engine, uuid.uuid4(), ozon_pid, item_id,
                                     purchase_cost=5.0, margin_rate=2.0)
    _insert_product(engine, ozon_pid, name="键不一致商品", price=37, stock=50,
                    old_price=45)

    result = _run(engine)

    assert result["summary"]["avg_profit_rate"] is not None, \
        "成本键用错了（item_id 匹配不到 Ozon product_id）→ avg_profit_rate 恒 None"
    promo_ids = {p["product_id"] for p in result["promo_ready_products"]}
    assert ozon_pid in promo_ids, \
        "高利润商品未进 promo_ready——成本未按 Ozon product_id 匹配（has_cost=False）"
    lm_ids = {p["product_id"] for p in result["low_margin_products"]}
    assert ozon_pid not in lm_ids
