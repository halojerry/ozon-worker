"""fix/category-bridge-v1 回归：UPDATE 项 dc/tp 必填（官方 required）。

2026-09-24 事故（终版根因）：follow 毒 dc/tp 被三道闸正确拦截 → hand 降级
api import-by-sku 复制成功（卡已建）→ state dc/tp 仍空 → prepare UPDATE 项
按「空=省略」组装 → /v3/product/import items[].required=[description_category_id,
price, type_id] 对 UPDATE 同样必填 → proto 默认 0 → 网关 400
`invalid Request.Items.TypeId: value must be greater than 0` → 误入 LLM 修复
循环 → 终态假 failed（卡真在架）。

修复两层（本文件锁定）：
1. 复制完成后立即 info/list 反查复制卡真实 dc/tp 回填（官方复制带出）；
2. UPDATE 硬闸：反查+信封+仲裁全空 → 显式 failed 拒绝空类目进 prepare。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from graphs.nodes import follow_sell_import_node as mod  # noqa: E402
from graphs.nodes.follow_sell_import_node import follow_sell_import_node  # noqa: E402


class _FakeState:
    """最小 state 桩（follow_sell_import_node 消费面）。"""

    def __init__(self, envelope):
        self.envelope = envelope
        self.ozon_client_id = "1"
        self.ozon_api_key = "k"
        self.token = "t"
        self.user_id = "test"
        self.currency_code = "CNY"


def _env(widget_dc="14762", widget_tp="14762"):
    """2026-09-24 事故形态信封：前台 Widget ID 假冒 dc/tp（同值双塞）。"""
    return {
        "draft": {
            "ozon_product_id": "3465392291",
            "title": "Storage Case",
            "ozon_title": "Storage Case, 14 х 10 х 28 cm",
            "images": ["https://cdn.ozon.ru/img1.jpg"],
            "ozon_category": {
                "description_category_id": widget_dc,
                "type_id": widget_tp,
                "category_path": "House & Garden > Storage > Organizers and dividers > Cases",
            },
            "competitor_price": "137.02",
            "purchase_cost": 4.0,
            "purchase_url": "https://detail.1688.com/offer/1030281440854.html",
            "currency": "CNY",
            "weight": 200,
            "dimensions": {"length": 140, "width": 100, "height": 280},
            "item_id": "1030281440854",
        },
        "extensions": {"follow_sell": True, "follow_type": "hand"},
    }


def _patch(monkeypatch, info_list_result=None, info_list_raises=False,
           schema_ok=False):
    """打桩 ozon_post / 类目解析。schema_ok=False 复现毒 ID 被 schema API 400 拒。"""

    def fake_post(client_id, api_key, endpoint, body=None, timeout=60, **kw):
        if "description-category/attribute" in endpoint:
            if schema_ok:
                return {"result": [{"id": 8229, "name": "Тип", "is_collection": False}]}
            raise RuntimeError("HTTP 400: category is not found")  # 毒 ID 被拒
        if "import-by-sku" in endpoint:
            return {"result": {"task_id": "777"}}
        if "import/info" in endpoint:
            return {"result": {"items": [{"product_id": 6443818882, "status": "imported"}]}}
        if "product/info/list" in endpoint:
            if info_list_raises:
                raise RuntimeError("connection reset")
            return info_list_result if info_list_result is not None else {}
        return {}

    monkeypatch.setattr(mod, "ozon_post", fake_post, raising=True)
    monkeypatch.setattr(mod, "_resolve_category_by_id",
                        lambda *a, **k: ("", ""), raising=True)
    monkeypatch.setattr(mod, "_gated_category_arbitration",
                        lambda *a, **k: ("", ""), raising=True)
    # schema 自校验隔离：test_follow_sell_v5.py 的存量用例裸赋值不恢复（残留
    # lambda:True 会让毒 ID 被信任改道）——本文件显式按场景打桩，不依赖模块初态
    monkeypatch.setattr(mod, "_verify_category_schema",
                        lambda *a, **k: schema_ok, raising=True)
    monkeypatch.setattr(mod, "_resolve_category",
                        lambda *a, **k: (None, None), raising=True)
    # 学习表隔离：全量套件里其他测试会往 category_mapping 写行，真查会命中改路径
    import utils.category_mapping_learn as _cml
    monkeypatch.setattr(_cml, "lookup_mapping", lambda **k: None, raising=True)
    monkeypatch.setattr(_cml, "resolve_1688_source_category_id",
                        lambda *a, **k: "", raising=True)


def test_update_gate_hard_fails_when_backfill_unavailable(monkeypatch):
    """反查不可用 + 信封毒 ID 被拦 + 仲裁空 → 显式 failed，绝不空类目进 prepare。"""
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _patch(monkeypatch, info_list_result={}, info_list_raises=False)
    result = follow_sell_import_node(_FakeState(_env()))
    assert result.get("failed_stage") == "follow_sell_import"
    assert "UPDATE 项缺类目" in (result.get("error_message") or "")
    # 复制拿到的 product_id 仍透传（卡已建成的事实不丢，可人工恢复）
    assert result.get("product_id") == "6443818882"


def test_update_gate_backfills_copied_card_category(monkeypatch):
    """反查可用（官方复制带出真实类目）→ 回填 dc/tp，不再缺类目。"""
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _patch(monkeypatch, info_list_result={
        "items": [{"id": 6443818882, "product_id": 6443818882,
                   "description_category_id": 17027933, "type_id": 970742618}]})
    result = follow_sell_import_node(_FakeState(_env()))
    assert not result.get("error_message"), f"不应失败: {result.get('error_message')}"
    assert result.get("description_category_id") == "17027933"
    assert result.get("type_id") == "970742618"
    assert result.get("category_missing") is not True


def test_widget_id_never_reaches_output(monkeypatch):
    """毒 Widget ID（14762 同值双塞）绝不进 Output——信封值必须被防线洗掉。"""
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _patch(monkeypatch, info_list_result={
        "items": [{"id": 6443818882, "product_id": 6443818882,
                   "description_category_id": 17027933, "type_id": 970742618}]})
    result = follow_sell_import_node(_FakeState(_env()))
    assert str(result.get("description_category_id")) != "14762"
    assert str(result.get("type_id")) != "14762"


def test_update_gate_tolerates_backfill_exception(monkeypatch):
    """反查抛异常（网络故障）→ 硬闸宽 except 兜住 → 显式 failed 不裸崩。"""
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _patch(monkeypatch, info_list_raises=True)
    result = follow_sell_import_node(_FakeState(_env()))
    assert result.get("failed_stage") == "follow_sell_import"
    assert "UPDATE 项缺类目" in (result.get("error_message") or "")
