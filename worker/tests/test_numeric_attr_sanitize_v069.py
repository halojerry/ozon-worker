"""
v0.69 T1.1 数值属性清洗回归测试（三店健康扫描：数值属性错误 42 例实证）

生产实证（三店健康扫描）：
- VALUE_MUST_BE_DECIMAL 11 / VALUE_MUST_BE_INTEGER 10 / VALUE_MAX_LIMIT 6 /
  VALUE_MIN_LIMIT 3 / error_attribute_values_empty 12。
- 典型：属性 8962 «Единиц в одном товаре» VALUE_MAX_LIMIT。
- 根因：worker 8962 显式写点全硬编码 "1"，真实污染通道是 prepare 主转换循环把
  1688「件数/数量」类属性按名映射到 8962 后**非空原样保留不校验**（可带单位
  如 "30包" 或超限数值如 20000）。

覆盖：
(3a) sanitize_numeric_attr_value 纯函数：剥单位/逗号小数/夹取/剔除/原样通过
(3b) prepare 主转换循环接线（集成）+ 8962 兜底段非空清洗
(3c) attributes_adjusted 追溯通道（GlobalState/PrepareOzonUploadOutput/GraphOutput）
(3d) retry REPAIR_STRATEGY/FIX_TYPE_ATTRIBUTES 映射 + repair_prepare_node payload 清洗

运行：cd worker && PYTHONPATH=src python -m pytest tests/test_numeric_attr_sanitize_v069.py -q
"""
import os
import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.attr_numeric_sanitize import (
    NUMERIC_ATTR_BOUNDS,
    is_numeric_attr_type,
    sanitize_numeric_attr_value,
)


# ==================== (3a) 纯函数 ====================

def test_30bao_to_30_integer():
    """① "30包" → "30"（Integer，剥中文单位；调整说明非空）"""
    val, reason = sanitize_numeric_attr_value(8962, "30包", "Integer")
    assert val == "30", f"'30包' 应清洗为 '30'，实际 {val!r}"
    assert reason, "有调整时 reason 不应为空"


def test_comma_decimal_1_5():
    """② RU locale 逗号小数 "1,5" → "1.5"（Decimal）"""
    val, reason = sanitize_numeric_attr_value(4497, "1,5", "Decimal")
    assert val == "1.5", f"'1,5' 应清洗为 '1.5'，实际 {val!r}"


def test_about_100ml_to_100():
    """③ "约100ml" → "100"（Number 数值型，剥前后缀单位）"""
    val, reason = sanitize_numeric_attr_value(7444, "约100ml", "Number")
    assert val == "100", f"'约100ml' 应清洗为 '100'，实际 {val!r}"


def test_pure_chinese_no_digit_dropped():
    """④ 纯中文无数字 "彩色包装" → (None, reason)（应剔除）"""
    val, reason = sanitize_numeric_attr_value(8962, "彩色包装", "Integer")
    assert val is None, f"纯中文无数值应剔除，实际 {val!r}"
    assert reason, "剔除时 reason 不应为空"


def test_8962_bounds_clamp():
    """⑤ 8962 越界夹取：0→下限 1；20000→上限 10000（区间来自 NUMERIC_ATTR_BOUNDS）"""
    assert NUMERIC_ATTR_BOUNDS.get(8962) == (1, 10000), "8962 合法区间应为 (1, 10000)"
    lo_val, lo_reason = sanitize_numeric_attr_value(8962, "0", "Integer")
    assert lo_val == "1", f"8962 值 0 应下夹取到 1，实际 {lo_val!r}"
    assert lo_reason and "10000" in lo_reason, f"夹取说明应含区间: {lo_reason}"
    hi_val, hi_reason = sanitize_numeric_attr_value(8962, "20000", "number")  # 小写类型也要认
    assert hi_val == "10000", f"8962 值 20000 应上夹取到 10000，实际 {hi_val!r}"


def test_8962_legal_value_passthrough():
    """⑥ 8962 合法值 30 原样通过（reason=None 表示无调整）"""
    val, reason = sanitize_numeric_attr_value(8962, "30", "Integer")
    assert val == "30" and reason is None, f"合法值应原样通过，实际 ({val!r}, {reason!r})"


def test_numeric_type_detection_case_insensitive():
    """数值类型判定大小写不敏感 + String 不算数值"""
    assert is_numeric_attr_type("Integer")
    assert is_numeric_attr_type("integer")
    assert is_numeric_attr_type("Decimal")
    assert is_numeric_attr_type("Number")
    assert is_numeric_attr_type("number")
    assert not is_numeric_attr_type("String")
    assert not is_numeric_attr_type("")
    assert not is_numeric_attr_type(None)


def test_negative_and_decimal_formatting():
    """负号保留 + Decimal 整数值不带 .0 尾巴"""
    val, _ = sanitize_numeric_attr_value(7444, "-5 см", "Decimal")
    assert val == "-5", f"负数应保留符号: {val!r}"
    val2, _ = sanitize_numeric_attr_value(7444, "2,0", "Decimal")
    assert val2 == "2", f"Decimal 整数值不应带 .0 尾巴: {val2!r}"


# ==================== (3b) prepare 接线（集成） ====================

def _draft():
    return {
        "item_id": "test8962",
        "title": "Набор салфеток",  # 俄语标题，避免标题翻译分支
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {},
        "sku_id": "test8962",
        "price": "1290",
        "original_price": "1490",
    }


def _make_state(final_attributes, schema):
    from graphs.state import PrepareOzonUploadInput
    return PrepareOzonUploadInput(
        draft=_draft(),
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=final_attributes,
        attributes_schema=schema,
        dictionary_values={},
        token="sk-test",
        original_images=_draft()["images"],
    )


def _payload_attr_map(output):
    attrs = []
    for item in (output.ozon_payload or {}).get("items", []):
        attrs.extend(item.get("attributes", []))
    return {int(a["id"]): a for a in attrs if isinstance(a, dict) and a.get("id")}


def _base_patches():
    """统一 mock：LLM 富文本/标题兜底/mxou chat（防网络）"""
    return [
        patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title", return_value="Товар для дома"),
        patch("utils.mxou_api.call_mxou_chat_api", return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm", return_value=""),
    ]


@contextmanager
def _patches(*extra):
    with ExitStack() as st:
        for p in _base_patches() + list(extra):
            st.enter_context(p)
        yield


def test_main_loop_sanitizes_quantity_attr():
    """⑦ 主转换循环集成：1688「件数」属性（schema type=Number，此前
    _convert_numeric_attrs 大小写敏感白名单外的漏网通道）映射到 8962 后被清洗
    "30包"→"30"，且 attributes_adjusted 记录调整。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node

    schema = [{"id": 8962, "name": "Единиц в одном товаре", "dictionary_id": 0,
               "type": "Number", "is_required": False}]
    final_attributes = [{"attribute_id": 8962, "value": "30包", "dictionary_value_id": 0}]
    state = _make_state(final_attributes, schema)

    with _patches():
        output = prepare_ozon_upload_node(state, None, None)

    attr_map = _payload_attr_map(output)
    assert 8962 in attr_map, "8962 不应被剔除"
    got = attr_map[8962]["values"][0]["value"]
    assert got == "30", f"8962 应清洗为 '30'，实际 {got!r}"
    assert output.attributes_adjusted, "attributes_adjusted 应记录本次调整"
    rec = next(r for r in output.attributes_adjusted if int(r.get("attr_id") or 0) == 8962)
    assert rec.get("before") == "30包" and rec.get("after") == "30", f"调整记录前后值不对: {rec}"


def test_main_loop_drops_unparseable_numeric_attr():
    """⑦b 主转换循环：数值型属性纯中文无数字 → 剔除（防 VALUE_MUST_BE_* 整单被拒）"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node

    schema = [{"id": 8962, "name": "Единиц в одном товаре", "dictionary_id": 0,
               "type": "Integer", "is_required": False}]
    final_attributes = [{"attribute_id": 8962, "value": "彩色包装", "dictionary_value_id": 0}]
    state = _make_state(final_attributes, schema)

    with _patches():
        output = prepare_ozon_upload_node(state, None, None)

    attr_map = _payload_attr_map(output)
    # 剔除后走 8962 兜底段 → 兜底 "1"（而非 "彩色包装" 原样上传）
    got = attr_map[8962]["values"][0]["value"] if 8962 in attr_map else None
    assert got == "1", f"8962 无值应走兜底 '1'，实际 {got!r}"


def test_8962_fallback_cleans_existing_value():
    """3b-2 8962 兜底段：schema 无 type 信息时主循环漏网，兜底段仍清洗
    已有非空值 "20000 шт" → "10000"（夹取上限）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node

    schema = [{"id": 8962, "name": "Единиц в одном товаре", "dictionary_id": 0,
               "is_required": False}]  # 无 type → 主循环数值表不认
    final_attributes = [{"attribute_id": 8962, "value": "20000 шт", "dictionary_value_id": 0}]
    state = _make_state(final_attributes, schema)

    with _patches():
        output = prepare_ozon_upload_node(state, None, None)

    attr_map = _payload_attr_map(output)
    got = attr_map[8962]["values"][0]["value"]
    assert got == "10000", f"8962 兜底段应夹取 20000→10000，实际 {got!r}"
    assert output.attributes_adjusted, "兜底段清洗也应记录 attributes_adjusted"


# ==================== (3c) 追溯通道 ====================

def test_attributes_adjusted_channel_registered():
    """⑨ attributes_adjusted 通道在 GlobalState / PrepareOzonUploadOutput /
    GraphOutput 三处注册（output_schema 按名过滤透传的前提）。"""
    from graphs.state import GlobalState, GraphOutput, PrepareOzonUploadOutput
    assert "attributes_adjusted" in GlobalState.model_fields
    assert "attributes_adjusted" in PrepareOzonUploadOutput.model_fields
    assert "attributes_adjusted" in GraphOutput.model_fields


def test_graph_output_transparent():
    """GraphOutput 可携带 attributes_adjusted 记录（列表结构）"""
    from graphs.state import GraphOutput
    out = GraphOutput(
        task_id="t-8962",
        attributes_adjusted=[{
            "attr_id": 8962, "attr_name": "Единиц в одном товаре",
            "before": "30包", "after": "30", "reason": "剥单位",
        }],
    )
    assert len(out.attributes_adjusted) == 1
    assert out.attributes_adjusted[0]["attr_id"] == 8962


# ==================== (3d) retry 映射 ====================

def test_repair_strategy_and_fix_type_mapping():
    """⑧ VALUE_MAX_LIMIT/VALUE_MIN_LIMIT → repair_prepare + FIX_TYPE_ATTRIBUTES
    （靶向 attributes update）。"""
    from graphs.validation_retry_loop import FIX_TYPE_ATTRIBUTES, REPAIR_STRATEGY
    assert REPAIR_STRATEGY.get("VALUE_MAX_LIMIT") == "repair_prepare"
    assert REPAIR_STRATEGY.get("VALUE_MIN_LIMIT") == "repair_prepare"
    assert "VALUE_MAX_LIMIT" in FIX_TYPE_ATTRIBUTES
    assert "VALUE_MIN_LIMIT" in FIX_TYPE_ATTRIBUTES


def test_repair_prepare_node_sanitizes_numeric_payload():
    """repair_prepare 不重跑 prepare 主转换循环（就地改 payload）→ 数值清洗必须
    内嵌 repair_prepare_node：payload 8962=20000 → 10000。"""
    from graphs.validation_retry_loop import ValidationRetryLoopState, repair_prepare_node

    state = ValidationRetryLoopState(
        ozon_payload={"items": [{
            "offer_id": "offer1",
            "attributes": [
                {"id": 8962, "values": [{"dictionary_value_id": 0, "value": "20000"}]},
                {"id": 9048, "values": [{"dictionary_value_id": 0, "value": "item1~abc"}]},
            ],
        }]},
        attributes_schema=[{"id": 8962, "name": "Единиц в одном товаре",
                            "dictionary_id": 0, "type": "Integer"}],
        error_code="VALUE_MAX_LIMIT",
        attribute_id=0,  # 0=跳过字典 post-fill（本用例只验证数值清洗，不触 PG）
        retry_count=1,
    )
    out = repair_prepare_node(state)
    attrs = out.ozon_payload["items"][0]["attributes"]
    got = next(a for a in attrs if int(a.get("id") or 0) == 8962)
    assert got["values"][0]["value"] == "10000", f"repair_prepare 应夹取 8962→10000: {got}"
    # 非数值属性不受影响
    m9048 = next(a for a in attrs if int(a.get("id") or 0) == 9048)
    assert m9048["values"][0]["value"] == "item1~abc"


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
