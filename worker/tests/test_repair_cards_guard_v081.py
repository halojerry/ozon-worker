"""v0.81.1 repair_cards 维度 0 值守卫回归（fix/import-exit-guards Fix 4）。

背景：scripts/repair_cards.py `_build_item` 此前 `attrs.get("depth", 0)` 直取
/v4 回显——回显缺维度/重量时把 0 发进全量 import UPDATE/CREATE，违反 Ozon
契约（MCP ProductAPI_ImportProductsV3：«Не пропускайте эти параметры в запросе
и не указывайте 0»）必被 missing_dimension 拒。现口径：任一维度/重量缺失或 0
→ 跳过该卡记原因，绝不发 0；vat 也改为「回显真实值、取不到省略键」。

运行（纯 mock，无需 PG、无网络）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_repair_cards_guard_v081.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "repair_cards.py"
    spec = importlib.util.spec_from_file_location("repair_cards", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cur(**attr_overrides) -> dict:
    """构造 _get_current 产物形状（info 来自 info/list，attrs 来自 /v4）。"""
    attrs = {
        "dimension_unit": "mm", "weight_unit": "g",
        "depth": 200, "width": 150, "height": 100, "weight": 500,
        "attributes": [
            {"id": 85, "values": [{"dictionary_value_id": 1, "value": "Нет бренда"}]},
        ],
    }
    attrs.update(attr_overrides)
    return {
        "info": {
            "id": 6443821910,
            "offer_id": "821565763841_0",
            "name": "Товар",
            "price": "254",
            "old_price": "305",
            "currency_code": "CNY",
            "vat": "0.1",
            "images": ["https://cos/1.jpg"],
            "primary_image": "https://cos/1.jpg",
        },
        "attrs": attrs,
    }


def test_build_item_ok_full_payload():
    mod = _load_module()
    item, reason = mod._build_item(_cur(), 17028959, 96513)
    assert item is not None and reason == ""
    assert (item["depth"], item["width"], item["height"], item["weight"]) == (200, 150, 100, 500)
    assert item["vat"] == "0.1"  # info/list 真实税率回显


def test_build_item_missing_depth_skips():
    """回显缺 depth（键缺失）→ 拒绝重建，绝不发 0。"""
    mod = _load_module()
    cur = _cur()
    del cur["attrs"]["depth"]
    item, reason = mod._build_item(cur, 1, 2)
    assert item is None and "depth" in reason


def test_build_item_zero_dimension_skips():
    """任一维度为 0 → 拒绝（Ozon missing_dimension 拒单根因）。"""
    mod = _load_module()
    for key in ("depth", "width", "height", "weight"):
        item, reason = mod._build_item(_cur(**{key: 0}), 1, 2)
        assert item is None, key
        assert key in reason and "0" in reason


def test_build_item_non_numeric_dimension_skips():
    """畸形维度值（字符串垃圾）→ 拒绝而非发 0/抛异常。"""
    mod = _load_module()
    item, reason = mod._build_item(_cur(weight="abc"), 1, 2)
    assert item is None and "weight" in reason


def test_build_item_vat_omitted_when_absent():
    """vat 取不到 → 省略键（绝不写死 "0"——全量替换会洗掉非零税率）。"""
    mod = _load_module()
    cur = _cur()
    cur["info"].pop("vat")
    item, _ = mod._build_item(cur, 1, 2)
    assert "vat" not in item
    cur["info"]["vat"] = ""
    item, _ = mod._build_item(cur, 1, 2)
    assert "vat" not in item


def test_recreate_skips_card_without_posting(monkeypatch):
    """_recreate 对缺维度卡整体跳过：不归档/不删除/不 import。"""
    mod = _load_module()
    cur = _cur(height=0)
    calls: list[str] = []
    monkeypatch.setattr(mod, "_post", lambda ep, body: calls.append(ep) or {})
    res = mod._recreate(cur, 17028959, 96513, dry=False)
    assert res.get("skipped") and "height" in res["skipped"]
    assert calls == [], "跳过路径不得发出任何 Ozon 请求"


def test_recreate_dry_run_guard_active():
    """--dry-run 同样受守卫：缺维度卡不产出 payload。"""
    mod = _load_module()
    res = mod._recreate(_cur(weight=0), 17028959, 96513, dry=True)
    assert res.get("skipped")
