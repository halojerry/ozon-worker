"""v0.81 card_audit 域巡检服务测试（PLAN-card-audit-sweep-v1 §3）— 真实 PG + mock Ozon/LLM。

覆盖:四不变量动作分级（A 白名单自动修含全量回显断言 / B declined 只报告零写 /
C LLM 比对只报告 + 日封顶 + 无源映射跳过 / D 阈值内自动修 + 异常只报告）、
幂等重复轮次不重复开单、域注册（due 含 card_audit / ENABLED=0 跳过）、通知接线。
"""
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

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)

from services import card_audit_service as svc  # noqa: E402
from services import store_sync_jobs  # noqa: E402
from utils.credential_cipher import encrypt  # noqa: E402

os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")

# ── 场景数据（两卡店：101 低分缺 4191；102 declined 高分价健康）───────

_CARD_101_ECHO = {
    "id": 101, "offer_id": "o1", "name": "Картинка A",
    "description_category_id": 1, "type_id": 2,
    "weight": 100, "weight_unit": "g", "depth": 100, "width": 80, "height": 30,
    "dimension_unit": "mm",
    "images": ["http://img.example/1.jpg", "http://img.example/2.jpg"],
    "attributes": [{"id": 9001, "values": [{"value": "уже стоит"}]}],
}
_CARD_102_ECHO = {
    "id": 102, "offer_id": "o2", "name": "Картинка B",
    "description_category_id": 1, "type_id": 2,
    "weight": 200, "weight_unit": "g", "depth": 100, "width": 80, "height": 30,
    "dimension_unit": "mm",
    "images": ["http://img.example/3.jpg", "http://img.example/4.jpg"],
    "attributes": [],
}


def _make_handler(*, info_extra=None, price_map=None, rating_map=None,
                  echoes=None, import_fail=False):
    """构造 ozon_post mock：按 endpoint 路由；捕获 import POST 到 calls。"""
    calls: list[dict] = []

    def _handler(client_id, api_key, path, body=None, **kw):
        calls.append({"path": path, "body": body})
        if path == "/v3/product/list":
            return {"result": {"total": 2, "last_id": "", "items": [
                {"product_id": 101, "offer_id": "o1"},
                {"product_id": 102, "offer_id": "o2"},
            ]}}
        if path == "/v3/product/info/list":
            items = [
                {"id": 101, "offer_id": "o1", "name": "Картинка A",
                 "statuses": {"moderate_status": "approved"}, "vat": "0.1",
                 "images360": []},
                {"id": 102, "offer_id": "o2", "name": "Картинка B",
                 "statuses": {"moderate_status": "declined"}, "vat": "0.1",
                 "images360": []},
            ]
            for it in items:
                it.update((info_extra or {}).get(str(it["id"]), {}))
            return {"items": items}
        if path == "/v5/product/info/prices":
            return {"items": [
                {"product_id": 101,
                 "price": (price_map or {}).get("101") or
                 {"price": "100", "old_price": "150", "currency_code": "CNY"}},
                {"product_id": 102,
                 "price": (price_map or {}).get("102") or
                 {"price": "200", "old_price": "240", "currency_code": "CNY"}},
            ]}
        if path == "/v1/product/rating-by-sku":
            return {"products": [
                {"sku": 101, "rating": (rating_map or {}).get("101", 60),
                 "groups": [{"name": "Текст", "rating": 40.0,
                             "improve_attributes": [{"id": 4191, "name": "Аннотация"}]}]},
                {"sku": 102, "rating": (rating_map or {}).get("102", 95), "groups": []},
            ]}
        if path == "/v4/product/info/attributes":
            return {"result": [e for e in (echoes if echoes is not None else
                                           [_CARD_101_ECHO, _CARD_102_ECHO])]}
        if path == "/v3/product/import":
            if import_fail:
                raise RuntimeError("import boom")
            return {"result": {"task_id": 42}}
        return {}

    return _handler, calls


@pytest.fixture()
def cred():
    tenant = f"user_{uuid.uuid4().hex[:12]}"
    client_id = f"5{uuid.uuid4().int % 10**7}"
    enc_key = encrypt("test-api-key", f"{tenant}:{client_id}")
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        row = conn.execute(text(
            """
            INSERT INTO credentials (tenant_id, ozon_client_id, ozon_api_key_enc, api_key_masked,
                                     status, sync_enabled)
            VALUES (:t, :c, :enc, '****', 'active', TRUE)
            RETURNING id::text
            """
        ), {"t": tenant, "c": client_id, "enc": enc_key}).fetchone()
    cid = str(row[0])
    svc.reset_llm_budget()
    yield tenant, cid
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM card_audit_finding WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text("DELETE FROM listing_result_log WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text("DELETE FROM product_task_index WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text("DELETE FROM ozon_product_tasks WHERE tenant_id=:t"), {"t": tenant})
        for t in ("ozon_orders_cache", "ozon_products_cache", "credential_sync_state",
                  "store_metrics_history", "store_sync_jobs"):
            conn.execute(text(f"DELETE FROM {t} WHERE tenant_id=:t"), {"t": tenant})
        conn.execute(text("DELETE FROM credentials WHERE tenant_id=:t"), {"t": tenant})


def _findings(eng, tenant, invariant=None):
    q = ("SELECT ozon_product_id, invariant, status, severity, detail "
         "FROM card_audit_finding WHERE tenant_id=:t")
    args = {"t": tenant}
    if invariant:
        q += " AND invariant=:i"
        args["i"] = invariant
    with eng.connect() as conn:
        return conn.execute(text(q + " ORDER BY ozon_product_id"), args).fetchall()


# ── A rating_gap ─────────────────────────────────────────────


def test_a_low_rating_autofix_with_full_echo(cred):
    """rating<90 白名单内 → 走构造器发 /v3 UPDATE，载荷含现卡特征全量回显 → auto_fixed。"""
    tenant, cid = cred
    handler, calls = _make_handler()
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    imports = [c for c in calls if c["path"] == "/v3/product/import"]
    assert len(imports) == 1, "只有 101 低分卡触发自动修"
    item = imports[0]["body"]["items"][0]
    assert item["product_id"] == 101
    attr_ids = {a["id"] for a in item["attributes"]}
    assert 9001 in attr_ids, "现卡特征必须全量回显（A6 防洗卡）"
    assert 4191 in attr_ids and 11254 in attr_ids, "白名单缺口补填"
    assert item["price"] == "100" and item["old_price"] == "150"
    rows = _findings(create_engine(DB_URL), tenant, "rating_gap")
    assert len(rows) == 1 and rows[0][2] == "auto_fixed"
    assert summary["auto_fixed"] == 1
    assert summary["findings_open"] == 1, "本轮唯一 open 是 declined(102)；A 修好不产 open"


def test_a_above_threshold_no_action(cred):
    """rating>=90 不动：无 import 调用、无 rating_gap finding。"""
    tenant, cid = cred
    handler, calls = _make_handler(rating_map={"101": 95})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        svc.run_card_audit(tenant, cid)
    assert not [c for c in calls if c["path"] == "/v3/product/import"]
    assert _findings(create_engine(DB_URL), tenant, "rating_gap") == []


def test_a_autofix_disabled_report_only(cred, monkeypatch):
    """CARD_AUDIT_AUTOFIX=0 → A 只落 finding，零写调用。"""
    tenant, cid = cred
    monkeypatch.setenv("CARD_AUDIT_AUTOFIX", "0")
    handler, calls = _make_handler()
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    assert not [c for c in calls if c["path"] == "/v3/product/import"]
    rows = _findings(create_engine(DB_URL), tenant, "rating_gap")
    assert len(rows) == 1 and rows[0][2] == "open"
    assert rows[0][4]["reason"] == "autofix_disabled"
    assert summary["auto_fixed"] == 0


def test_a_constructor_abstain_opens_finding(cred):
    """构造器保守放弃（回显缺维度 zero_dims）→ finding open 带 reason，不裸发。"""
    tenant, cid = cred
    bad_echo = {k: v for k, v in _CARD_101_ECHO.items() if k != "weight"}
    handler, calls = _make_handler(echoes=[bad_echo])
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    assert not [c for c in calls if c["path"] == "/v3/product/import"]
    rows = _findings(create_engine(DB_URL), tenant, "rating_gap")
    assert len(rows) == 1 and rows[0][2] == "open"
    assert rows[0][4]["reason"] == "zero_dims"
    assert summary["autofix_failed_open"] == 1


# ── B declined ───────────────────────────────────────────────


def test_b_declined_report_only_zero_writes(cred):
    """moderate_status=declined → finding open（high），零 import 写调用。"""
    tenant, cid = cred
    handler, calls = _make_handler(rating_map={"101": 95})  # 101 高分：隔离 A 的自动修
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    imports = [c for c in calls if c["path"] == "/v3/product/import"]
    assert not imports, "declined 只报告：全轮零写操作"
    assert not [c for c in calls if "/v1/product/pictures" in c["path"]]
    rows = _findings(create_engine(DB_URL), tenant, "declined")
    assert len(rows) == 1
    assert rows[0][0] == "102" and rows[0][2] == "open" and rows[0][3] == "high"
    assert summary["declined"] == 1


def test_b_declined_moderate_status_persisted_to_cache(cred):
    """巡检顺手把 moderate_status 落 ozon_products_cache（B 的底座）。"""
    tenant, cid = cred
    handler, _ = _make_handler()
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        svc.run_card_audit(tenant, cid)
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        rows = dict(conn.execute(text(
            "SELECT product_id, moderate_status FROM ozon_products_cache WHERE tenant_id=:t",
        ), {"t": tenant}).fetchall())
    assert rows.get("102") == "declined" and rows.get("101") == "approved"


# ── C source_mismatch ────────────────────────────────────────


def _seed_source(eng, tenant, cid, product_id, title="不锈钢沥水篮", path="家居>厨房"):
    task_id = str(uuid.uuid4())
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO ozon_product_tasks (id, tenant_id, payload) "
            "VALUES (CAST(:id AS uuid), :t, '{}'::jsonb)"
        ), {"id": task_id, "t": tenant})
        conn.execute(text(
            "INSERT INTO product_task_index (product_id, tenant_id, offer_id, task_id, credential_id) "
            "VALUES (:p, :t, :o, CAST(:id AS uuid), CAST(:c AS uuid))"
        ), {"p": product_id, "t": tenant, "o": f"off_{product_id}", "id": task_id, "c": cid})
        conn.execute(text(
            "INSERT INTO listing_result_log (task_db_id, tenant_id, source_title_cn, "
            "source_category_path, ozon_product_id) "
            "VALUES (:id, :t, :title, :path, :p)"
        ), {"id": task_id, "t": tenant, "title": title, "path": path, "p": product_id})


def test_c_mismatch_reported_no_writes(cred):
    """LLM 判 mismatch → finding open（high）；全程零改卡调用。"""
    tenant, cid = cred
    eng = create_engine(DB_URL)
    _seed_source(eng, tenant, cid, "102")
    monkey_llm = {"verdict": "mismatch", "reason": "源是沥水篮卡是别的"}
    handler, calls = _make_handler(rating_map={"101": 95})
    with patch("utils.ozon_client.ozon_post", side_effect=handler), \
         patch("utils.mxou_api.call_mxou_chat_api", return_value=json.dumps(monkey_llm)), \
         patch.dict(os.environ, {"CARD_AUDIT_LLM_TOKEN": "sk-test"}):
        summary = svc.run_card_audit(tenant, cid)
    assert not [c for c in calls if c["path"] == "/v3/product/import"], "C 绝不改卡"
    rows = _findings(eng, tenant, "source_mismatch")
    assert len(rows) == 1 and rows[0][0] == "102" and rows[0][2] == "open"
    assert rows[0][4]["llm_verdict"] == "mismatch"
    assert summary["source_checked"] == 1


def test_c_cap_daily(cred, monkeypatch):
    """超日封顶：只花 1 次 LLM，summary 标 capped，剩余卡不比对。"""
    tenant, cid = cred
    eng = create_engine(DB_URL)
    _seed_source(eng, tenant, cid, "101", title="错货甲")
    _seed_source(eng, tenant, cid, "102", title="错货乙")
    monkeypatch.setenv("CARD_AUDIT_LLM_CAP_DAILY", "1")
    monkeypatch.setenv("CARD_AUDIT_LLM_TOKEN", "sk-test")
    svc.reset_llm_budget()
    llm_calls = []

    def _llm(token, system, user, **kw):
        llm_calls.append(user)
        return json.dumps({"verdict": "mismatch", "reason": "x"})

    handler, _ = _make_handler(rating_map={"101": 95})
    with patch("utils.ozon_client.ozon_post", side_effect=handler), \
         patch("utils.mxou_api.call_mxou_chat_api", side_effect=_llm):
        summary = svc.run_card_audit(tenant, cid)
    assert len(llm_calls) == 1, "日封顶 1 次：第二张卡不调 LLM"
    assert summary["capped"] is True
    assert summary["source_mismatch"] == 1


def test_c_no_source_mapping_skipped(cred):
    """无 product_task_index→listing_result_log 映射（workbuddy 时代卡）→ 跳过不调 LLM。"""
    tenant, cid = cred
    llm_calls = []

    def _llm(token, system, user, **kw):
        llm_calls.append(user)
        return json.dumps({"verdict": "mismatch", "reason": "x"})

    handler, _ = _make_handler(rating_map={"101": 95})
    with patch("utils.ozon_client.ozon_post", side_effect=handler), \
         patch("utils.mxou_api.call_mxou_chat_api", side_effect=_llm), \
         patch.dict(os.environ, {"CARD_AUDIT_LLM_TOKEN": "sk-test"}):
        summary = svc.run_card_audit(tenant, cid)
    assert not llm_calls
    assert summary["source_skipped_no_source"] == 2
    assert _findings(create_engine(DB_URL), tenant, "source_mismatch") == []


def test_c_no_token_skipped(cred, monkeypatch):
    """CARD_AUDIT_LLM_TOKEN 未配置 → C 整体跳过（其余不变量照跑）。"""
    tenant, cid = cred
    monkeypatch.delenv("CARD_AUDIT_LLM_TOKEN", raising=False)
    handler, _ = _make_handler(rating_map={"101": 95})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    assert summary["llm_skipped_no_token"] is True


# ── D price_sanity ───────────────────────────────────────────


def test_d_missing_old_price_autofix(cred):
    """缺 old_price → 家族构造器单字段自动修（old_price 抬到规则下限）→ auto_fixed。"""
    tenant, cid = cred
    handler, calls = _make_handler(
        rating_map={"101": 95},
        price_map={"101": {"price": "100", "old_price": "", "currency_code": "CNY"}})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    imports = [c for c in calls if c["path"] == "/v3/product/import"]
    assert len(imports) == 1
    item = imports[0]["body"]["items"][0]
    assert item["product_id"] == 101
    assert item["old_price"] == "120", "old_price 缺失 → enforce_old_price_rule 下限 100×1.2"
    attr_ids = {a["id"] for a in item["attributes"]}
    assert 9001 in attr_ids, "价格修复也必须全量回显现卡特征"
    rows = _findings(create_engine(DB_URL), tenant, "price_sanity")
    assert len(rows) == 1 and rows[0][2] == "auto_fixed"
    assert summary["price_fixed"] == 1


def test_d_old_price_gap_insufficient_autofix(cred):
    """差价不足（old 105 < 100×1.2=120）→ 自动修抬到合规。"""
    tenant, cid = cred
    handler, calls = _make_handler(
        rating_map={"101": 95},
        price_map={"101": {"price": "100", "old_price": "105", "currency_code": "CNY"}})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        svc.run_card_audit(tenant, cid)
    imports = [c for c in calls if c["path"] == "/v3/product/import"]
    assert len(imports) == 1 and imports[0]["body"]["items"][0]["old_price"] == "120"


def test_d_min_price_anomaly_report_only(cred):
    """min_price < 售价 50% → 只报告，零写调用。"""
    tenant, cid = cred
    handler, calls = _make_handler(
        rating_map={"101": 95},
        info_extra={"101": {"min_price": "40"}})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    assert not [c for c in calls if c["path"] == "/v3/product/import"]
    rows = _findings(create_engine(DB_URL), tenant, "price_sanity")
    assert len(rows) == 1 and rows[0][2] == "open"
    assert rows[0][4]["reason"] == "min_price_below_half_price"
    assert summary["price_reported"] == 1 and summary["price_fixed"] == 0


def test_d_healthy_price_no_action(cred):
    """价格健康（默认场景 150/100、240/200）→ 无 D finding。"""
    tenant, cid = cred
    handler, calls = _make_handler(rating_map={"101": 95})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        svc.run_card_audit(tenant, cid)
    assert not [c for c in calls if c["path"] == "/v3/product/import"]
    assert _findings(create_engine(DB_URL), tenant, "price_sanity") == []


# ── 幂等 / 通知 / 域注册 ─────────────────────────────────────


def test_idempotent_rerun_no_duplicate_open(cred):
    """同店跑两轮：open finding 不重复开（原位更新）。"""
    tenant, cid = cred
    handler, _ = _make_handler()
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        svc.run_card_audit(tenant, cid)
        svc.run_card_audit(tenant, cid)
    rows = _findings(create_engine(DB_URL), tenant, "declined")
    assert len(rows) == 1, "同卡同不变量重复轮次不重复开 open 行"


def test_rerun_after_autofix_recurrence_new_row(cred):
    """auto_fixed 后问题复现 → 新行（旧行保留）。"""
    tenant, cid = cred
    handler, _ = _make_handler()
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        svc.run_card_audit(tenant, cid)  # 101 低分 → auto_fixed
    # 第二轮 rating 更新被 mock 固定 → 仍低分复现
    with patch("utils.ozon_client.ozon_post", side_effect=_make_handler()[0]):
        svc.run_card_audit(tenant, cid)
    rows = _findings(create_engine(DB_URL), tenant, "rating_gap")
    statuses = sorted(r[2] for r in rows)
    assert statuses == ["auto_fixed", "auto_fixed"], "复现另开新行（无 open 残留）"


def test_notify_on_findings(cred, monkeypatch):
    """有发现 → 经 _send_task_notify 通道发一条；零发现零修复 → 不发。

    通知挂在调度入口 run_card_audit_if_due（TASK_NOTIFY_URL 未配置时通道内静默跳过）。
    """
    tenant, cid = cred
    eng = create_engine(DB_URL)
    monkeypatch.delenv("CARD_AUDIT_ENABLED", raising=False)
    notified = []
    handler, _ = _make_handler()  # 101 低分（自动修）+ 102 declined
    with patch("utils.ozon_client.ozon_post", side_effect=handler), \
         patch("orchestrator.task_processor._send_task_notify",
               side_effect=lambda *a, **k: notified.append((a, k))):
        summary = svc.run_card_audit_if_due(tenant, cid)
        assert summary is not None
        assert len(notified) == 1
        args, kwargs = notified[0]
        task_id = args[0] if args else kwargs.get("task_id", "")
        assert task_id.startswith("card_audit:")
        graph_result = kwargs.get("graph_result") or (args[2] if len(args) > 2 else {})
        assert graph_result.get("product_summary", {}).get("domain") == "card_audit"

        # 零发现零修复的一轮 → 不打扰：清 finding + 清水位（让 if_due 再次放行）
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM card_audit_finding WHERE tenant_id=:t"), {"t": tenant})
            conn.execute(text("DELETE FROM credential_sync_state WHERE tenant_id=:t"), {"t": tenant})
        handler2, _ = _make_handler(
            info_extra={"102": {"statuses": {"moderate_status": "approved"}}},
            rating_map={"101": 95})
        notified.clear()
        with patch("utils.ozon_client.ozon_post", side_effect=handler2):
            svc.run_card_audit_if_due(tenant, cid)
        assert notified == []


def test_run_if_due_respects_watermark_and_switch(cred, monkeypatch):
    """调度入口：水位未到 → None；ENABLED=0 → None；水位到点 → 跑并推进。"""
    tenant, cid = cred
    eng = create_engine(DB_URL)
    monkeypatch.delenv("CARD_AUDIT_ENABLED", raising=False)
    handler, _ = _make_handler()

    # 首轮：无水位 → due → 跑并推进水位
    with patch("utils.ozon_client.ozon_post", side_effect=handler), \
         patch("orchestrator.task_processor._send_task_notify"):
        s1 = svc.run_card_audit_if_due(tenant, cid)
    assert s1 is not None and s1["total_cards"] == 2
    with eng.connect() as conn:
        ds = conn.execute(text(
            "SELECT domain_state FROM credential_sync_state "
            "WHERE tenant_id=:t AND credential_id=CAST(:c AS uuid)"
        ), {"t": tenant, "c": cid}).fetchone()
    assert "card_audit" in (ds[0] or {}) and ds[0]["card_audit"].get("last_synced_at")

    # 水位刚推进（日级间隔）→ 本轮不该跑
    handler3, _ = _make_handler()
    with patch("utils.ozon_client.ozon_post", side_effect=handler3):
        assert svc.run_card_audit_if_due(tenant, cid) is None

    # ENABLED=0 → 直接跳过（即使无水位的新店）
    monkeypatch.setenv("CARD_AUDIT_ENABLED", "0")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM credential_sync_state WHERE tenant_id=:t"), {"t": tenant})
    assert svc.run_card_audit_if_due(tenant, cid) is None


def test_domain_registration_includes_card_audit(cred, monkeypatch):
    """due_credentials 域注册：新店 card_audit due；水位新鲜不 due；总闸=0 不注册。"""
    tenant, cid = cred
    eng = create_engine(DB_URL)
    monkeypatch.delenv("CARD_AUDIT_ENABLED", raising=False)

    due = store_sync_jobs.due_credentials()
    row = next((d for d in due if d["credential_id"] == cid), None)
    assert row is not None and "card_audit" in row["domains_due"], "新店日级域应 due"

    # 推进 card_audit 水位到当前 → 不再 due（其余域水位不动）
    from services.store_sync_service import _domain_state_update
    _domain_state_update(tenant, cid, "card_audit", last_synced_at=datetime.datetime.now(
        datetime.timezone.utc).isoformat())
    due = store_sync_jobs.due_credentials()
    row = next((d for d in due if d["credential_id"] == cid), None)
    assert row is not None and "card_audit" not in row["domains_due"]

    # 总闸 =0 → 域不注册
    monkeypatch.setenv("CARD_AUDIT_ENABLED", "0")
    due = store_sync_jobs.due_credentials()
    row = next((d for d in due if d["credential_id"] == cid), None)
    assert row is not None and "card_audit" not in row["domains_due"]


def test_run_job_branch_does_not_fail_job(cred):
    """_run_job card_audit 分支：巡检异常不置 job failed（与 returns/rating 域同口径）。"""
    import services.store_sync_scheduler as sched
    tenant, cid = cred
    eng = create_engine(DB_URL)
    # 测试卫生：claim_next 是全局的，清空残留 job 防捡到上次遗留行
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM store_sync_jobs"))
    job = store_sync_jobs.enqueue(tenant, cid, kind="initial", trigger="bind")
    claimed = store_sync_jobs.claim_next()
    assert claimed is not None and claimed["id"] == job["id"]
    with patch.object(sched.store_sync_service, "sync_store",
                      return_value={"orders": {"synced": 0}, "products": {"synced": 0}}), \
         patch("services.card_audit_service.run_card_audit_if_due",
               side_effect=RuntimeError("audit boom")):
        sched._run_job(job)
    got = store_sync_jobs.get_job(tenant, job["id"])
    assert got["status"] == "ok", "巡检异常不影响订单/商品域 job 成功语义"
