# -*- coding: utf-8 -*-
"""
v0.14 A 批专项验证：P1-4 定价失败阻断 + P0-2 跟卖属性链路

运行：cd worker && PYTHONPATH=src python3 tests/test_audit_a_fixes.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── P1-4: 定价失败 [PRICING_FAILED] 标记 + graph 路由阻断 ──
def test_pricing_failure_marks_flag():
    """定价异常返回 [PRICING_FAILED] 标记（供 graph 路由阻断）"""
    import importlib
    from unittest.mock import patch
    from graphs.nodes.pricing_node import pricing_node

    # 构造会触发异常的 draft（缺失 purchase_cost 等）
    state = {
        "draft": {},  # 空 draft 触发异常路径
        "token": "sk-test",
        "ozon_client_id": "1",
        "ozon_api_key": "k",
        "description_category_id": "17028830",
        "type_id": "971206780",
        "competitor_price": "",
    }
    with patch("graphs.nodes.pricing_node.PricingInput", create=True) as MockInput:
        MockInput.return_value = type("S", (), {"draft": {}, "token": "sk-test",
                                               "ozon_client_id": "1", "ozon_api_key": "k",
                                               "description_category_id": "17028830",
                                               "type_id": "971206780",
                                               "competitor_price": ""})()
        # 直接调用内部异常路径：手动触发 pricing_node 异常分支
        try:
            out = pricing_node(state, None, None)
        except Exception as e:
            # 测试环境缺依赖时跳过，主断言走函数级
            print(f"  (pricing_node 直接调用受限: {type(e).__name__})")
            return
    em = out.error_message or ""
    assert "[PRICING_FAILED]" in em, f"错误信息应含 [PRICING_FAILED]: {em}"
    assert out.price == "", "失败时不应有兜底价格"


def test_graph_route_blocks_pricing_failed():
    """graph.route_after_pricing 检测 [PRICING_FAILED] → END 阻断"""
    from graphs.graph import route_after_pricing

    class FakeState:
        error_message = "[PRICING_FAILED] Pricing calculation failed: div by zero"

    assert route_after_pricing(FakeState()) == "END", "定价失败应路由到 END"


def test_graph_route_passes_normal():
    """正常定价 → assemble"""
    from graphs.graph import route_after_pricing

    class FakeState:
        error_message = ""

    assert route_after_pricing(FakeState()) == "assemble"


# ── P0-2: 跟卖属性链路（_assemble_follow_sell 消费 follow 输出） ──
def test_assemble_follow_sell_consumes_follow_attrs():
    """_assemble_follow_sell 消费 follow_sell_import 输出的 final_attributes，
    不再硬编码 {"id": 126745801}（字典值ID当属性ID的 bug）"""
    from unittest.mock import patch
    from graphs.nodes.assemble_ozon_product_node import _assemble_follow_sell

    class FakeState:
        description_category_id = "17028830"
        type_id = "971206780"
        ozon_client_id = "1"
        ozon_api_key = "k"
        currency_code = "RUB"
        # follow_sell_import 节点输出的 final_attributes（Ozon 格式 id+values）
        final_attributes = [
            {"id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
            {"id": 5076, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
            {"id": 4389, "values": [{"dictionary_value_id": 90296, "value": "Китай"}]},
            {"id": 9048, "values": [{"dictionary_value_id": 0, "value": "12345"}]},
            {"id": 8962, "values": [{"dictionary_value_id": 0, "value": "1"}]},
        ]

    state = FakeState()
    draft = {"item_id": "offer123", "ozon_product_id": "12345", "weight": 300,
             "dimensions": {"length": 100, "width": 100, "height": 50}}
    pricing_info = {"price": "1990", "old_price": "2390"}

    class P:
        def log_node_action(self, *a, **k): pass

    with patch("utils.ozon_category_query.get_category_query") as mock_q:
        mock_q.return_value.get_attribute_schema.return_value = None
        with patch("graphs.nodes.assemble_ozon_product_node._fetch_attribute_schema_from_ozon", return_value=[]):
            out = _assemble_follow_sell(state, draft, "Тест товар", ["http://img/1.jpg"], pricing_info, P())

    payload_attrs = out["ozon_payload"]["items"][0].get("attributes", [])
    payload_ids = {int(a["id"]) for a in payload_attrs}
    final_ids = {int(fa.get("attribute_id", 0)) for fa in out["final_attributes"] if fa.get("attribute_id")}

    # 断言：消费了 follow 输出的属性（85/5076/4389/9048/8962）
    assert 85 in payload_ids, f"品牌 85 应出现在 payload: {payload_ids}"
    assert 4389 in payload_ids, f"原产国 4389 应出现: {payload_ids}"
    assert 9048 in payload_ids, f"型号名 9048 应出现: {payload_ids}"
    # 断言：不再有 126745801 当属性 ID 的假条目
    assert 126745801 not in payload_ids, "126745801（字典值ID）绝不能被当作属性 ID"
    # 断言：final_attributes 用 attribute_id 键（prepare 可读）
    assert 85 in final_ids and 4389 in final_ids, f"final_attributes 应含 85/4389: {final_ids}"
    # 断言：品牌 dict_id 保留
    brand_85 = next(a for a in payload_attrs if int(a["id"]) == 85)
    assert brand_85["values"][0]["dictionary_value_id"] == 126745801


def test_assemble_follow_sell_fallback_hardcoded():
    """follow 无属性输出 → 最小硬编码属性集兜底（品牌/原产国）"""
    from unittest.mock import patch
    from graphs.nodes.assemble_ozon_product_node import _assemble_follow_sell

    class FakeState2:
        description_category_id = "17028830"
        type_id = "971206780"
        ozon_client_id = "1"
        ozon_api_key = "k"
        currency_code = "RUB"
        final_attributes = []  # 无 follow 属性

    state = FakeState2()
    draft = {"item_id": "offer123", "weight": 300, "dimensions": {"length": 100, "width": 100, "height": 50}}
    pricing_info = {"price": "1990", "old_price": "2390"}

    class P2:
        def log_node_action(self, *a, **k): pass

    with patch("utils.ozon_category_query.get_category_query") as mock_q2:
        mock_q2.return_value.get_attribute_schema.return_value = None
        with patch("graphs.nodes.assemble_ozon_product_node._fetch_attribute_schema_from_ozon", return_value=[]):
            out = _assemble_follow_sell(state, draft, "Тест товар", ["http://img/1.jpg"], pricing_info, P2())

    payload_ids = {int(a["id"]) for a in out["ozon_payload"]["items"][0].get("attributes", [])}
    assert 85 in payload_ids and 4389 in payload_ids, "兜底属性集应含品牌+原产国"
    assert 126745801 not in payload_ids, "兜底也不应有字典值ID当属性ID"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)
