"""feat/follow-copy-attrs-v1 (A6) 回归：复制卡原带特征保全。

取证（2026-09-24/25，本地真链路）：import-by-sku 复制竞品整卡（含已过审特征表），
但 /v3/product/import 是「完全更新」语义（官方：完全更新特征用 import）——
后续稀疏 payload 把复制卡带来的特征全量洗掉（实测 follow 按摩器卡 6447343398:
14/34=41%，竞品页面明明有完整特征表）。

修复两层（本文件锁定）：
1. follow_sell_import_node 复制完成点 /v4 读回复制卡原带特征表 → state；
2. prepare merge_copied_card_attributes 合并回 payload（我方已填我方权威，
   缺口照抄竞品；防洗卡）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from graphs.nodes import follow_sell_import_node as mod
from graphs.nodes.follow_sell_import_node import follow_sell_import_node
from graphs.nodes.prepare_ozon_upload_node import merge_copied_card_attributes


class _FakeState:
    def __init__(self, envelope):
        self.envelope = envelope
        self.ozon_client_id = "1"
        self.ozon_api_key = "k"
        self.token = "t"
        self.user_id = "test"
        self.currency_code = "CNY"


def _env():
    return {
        "draft": {
            "ozon_product_id": "3465392291",
            "title": "Storage Case",
            "ozon_title": "Storage Case, 14 х 10 х 28 cm",
            "images": ["https://cdn.ozon.ru/img1.jpg"],
            "ozon_category": {
                "description_category_id": "14762",
                "type_id": "14762",
                "category_path": "House & Garden > Storage > Cases",
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


_COPIED_V4 = {
    "result": {"items": [{
        "id": 6443818882,
        "attributes": [
            {"complex_id": 0, "id": 21550, "values": [{"dictionary_value_id": 99, "value": "Квадрат"}]},
            {"complex_id": 0, "id": 21832, "values": [{"dictionary_value_id": 98, "value": "Пластик"}]},
            {"id": 0, "values": []},  # 非法行应被滤
        ],
    }]},
}


def _patch(monkeypatch, v4_result=None, v4_raises=False):
    def fake_post(client_id, api_key, endpoint, body=None, timeout=60, **kw):
        if "description-category/attribute" in endpoint:
            raise RuntimeError("HTTP 400: category is not found")  # 毒 ID 被拒
        if "import-by-sku" in endpoint:
            return {"result": {"task_id": "777"}}
        if "import/info" in endpoint:
            return {"result": {"items": [{"product_id": 6443818882, "status": "imported"}]}}
        if "product/info/list" in endpoint:
            return {"items": [{"id": 6443818882, "product_id": 6443818882,
                               "description_category_id": 17027933,
                               "type_id": 970742618}]}
        if "product/info/attributes" in endpoint:
            if v4_raises:
                raise RuntimeError("connection reset")
            return v4_result if v4_result is not None else {"result": {"items": []}}
        return {}
    monkeypatch.setattr(mod, "ozon_post", fake_post, raising=True)
    # 路径隔离（raising=False：无论存量测试裸赋值是否残留都打上自己的桩——
    # test_follow_sell_v5 系裸赋值不恢复是已知前科，见 v081 注释）
    monkeypatch.setattr(mod, "_verify_category_schema",
                        lambda *a, **k: False, raising=False)  # 毒 ID 必须被拒
    monkeypatch.setattr(mod, "_resolve_category_by_id",
                        lambda *a, **k: ("", ""), raising=False)
    monkeypatch.setattr(mod, "_gated_category_arbitration",
                        lambda *a, **k: ("", ""), raising=False)
    monkeypatch.setattr(mod, "_resolve_category",
                        lambda *a, **k: (None, None), raising=False)
    import graphs.nodes.follow_sell_import_node as _m
    # 学习表隔离（CI 无 PG）
    import utils.category_mapping_learn as _cml
    monkeypatch.setattr(_cml, "lookup_mapping", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(_cml, "resolve_1688_source_category_id",
                        lambda *a, **k: "", raising=True)


# ── ① follow 节点：复制完成点 /v4 读回原表 ─────────────────────

def test_node_reads_back_copied_attributes(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _patch(monkeypatch, v4_result=_COPIED_V4)
    result = follow_sell_import_node(_FakeState(_env()))
    assert not result.get("error_message"), f"不应失败: {result.get('error_message')}"
    got = result.get("follow_copied_attributes") or []
    ids = {a["id"] for a in got}
    assert ids == {21550, 21832}          # 非法行（id=0）被滤
    assert got[0]["values"][0]["dictionary_value_id"] == 99  # 原值照存


def test_node_tolerates_v4_failure(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _patch(monkeypatch, v4_raises=True)
    result = follow_sell_import_node(_FakeState(_env()))
    # 反查失败不阻断主流程（UPDATE 硬闸仍由类目反查兜底）
    assert result.get("description_category_id") == "17027933"
    assert result.get("follow_copied_attributes") == []


# ── ② prepare 合并：防 import 洗卡 ────────────────────────────

def test_merge_fills_gaps_verbatim():
    copied = [
        {"complex_id": 0, "id": 21550, "values": [{"dictionary_value_id": 99, "value": "Квадрат"}]},
        {"complex_id": 0, "id": 6788, "values": [{"dictionary_value_id": 55, "value": "1500"}]},
    ]
    items = [{"product_id": "6443818882",
              "attributes": [{"id": 85, "values": [{"dictionary_value_id": 1, "value": "Нет бренда"}]}]}]
    out = merge_copied_card_attributes(items, copied)
    got = {a["id"]: a for a in out[0]["attributes"]}
    assert 21550 in got and 6788 in got
    assert got[21550]["values"][0]["dictionary_value_id"] == 99  # 原值原样（含 dict_id）
    assert got[85]["values"][0]["value"] == "Нет бренда"          # 我方已填不动


def test_merge_ours_win_ids_never_overwritten():
    copied = [{"complex_id": 0, "id": 9048, "values": [{"dictionary_value_id": 0, "value": "竞品型号"}]}]
    items = [{"product_id": "6443818882",
              "attributes": [{"id": 9048, "values": [{"dictionary_value_id": 0, "value": "我们的并卡hash"}]}]}]
    out = merge_copied_card_attributes(items, copied)
    assert len(out[0]["attributes"]) == 1
    assert out[0]["attributes"][0]["values"][0]["value"] == "我们的并卡hash"


def test_merge_skips_create_items_and_empty():
    copied = [{"complex_id": 0, "id": 21550, "values": [{"value": "x"}]}]
    # CREATE 项（无 product_id）不动
    items = [{"attributes": []}]
    assert merge_copied_card_attributes(items, copied) == items
    # 空 copied → 原样返回
    items2 = [{"product_id": "1", "attributes": []}]
    assert merge_copied_card_attributes(items2, []) == items2


# ── ③ channel 纪律：三处声明 ──────────────────────────────────

def test_channel_declarations():
    from graphs.state import FollowSellImportOutput, GlobalState, PrepareOzonUploadInput
    for model in (GlobalState, FollowSellImportOutput, PrepareOzonUploadInput):
        assert "follow_copied_attributes" in model.model_fields, f"{model.__name__} 缺字段声明"
    out = FollowSellImportOutput()
    assert out.follow_copied_attributes == []
    inp = PrepareOzonUploadInput(follow_copied_attributes=[{"id": 1, "values": []}])
    assert len(inp.follow_copied_attributes) == 1

# ── ④ UPDATE 形态就地读回 ────────────────────────────────────

def test_read_existing_card_attributes(monkeypatch):
    from graphs.nodes.prepare_ozon_upload_node import _read_existing_card_attributes
    import graphs.nodes.prepare_ozon_upload_node as pmod
    import utils.ozon_client as oc
    monkeypatch.setattr(
        oc, "ozon_post",
        lambda cid, key, ep, body=None, timeout=15, **kw: _COPIED_V4, raising=True)
    st = SimpleNamespace(ozon_client_id="1", ozon_api_key="k")
    got = _read_existing_card_attributes(st, "6443818882")
    assert {a["id"] for a in got} == {21550, 21832}
    # 404/异常 → 空表不阻断
    monkeypatch.setattr(
        oc, "ozon_post",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")), raising=True)
    assert _read_existing_card_attributes(st, "1") == []

def test_upload_merge_wiring_import(monkeypatch):
    """A6 upload 侧接线冒烟：merge_copied_card_attributes 可从 upload 节点 import。"""
    import graphs.nodes.ozon_upload_node as umod
    assert hasattr(umod, "ozon_upload_node")
    from graphs.nodes.prepare_ozon_upload_node import merge_copied_card_attributes as m
    out = m([{"product_id": "1", "attributes": []}],
            [{"complex_id": 0, "id": 21550, "values": [{"value": "x"}]}])
    assert out[0]["attributes"][0]["id"] == 21550
