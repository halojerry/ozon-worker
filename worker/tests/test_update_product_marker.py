# -*- coding: utf-8 -*-
"""
T8 编辑更新模式标记测试 — prepare_ozon_upload_node 消费 extensions.update_product_id

契约（T7 注入）：
- draft_service.submit_draft(update_product_id=...) 在 graph_payload.envelope.extensions 注入
  update_product_id（必）+ update_offer_id（可选）
- prepare 节点纯读 extensions：update_product_id 非空 → item 加 product_id（Ozon UPDATE 模式）
- 无 marker → 行为与旧路径完全一致（item 无 product_id 键，防双卡）
- update_product_id 优先于 follow_sell 的 product_id（用户编辑的目标商品）
- 绝不持久化 marker（无写 side effect）

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_update_product_marker.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import patch

from graphs.state import PrepareOzonUploadInput
from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node


def _make_state(extensions=None, product_id=None, draft_extra=None):
    draft = {
        "item_id": "test001",
        "title": "宠物玩具 猫抓板",
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {"商品颜色": "蓝色"},
        "sku_id": "test001",
        "price": "1990",
        "original_price": "2390",
    }
    if draft_extra:
        draft.update(draft_extra)
    return PrepareOzonUploadInput(
        draft=draft,
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        extensions=extensions or {},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=[
            {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "llm"},
        ],
        attributes_schema=[{"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True}],
        dictionary_values={},
        token="sk-test",
        original_images=draft["images"],
        product_id=product_id,
    )


def _run(state):
    # v0.63.1: MXOU 401/403 已 fatal（全链路），这些用例不关心 LLM 结果，
    # 必须 mock 掉 chat 调用（返回空 → 走正则/兜底路径），不能靠吞异常隐式通过。
    with patch("utils.mxou_api.call_mxou_chat_api", return_value=""), \
         patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
               return_value="Тестовый товар для кошек"), \
         patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title",
               return_value="Товар для дома"):
        output = prepare_ozon_upload_node(state, None, None)
    return output


def _first_item(output):
    items = (output.ozon_payload or {}).get("items", [])
    assert items, "ozon_payload.items 不应为空"
    return items[0]


# ── 1. update marker → product_id ──
def test_update_marker_adds_product_id():
    """extensions 含 update_product_id=12345 → item 有 product_id=12345（UPDATE 模式）"""
    state = _make_state(extensions={"update_product_id": 12345})
    item = _first_item(_run(state))
    assert item.get("product_id") == 12345, f"update 模式应注入 product_id: {item.get('product_id')}"


def test_update_marker_string_product_id():
    """update_product_id 以字符串提供 → 仍转 int 注入 product_id"""
    state = _make_state(extensions={"update_product_id": "12345"})
    item = _first_item(_run(state))
    assert item.get("product_id") == 12345, "字符串 update_product_id 应转为 int"


# ── 2. update_offer_id 覆盖 ──
def test_update_marker_offer_id_override():
    """update_offer_id 提供 → item.offer_id 用它覆盖原 offer_id"""
    state = _make_state(extensions={"update_product_id": 12345, "update_offer_id": "edited_offer"})
    item = _first_item(_run(state))
    assert item.get("offer_id") == "edited_offer", f"offer_id 应被 update_offer_id 覆盖: {item.get('offer_id')}"


def test_update_marker_offer_id_default():
    """update_offer_id 未提供 → offer_id 保留原 sku_id"""
    state = _make_state(extensions={"update_product_id": 12345})
    item = _first_item(_run(state))
    assert item.get("offer_id") == "test001", f"未提供 update_offer_id 时应保留 sku_id: {item.get('offer_id')}"


# ── 3. 无 marker → 无 product_id（防双卡关键断言）──
def test_no_marker_no_product_id():
    """无 update_product_id → item 无 product_id 键（行为完全不变）"""
    state = _make_state(extensions={})
    item = _first_item(_run(state))
    assert "product_id" not in item, "无 marker 时绝不能注入 product_id（防双卡）"


def test_no_marker_offer_id_unchanged():
    """无 marker → offer_id 仍是 sku_id"""
    state = _make_state(extensions={})
    item = _first_item(_run(state))
    assert item.get("offer_id") == "test001"


# ── 4. update 优先于 follow_sell ──
def test_update_overrides_follow_sell():
    """follow_sell + update_product_id 同时存在 → product_id 用 update 值（用户编辑目标商品优先）"""
    state = _make_state(
        extensions={"update_product_id": 12345},
        product_id="999999999",  # follow_sell 的 product_id
        draft_extra={"ozon_product_id": "3852000144"},  # is_follow_sell 触发
    )
    item = _first_item(_run(state))
    assert item.get("product_id") == 12345, f"update 模式应覆盖 follow_sell product_id: {item.get('product_id')}"


def test_update_overrides_follow_sell_offer_id():
    """follow_sell + update（带 update_offer_id）→ offer_id 用 update_offer_id"""
    state = _make_state(
        extensions={"update_product_id": 12345, "update_offer_id": "edited_offer"},
        product_id="999999999",
        draft_extra={"ozon_product_id": "3852000144"},
    )
    item = _first_item(_run(state))
    assert item.get("offer_id") == "edited_offer", "update_offer_id 应优先于 follow 竞品 offer_id"


# ── 5. 不持久化 marker ──
def test_update_marker_not_in_persisted():
    """prepare 纯读 extensions：state 不被修改 + payload item 无 marker 键"""
    state = _make_state(extensions={"update_product_id": 12345, "update_offer_id": "edited_offer"})
    orig_extensions = dict(state.extensions)
    output = _run(state)
    # state 不被修改（无写 side effect）
    assert state.extensions == orig_extensions, "prepare 不应修改 state.extensions"
    # payload item 只有 product_id 注入，无 update_product_id/update_offer_id marker 残留
    item = _first_item(output)
    assert "update_product_id" not in item, "marker 不应持久化到 item"
    assert "update_offer_id" not in item, "update_offer_id 不应持久化到 item"


def test_update_marker_draft_untouched():
    """marker 不进入 draft：draft 无 update_product_id 字段"""
    state = _make_state(extensions={"update_product_id": 12345})
    _run(state)
    assert "update_product_id" not in state.draft, "update marker 不应写入 draft"
    assert "update_offer_id" not in state.draft, "update_offer_id 不应写入 draft"


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
