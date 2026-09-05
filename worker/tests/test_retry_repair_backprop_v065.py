# -*- coding: utf-8 -*-
"""v0.65 C1(N4): retry 子图修复结果回传主图测试。

背景：validation_retry_loop 子图内改 state.type_id/description_category_id/
final_attributes（重配类目/改属性），但 ValidationRetryLoopOutput/WrapperOutput
不含这些字段 → 主图 learning_record 读旧 dc/tp → 学习表按旧类目固化(Goodhart)。

修复：LoopOutput/WrapperOutput 增加 4 字段，final_result 透传子图修复后值，
wrapper 非空才覆盖主图（子图未重配时保持主图原值）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_retry_repair_backprop_v065.py -v
⚠️ 纯 mock/纯函数，无需 PG/GPU。
"""
import os
import sys
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.validation_retry_loop import final_result, ValidationRetryLoopState  # noqa: E402


def _retry_state(**over):
    from types import SimpleNamespace
    base = dict(
        ozon_payload={"items": [{"description_category_id": 17028653, "type_id": 92147}]},
        validation_errors=[], is_valid=True, retry_count=2,
        error_type="", error_message="", product_id="123", upload_status="success",
        moderation_status="approved",
        description_category_id="17028653", type_id="92147",
        final_attributes=[{"id": 4181, "values": [{"dictionary_value_id": 1, "value": "x"}]}],
        attributes_schema=[{"id": 4181, "name": "形状"}],
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_final_result_backprops_repaired_category():
    """子图重配类目后，final_result 输出带修复后 dc/tp/attrs（此前丢失）。"""
    state = _retry_state(
        description_category_id="99999", type_id="88888",  # 重配后的新类目
        final_attributes=[{"id": 111, "values": [{"dictionary_value_id": 7, "value": "y"}]}],
    )
    out = final_result(state)
    assert out.description_category_id == "99999", "修复后 dc 应回传"
    assert out.type_id == "88888", "修复后 tp 应回传"
    assert out.final_attributes[0]["id"] == 111, "修复后属性应回传"


def test_final_result_backprops_when_no_recategorize():
    """子图未重配类目（仅改属性）→ 仍回传当前值（= 传入值，不丢）。"""
    state = _retry_state()
    out = final_result(state)
    assert out.description_category_id == "17028653"
    assert out.type_id == "92147"


def test_wrapper_output_includes_repaired_fields():
    """wrapper 透传子图结果到主图 output（含修复后 dc/tp）。"""
    from graphs.nodes.validation_retry_wrapper_node import validation_retry_wrapper_node
    from graphs.state import ValidationRetryWrapperInput

    # 构造 input state（主图旧类目）
    inp = ValidationRetryWrapperInput(
        ozon_payload={}, validation_errors=[], errors=[], error_message="",
        draft={"title": "t"}, token="tok", ozon_client_id="c", ozon_api_key="k",
        description_category_id="11111", type_id="22222", task_id="task1",
        product_id="", purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
        final_attributes=[], attributes_schema=[], dictionary_values={},
        learned_attributes={}, pricing_info={}, retry_count=1,
    )
    runtime = type("R", (), {"context": None})()

    # mock 子图 invoke：返回重配后的类目
    fake_result = {
        "is_valid": True, "retry_count": 2, "ozon_payload": {},
        "validation_errors": [], "error_message": "", "product_id": "123",
        "upload_status": "success", "moderation_status": "approved",
        "description_category_id": "33333", "type_id": "44444",
        "final_attributes": [{"id": 9}], "attributes_schema": [{"id": 9}],
    }
    with mock.patch("graphs.nodes.validation_retry_wrapper_node.validation_retry_loop") as m_loop:
        m_loop.invoke.return_value = fake_result
        out = validation_retry_wrapper_node(inp, {}, runtime)

    assert out.description_category_id == "33333", "修复后 dc 应透传到主图"
    assert out.type_id == "44444"
    assert out.final_attributes == [{"id": 9}]


def test_wrapper_empty_repair_keeps_original_category():
    """子图未重配（返回空 dc/tp）→ wrapper 保持主图原值（不空串清空）。"""
    from graphs.nodes.validation_retry_wrapper_node import validation_retry_wrapper_node
    from graphs.state import ValidationRetryWrapperInput

    inp = ValidationRetryWrapperInput(
        ozon_payload={}, validation_errors=[], errors=[], error_message="",
        draft={"title": "t"}, token="tok", ozon_client_id="c", ozon_api_key="k",
        description_category_id="11111", type_id="22222", task_id="task1",
        product_id="", purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
        final_attributes=[], attributes_schema=[], dictionary_values={},
        learned_attributes={}, pricing_info={}, retry_count=1,
    )
    runtime = type("R", (), {"context": None})()
    fake_result = {
        "is_valid": True, "retry_count": 2, "ozon_payload": {},
        "validation_errors": [], "error_message": "", "product_id": "123",
        "upload_status": "success", "moderation_status": "approved",
        "description_category_id": "", "type_id": "",  # 子图未重配 → 空
        "final_attributes": [], "attributes_schema": [],
    }
    with mock.patch("graphs.nodes.validation_retry_wrapper_node.validation_retry_loop") as m_loop:
        m_loop.invoke.return_value = fake_result
        out = validation_retry_wrapper_node(inp, {}, runtime)

    assert out.description_category_id == "11111", "空修复应保持主图原 dc"
    assert out.type_id == "22222", "空修复应保持主图原 tp"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
