"""fix/dedupe-batch1 回归测试 — 重复造轮子批次①（A1/A2/B7/C7）。

A1: follow_sell_import_node 占位划线价收敛唯一规则 enforce_old_price_rule
    （v0.81.1 立法：old_price ≥ price×1.2 且差价 <400 时必须 ≥20）——
    低价跟卖卡（purchase_cost=10 → placeholder 20 → 旧手写 int(*1.3)=26，
    差价 6）违反 Ozon 差价契约被拒。回归：purchase_cost=10 时 import-by-sku
    body 的 old_price 差价必须 ≥20。
A2: ai_field_service._get_cny_rub_rate 改走 utils.fx_rate_service.resolve_cny_rub_rate
    三级源链（BL-01：exchange_rates 死缓存只读是事故根因）。
B7: clone_card_builder.MEDIA_ATTR_IDS 不再自拷，恒等 content_enrich 同名常量。
C7: LOCAL_ 错误码常量化 utils/pipeline_error_codes.py——两消费文件禁裸字符串
    （棘轮），路由表/集合键以常量构建仍与字面量值互通。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ═══════════════════════════════════════════════════════════
# A1: follow_sell_import_node 占位划线价过唯一规则
# ═══════════════════════════════════════════════════════════

class _FakeState:
    """模拟 GlobalState — follow_sell_import_node 读取的最小字段面。"""

    def __init__(self, envelope):
        self.envelope = envelope
        self.ozon_client_id = "123"
        self.ozon_api_key = "key"
        self.currency_code = "CNY"
        self.token = "sk-test"


def _envelope(purchase_cost: float) -> dict:
    return {
        "draft": {
            "ozon_product_id": "3852000144",
            "title": "Тестовый товар",
            "ozon_title": "Оригинальный тестовый товар",
            "images": ["https://cdn.ozon.ru/img1.jpg"],
            "ozon_category": {
                "description_category_id": "17027918",
                "type_id": "971311385",
                "category_path": "Автозапчасти > Подвеска > Амортизаторы",
            },
            "purchase_cost": purchase_cost,
            "item_id": "980815374096",
        },
        "extensions": {"follow_sell": True, "follow_type": "api"},
    }


def _run_api_import(monkeypatch, purchase_cost: float) -> tuple[dict, dict]:
    """api 跟卖模式跑通节点，捕获 import-by-sku 请求 body。"""
    from graphs.nodes import follow_sell_import_node as mod

    captured: dict = {}

    def _fake_post(client_id, api_key, endpoint, body=None, timeout=60, **kw):
        if "import-by-sku" in endpoint:
            captured["body"] = body
            return {"result": {"task_id": "12345"}}
        if "import/info" in endpoint:
            return {"result": {"items": [{"product_id": 999888777, "status": "imported"}]}}
        if "product/info/list" in endpoint:
            return {"items": [{"id": 999888777, "product_id": 999888777,
                               "description_category_id": 17027918,
                               "type_id": 970742618}]}
        if "description-category/attribute" in endpoint:
            return {"result": [{"id": 8229, "name": "Тип", "is_collection": False}]}
        if "product/info/attributes" in endpoint:
            return {"result": {"items": []}}
        raise RuntimeError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(mod, "ozon_post", _fake_post)
    monkeypatch.setattr(mod, "_verify_category_schema",
                        lambda cid, akey, dc, tp: True)
    monkeypatch.setattr("time.sleep", lambda s: None)  # 轮询 sleep 置空加速

    result = mod.follow_sell_import_node(_FakeState(_envelope(purchase_cost)))
    return result, captured.get("body") or {}


@pytest.mark.parametrize("purchase_cost,placeholder", [(10.0, 20), (0, 100)])
def test_a1_placeholder_old_price_gap_at_least_20(monkeypatch, purchase_cost, placeholder):
    """低价跟卖卡：import-by-sku body 的 old_price 必须满足差价 ≥20 且 ≥price×1.2。

    purchase_cost=10 → placeholder 20：旧 int(*1.3)=26 差价 6 被 Ozon 拒；
    规则下限 = max(ceil(20×1.2)=24, 20+20=40) = 40。purchase_cost=0 → placeholder
    100：下限 = max(120, 120) = 120。
    """
    result, body = _run_api_import(monkeypatch, purchase_cost)
    assert result.get("product_id") == "999888777"
    item = body["items"][0]
    price = int(item["price"])
    old_price = int(item["old_price"])
    assert price == placeholder
    assert old_price - price >= 20, f"差价 {old_price - price} < 20（Ozon 契约）"
    assert old_price >= price * 1.2


def test_a1_compliant_candidate_preserved(monkeypatch):
    """高价卡（placeholder 2000）：合规候选 int(*1.3)=2600 不被规则抬高。"""
    _, body = _run_api_import(monkeypatch, 1000.0)
    item = body["items"][0]
    assert int(item["price"]) == 2000
    assert int(item["old_price"]) == 2600


# ═══════════════════════════════════════════════════════════
# A2: _get_cny_rub_rate 走三级源链，不再直读死缓存
# ═══════════════════════════════════════════════════════════

def test_a2_rate_delegates_to_fx_service(monkeypatch):
    """汇率值必须来自 resolve_cny_rub_rate；直读 exchange_rates 死缓存即红。"""
    import services.ai_field_service as afs
    import utils.fx_rate_service as fx
    import utils.local_db_manager as ldm

    monkeypatch.setattr(fx, "resolve_cny_rub_rate", lambda: (11.8, "pg_cache"))

    def _dead_cache(*a, **k):
        raise AssertionError("BL-01: 不得绕过三级源链直读 exchange_rates 死缓存")

    monkeypatch.setattr(ldm.LocalDBManager, "get_exchange_rate", _dead_cache)
    assert afs._get_cny_rub_rate() == 11.8


def test_a2_defensive_fallback_12(monkeypatch):
    """resolve 异常（理论上不发生）→ 保持旧兜底 12.0 语义。"""
    import services.ai_field_service as afs
    import utils.fx_rate_service as fx

    def _boom():
        raise RuntimeError("fx chain exploded")

    monkeypatch.setattr(fx, "resolve_cny_rub_rate", _boom)
    assert afs._get_cny_rub_rate() == 12.0


# ═══════════════════════════════════════════════════════════
# B7: MEDIA_ATTR_IDS 单一事实源
# ═══════════════════════════════════════════════════════════

def test_b7_media_attr_ids_single_source():
    from utils import clone_card_builder, content_enrich

    # 恒等（同一 frozenset 对象），不是值恰好的第二份拷贝
    assert clone_card_builder.MEDIA_ATTR_IDS is content_enrich.MEDIA_ATTR_IDS
    assert content_enrich.MEDIA_ATTR_IDS == frozenset({21841, 21845, 4195})


def test_b7_clone_normalize_still_skips_media():
    """常量化后行为不变：媒体属性跳过计数留痕。"""
    from utils.clone_card_builder import normalize_clone_attributes

    attrs = [
        {"id": 4180, "values": [{"dictionary_value_id": 0, "value": "чёрный"}]},
        {"id": 21841, "values": [{"dictionary_value_id": 0, "value": "x"}]},
    ]
    out, skipped = normalize_clone_attributes(attrs)
    assert [a["id"] for a in out] == [4180]
    assert skipped["media"] == 1


# ═══════════════════════════════════════════════════════════
# C7: LOCAL_ 错误码常量化
# ═══════════════════════════════════════════════════════════

def test_c7_constants_values():
    from utils import pipeline_error_codes as pec

    for name in (
        "LOCAL_PRICING_FAILED", "LOCAL_PRICE_GAP_BLOCKED",
        "LOCAL_TITLE_CATEGORY_MISMATCH", "LOCAL_CATEGORY_INVALID_REQUEST",
        "LOCAL_CATEGORY_RECATEGORIZE_FAILED", "LOCAL_REUPLOAD_FAILED",
        "LOCAL_UPLOAD_NO_TASK_ID", "LOCAL_STATUS_QUERY_FAILED",
    ):
        assert getattr(pec, name) == name


def test_c7_retry_loop_routing_built_from_constants():
    """路由表/集合以常量为键构建后，与既有字面量语义互通。"""
    from graphs.validation_retry_loop import (
        ERROR_NOTICE_MAP,
        FIX_TYPE_UNFIXABLE,
        REPAIR_STRATEGY,
    )
    from utils.pipeline_error_codes import (
        LOCAL_CATEGORY_INVALID_REQUEST,
        LOCAL_TITLE_CATEGORY_MISMATCH,
    )

    assert REPAIR_STRATEGY[LOCAL_TITLE_CATEGORY_MISMATCH] == "block_to_box"
    assert LOCAL_CATEGORY_INVALID_REQUEST in FIX_TYPE_UNFIXABLE
    assert LOCAL_CATEGORY_INVALID_REQUEST in ERROR_NOTICE_MAP
    assert "类目无效" in ERROR_NOTICE_MAP[LOCAL_CATEGORY_INVALID_REQUEST]


def test_c7_no_local_code_literals_in_consumers():
    """棘轮：两个消费文件不得再出现裸字符串 LOCAL_ 码（新码先入常量模块）。"""
    base = Path(__file__).resolve().parent.parent / "src" / "graphs"
    for rel in ("validation_retry_loop.py", "nodes/pricing_node.py"):
        src = (base / rel).read_text(encoding="utf-8")
        leaked = re.findall(r'["\']LOCAL_[A-Z_]+["\']', src)
        assert not leaked, f"{rel} 出现裸字符串 LOCAL_ 码: {leaked}（改用 utils/pipeline_error_codes.py 常量）"
