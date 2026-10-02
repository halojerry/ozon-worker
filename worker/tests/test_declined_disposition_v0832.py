"""v0.83.2 B 不变量分级处置测试（fix/card-audit-declined-v1）。

覆盖：
1. utils/declined_disposition 纯函数——决策阶梯（残壳/资质族→archive、可修族
   →auto_repair、无码/未知混码→report、erased 噪音剔除）+ 回显定向补丁
   （数值 RU 逗号/取整、空值剔除、重量密度、维度 clamp、A6 其余字节不动）；
2. card_audit 服务级（真 PG + mock Ozon）——修复经唯一构造器全量回显、
   归档批量出口 + kill-switch、validation_fail 触发面、无码保守零写。

背景（2026-10-02 4718259 店 67 卡实盘）：declined 卡零处置累积的根因修复。
DB 访问统一 exec_driver_sql（静态 SQL + %s + 参数元组，参数化编译）。
"""
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)

from services import card_audit_service as svc  # noqa: E402
from utils.credential_cipher import encrypt  # noqa: E402
from utils.declined_disposition import (  # noqa: E402
    FIXABLE_DECLINE_CODES,
    HOPELESS_DECLINE_CODES,
    declined_disposition,
    patch_echo_for_declines,
)

os.environ.setdefault("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")


def _pg_ready() -> bool:
    try:
        with create_engine(DB_URL).connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_ready(), reason="本地 PG 不可达")

# ═══ 1. 纯函数：决策阶梯 ═══

def _info(name="Электрический вентилятор", codes=(), statuses=None, extra=None):
    it = {"name": name,
          "statuses": statuses if statuses is not None
          else {"moderate_status": "declined"},
          "errors": [{"code": c, "attribute_id": 9001} for c in codes]}
    if extra:
        it.update(extra)
    return it


def test_disposition_title_shell_archives():
    d = declined_disposition(_info(name=", ,", codes=("DESCRIPTION_DECLINE",)))
    assert d["action"] == "archive" and d["reason"] == "title_shell"
    d2 = declined_disposition(_info(name="8000 66", codes=("VALUE_MUST_BE_INTEGER",)))
    assert d2["action"] == "archive"


def test_disposition_hopeless_codes_archive():
    d = declined_disposition(_info(codes=("BR_ASSORTMENT", "DESCRIPTION_DECLINE")))
    assert d["action"] == "archive" and "BR_ASSORTMENT" in d["reason"]


def test_disposition_fixable_codes_auto_repair():
    d = declined_disposition(_info(codes=("VALUE_MUST_BE_DECIMAL", "DESCRIPTION_DECLINE")))
    assert d["action"] == "auto_repair" and d["title_healthy"] is True


def test_disposition_no_codes_reports():
    d = declined_disposition(_info(codes=()))
    assert d["action"] == "report" and d["reason"] == "no_error_codes"


def test_disposition_unknown_mixed_reports():
    d = declined_disposition(_info(codes=("VALUE_MUST_BE_DECIMAL", "SOMETHING_NEW")))
    assert d["action"] == "report" and "SOMETHING_NEW" in d["reason"]


def test_disposition_erased_is_noise_not_reason():
    # erased 单独在场 = 无有效拒因 → report（不误归档）
    d = declined_disposition(_info(codes=("erased_attribute_value",)))
    assert d["action"] == "report"
    # erased + 可修 → 可修主导
    d2 = declined_disposition(_info(codes=("erased_attribute_value", "VALUE_MIN_LIMIT")))
    assert d2["action"] == "auto_repair"


def test_code_families_disjoint():
    assert not (FIXABLE_DECLINE_CODES & HOPELESS_DECLINE_CODES)


# ═══ 2. 纯函数：回显定向补丁 ═══

_ECHO = {
    "id": 102, "offer_id": "o2", "name": "Электрический вентилятор",
    "description_category_id": 1, "type_id": 2,
    "weight": 200, "weight_unit": "g", "depth": 100, "width": 80, "height": 30,
    "dimension_unit": "mm",
    "images": ["http://img.example/1.jpg"],
    "attributes": [
        {"id": 9001, "values": [{"value": "1,5"}]},   # RU 逗号小数
        {"id": 9002, "values": [{"value": "чистая ткань"}]},  # 文本属性：绝不动
    ],
}


def test_patch_numeric_ru_comma_and_integer():
    patched, changes = patch_echo_for_declines(
        _ECHO, _info(codes=("VALUE_MUST_BE_DECIMAL",)))
    a = next(a for a in patched["attributes"] if a["id"] == 9001)
    assert a["values"][0]["value"] == "1.5"
    assert "numeric:9001" in changes
    # 文本属性不动（A6 精神：只修点名问题）
    t = next(a for a in patched["attributes"] if a["id"] == 9002)
    assert t["values"][0]["value"] == "чистая ткань"
    # 原 echo 不被就地改写
    assert _ECHO["attributes"][0]["values"][0]["value"] == "1,5"


def test_patch_integer_coercion():
    patched, _ = patch_echo_for_declines(
        {**_ECHO, "attributes": [{"id": 9001, "values": [{"value": "2,7"}]}]},
        _info(codes=("VALUE_MUST_BE_INTEGER",)))
    assert patched["attributes"][0]["values"][0]["value"] == "2"  # 取整=截断（sanitize 既有语义）


def test_patch_strip_empty_values():
    patched, changes = patch_echo_for_declines(
        {**_ECHO, "attributes": [
            {"id": 9001, "values": [{"value": ""}, {"value": "5"}]}]},
        _info(codes=("error_attribute_values_empty",)))
    assert patched["attributes"][0]["values"] == [{"value": "5"}]
    assert any(c.startswith("strip_empty") for c in changes)


def test_patch_weight_floor():
    # 20g / 100×80×30mm（240cm³，floor≈96g）→ 上调
    patched, changes = patch_echo_for_declines(
        {**_ECHO, "weight": 20},
        _info(codes=("ML_INCORRECT_VOLUME_WEIGHT",)))
    assert patched["weight"] > 20 and any(
        c.startswith("weight_floor") for c in changes)


def test_patch_dim_clamp():
    patched, changes = patch_echo_for_declines(
        {**_ECHO, "depth": 20, "height": 500},
        _info(codes=("INCORRECT_DIMENSION",)))
    assert patched["depth"] == 42 and patched["height"] == 200  # height 上界 200
    assert len([c for c in changes if c.startswith("dim_clamp")]) == 2


def test_patch_unrelated_code_zero_changes():
    patched, changes = patch_echo_for_declines(
        _ECHO, _info(codes=("DESCRIPTION_DECLINE",)))  # 构造器侧处理，本函数透传
    assert changes == [] and patched == _ECHO


# ═══ 3. 服务级（真 PG + mock Ozon）═══

def _svc_handler(*, info_extra=None, echoes=None):
    """单 declined 卡（201）+ 健康对照（202）的最小 handler。"""
    calls: list[dict] = []

    def _handler(client_id, api_key, path, body=None, **kw):
        calls.append({"path": path, "body": body})
        if path == "/v3/product/list":
            return {"result": {"total": 2, "last_id": "", "items": [
                {"product_id": 201, "offer_id": "d1"},
                {"product_id": 202, "offer_id": "d2"},
            ]}}
        if path == "/v3/product/info/list":
            items = [
                {"id": 201, "offer_id": "d1", "name": "Электрический вентилятор",
                 "statuses": {"moderate_status": "declined"},
                 "errors": (info_extra or {}).get("errors_201", [])},
                {"id": 202, "offer_id": "d2", "name": "Кухонная полка",
                 "statuses": {"moderate_status": "approved"}},
            ]
            for it in items:
                ov = (info_extra or {}).get(f"override_{it['id']}")
                if ov:
                    it.update(ov)
            return {"items": items}
        if path == "/v5/product/info/prices":
            return {"items": [
                {"product_id": p, "price": {"price": "100", "old_price": "150",
                                            "currency_code": "CNY"}}
                for p in (201, 202)]}
        if path == "/v1/product/rating-by-sku":
            return {"products": [{"sku": p, "rating": 95, "groups": []}
                                 for p in (201, 202)]}
        if path == "/v4/product/info/attributes":
            if echoes is not None:
                return {"result": echoes}
            return {"result": [{
                "id": 201, "offer_id": "d1", "name": "Электрический вентилятор",
                "description_category_id": 1, "type_id": 2,
                "weight": 200, "weight_unit": "g",
                "depth": 100, "width": 80, "height": 30, "dimension_unit": "mm",
                "images": ["http://img.example/1.jpg"],
                "attributes": [{"id": 9001, "values": [{"value": "1,5"}]}],
            }, {
                "id": 202, "offer_id": "d2", "name": "Кухонная полка",
                "description_category_id": 1, "type_id": 2,
                "weight": 200, "weight_unit": "g",
                "depth": 100, "width": 80, "height": 30, "dimension_unit": "mm",
                "images": ["http://img.example/2.jpg"],
                "attributes": [],
            }]}
        if path == "/v3/product/import":
            return {"result": {"task_id": 42}}
        if path == "/v1/product/archive":
            return {"result": True}
        return {}

    return _handler, calls


_CRED_INSERT_SQL = (
    "INSERT INTO credentials (tenant_id, ozon_client_id, ozon_api_key_enc, "
    "api_key_masked, status, sync_enabled) "
    "VALUES (%s, %s, %s, '****', 'active', TRUE) RETURNING id::text"
)
_FINDINGS_SQL = (
    "SELECT ozon_product_id, status, severity, detail FROM card_audit_finding "
    "WHERE tenant_id=%s AND invariant=%s"
)


@pytest.fixture()
def cred():
    tenant = f"user_{uuid.uuid4().hex[:12]}"
    client_id = f"5{uuid.uuid4().int % 10**7}"
    eng = create_engine(DB_URL)
    enc_key = encrypt("test-api-key", f"{tenant}:{client_id}")
    with eng.begin() as conn:
        row = conn.exec_driver_sql(_CRED_INSERT_SQL, (tenant, client_id, enc_key)).fetchone()
    cid = str(row[0])
    svc.reset_llm_budget()
    yield tenant, cid
    # 清理：静态 SQL + %s + 参数元组（参数化编译，零拼接）
    with eng.begin() as conn:
        conn.exec_driver_sql("DELETE FROM card_audit_finding WHERE tenant_id=%s", (tenant,))
        conn.exec_driver_sql("DELETE FROM listing_result_log WHERE tenant_id=%s", (tenant,))
        conn.exec_driver_sql("DELETE FROM product_task_index WHERE tenant_id=%s", (tenant,))
        conn.exec_driver_sql("DELETE FROM ozon_product_tasks WHERE tenant_id=%s", (tenant,))
        conn.exec_driver_sql("DELETE FROM ozon_products_cache WHERE tenant_id=%s", (tenant,))
        conn.exec_driver_sql("DELETE FROM credential_sync_state WHERE tenant_id=%s", (tenant,))
        conn.exec_driver_sql("DELETE FROM store_metrics_history WHERE tenant_id=%s", (tenant,))
        conn.exec_driver_sql("DELETE FROM store_sync_jobs WHERE tenant_id=%s", (tenant,))
        conn.exec_driver_sql("DELETE FROM credentials WHERE tenant_id=%s", (tenant,))


def _findings(eng, tenant, invariant):
    with eng.connect() as conn:
        return conn.exec_driver_sql(_FINDINGS_SQL, (tenant, invariant)).fetchall()


def test_svc_repair_via_constructor_full_echo(cred):
    """可修族（RU 逗号数值）→ 定向补丁 + 唯一构造器 UPDATE，finding resolved。"""
    tenant, cid = cred
    handler, calls = _svc_handler(info_extra={
        "errors_201": [{"code": "VALUE_MUST_BE_DECIMAL", "attribute_id": 9001}]})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    imports = [c for c in calls if c["path"] == "/v3/product/import"]
    assert len(imports) == 1, "可修 declined 恰一次 UPDATE"
    item = imports[0]["body"]["items"][0]
    assert item["product_id"] == 201
    a = next(a for a in item["attributes"] if a["id"] == 9001)
    assert a["values"][0]["value"] == "1.5", "RU 逗号已在回显补丁中清洗"
    rows = _findings(create_engine(DB_URL), tenant, "declined")
    assert len(rows) == 1 and rows[0][0] == "201"
    assert rows[0][1] == "auto_fixed" and rows[0][3]["action"] == "auto_repaired"
    assert rows[0][3]["patched"] == ["numeric:9001"]
    assert summary["declined_repaired"] == 1 and summary["declined_reported"] == 0


def test_svc_archive_hopeless_batch(cred):
    """资质族（BR_ASSORTMENT）→ /v1/product/archive，finding resolved auto_archived。"""
    tenant, cid = cred
    handler, calls = _svc_handler(info_extra={
        "errors_201": [{"code": "BR_ASSORTMENT", "attribute_id": 0}]})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    archives = [c for c in calls if c["path"] == "/v1/product/archive"]
    assert len(archives) == 1 and archives[0]["body"] == {"product_id": [201]}
    assert not [c for c in calls if c["path"] == "/v3/product/import"]
    rows = _findings(create_engine(DB_URL), tenant, "declined")
    assert rows[0][1] == "auto_fixed" and rows[0][3]["action"] == "auto_archived"
    assert summary["declined_archived"] == 1


def test_svc_archive_killswitch_reports_instead(cred, monkeypatch):
    """CARD_AUDIT_DECLINED_AUTO_ARCHIVE=0 → 不归档，落 archive_suggested open。"""
    tenant, cid = cred
    monkeypatch.setenv("CARD_AUDIT_DECLINED_AUTO_ARCHIVE", "0")
    handler, calls = _svc_handler(info_extra={
        "errors_201": [{"code": "BR_ASSORTMENT", "attribute_id": 0}]})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    assert not [c for c in calls if c["path"] == "/v1/product/archive"]
    rows = _findings(create_engine(DB_URL), tenant, "declined")
    assert rows[0][1] == "open" and rows[0][3]["action"] == "archive_suggested"
    assert summary["declined_archived"] == 0 and summary["declined_reported"] == 1


def test_svc_validation_fail_triggers_b(cred):
    """validation_status=fail（moderate 空）→ 同路处置（触发面 v0.83.2 扩展）。"""
    tenant, cid = cred
    handler, calls = _svc_handler(info_extra={
        "errors_201": [{"code": "ML_INCORRECT_VOLUME_WEIGHT", "attribute_id": 0}],
        "override_201": {"statuses": {"moderate_status": "",
                                      "validation_status": "fail"}}})
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    imports = [c for c in calls if c["path"] == "/v3/product/import"]
    assert len(imports) == 1
    assert imports[0]["body"]["items"][0]["product_id"] == 201
    rows = _findings(create_engine(DB_URL), tenant, "declined")
    assert rows[0][1] == "auto_fixed"
    assert summary["declined"] == 1


def test_svc_no_codes_reports_zero_writes(cred):
    """无错误码 → 保守人工：零写调用（对齐 v081 只报告语义）。"""
    tenant, cid = cred
    handler, calls = _svc_handler()
    with patch("utils.ozon_client.ozon_post", side_effect=handler):
        summary = svc.run_card_audit(tenant, cid)
    assert not [c for c in calls if c["path"] in ("/v3/product/import", "/v1/product/archive")]
    rows = _findings(create_engine(DB_URL), tenant, "declined")
    assert rows[0][1] == "open" and rows[0][3]["action"] == "report_only"
    assert summary["declined_reported"] == 1
