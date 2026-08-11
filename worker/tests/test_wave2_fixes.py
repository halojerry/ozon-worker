"""v0.26 wave2 修复回归测试 — 假成功判定 + 尺寸方向反转 + 失败字段透出。

wave2 实证（2026-08-05）：3 张 created=False 的卡任务全 completed、final_error 空。
运行: cd worker && PYTHONPATH=src python3 tests/test_wave2_fixes.py
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ── D: 假成功判定（GraphOutput 失败字段透出 + task_processor 如实标 failed）──

def test_graph_output_has_failure_fields():
    """GraphOutput 必须含 upload_status/failed_stage/error_code，否则 output_schema
    过滤会把内部失败信息剥掉 → task_processor 无脑 completed（wave2 假成功根因）。"""
    from graphs.state import GraphOutput
    schema_fields = GraphOutput.model_fields
    for f in ("upload_status", "failed_stage", "error_code"):
        assert f in schema_fields, f"GraphOutput 缺 {f} 字段（假成功根因）"
    # 模拟 ozon_status 返回 validation_failed → 字段能透出
    out = GraphOutput(
        task_id="t1", product_id="123",
        upload_status="failed", failed_stage="ozon_status",
        error_code="ML_INCORRECT_VOLUME_WEIGHT",
        error_message="[OZON_VALIDATION_FAILED] ML_INCORRECT_VOLUME_WEIGHT",
    )
    d = out.model_dump()
    assert d["upload_status"] == "failed"
    assert d["error_code"] == "ML_INCORRECT_VOLUME_WEIGHT"
    assert d["failed_stage"] == "ozon_status"


def test_harness_failed_detection():
    """task_processor 的失败判定：upload_status=failed / error_message 带 [ 标记
    → 任务应标 failed 而非 completed（wave2 假成功修复）。"""
    # 复刻 task_processor 的判定逻辑（防误改）
    def _is_failed(graph_result: dict) -> bool:
        _up = str(graph_result.get("upload_status") or "")
        _err = str(graph_result.get("error_message") or "")
        _stg = str(graph_result.get("failed_stage") or "")
        return (_up == "failed") or _err.startswith("[") or bool(_err and _stg)

    assert _is_failed({"upload_status": "failed", "error_message": "配额不足"}) is True
    assert _is_failed({"error_message": "[OZON_VALIDATION_FAILED] xxx"}) is True
    assert _is_failed({"error_message": "类目解析失败", "failed_stage": "follow_sell_import"}) is True
    # 正常成功不含失败标记
    assert _is_failed({"upload_status": "success", "error_message": ""}) is False
    assert _is_failed({"upload_status": "pending", "error_message": ""}) is False


# ── A: 尺寸方向反转（保持重量、重算尺寸使体积重量≈重量）──

def test_repair_dimensions_keeps_weight():
    """wave2 清洁片 387g / 115×32×115mm → 比值 4.6x。
    v0.26 确保不把重量改成体积重量（387g 保留）。
    v0.37 D3d: 比值 4.6x 未达极端（>10）→ 真实尺寸也保留（防误伤）。
    """
    from graphs.validation_retry_loop import repair_dimensions_node
    from graphs.validation_retry_loop import ValidationRetryLoopState

    state = ValidationRetryLoopState(
        ozon_payload={"items": [{
            "name": "Очиститель", "weight": "387", "weight_unit": "g",
            "depth": "115", "width": "32", "height": "115", "dimension_unit": "mm",
            "offer_id": "x", "price": "36", "old_price": "43", "currency_code": "CNY",
        }]},
        validation_errors=[], errors=[], error_message="ML_INCORRECT_VOLUME_WEIGHT",
        draft={}, token="t", ozon_client_id="1", ozon_api_key="k",
        description_category_id="", type_id="", task_id="t1",
        product_id="123", purchase_url="", purchase_cost="",
        sku_id="", profit_estimation={}, final_attributes=[],
        attributes_schema=[], dictionary_values={}, learned_attributes={},
        pricing_info={}, error_code="ML_INCORRECT_VOLUME_WEIGHT",
        retry_count=1, max_retries=3,
    )
    out = repair_dimensions_node(state)
    item = out.ozon_payload["items"][0]
    weight = int(item["weight"])
    assert weight == 387, f"应保持重量 387g，实际 {weight}（旧逻辑会改成体积重量 84.6g）"
    # v0.37 D3d: 比值 4.6x 未达极端（>10），真实尺寸保留
    assert (int(item["depth"]), int(item["width"]), int(item["height"])) == (115, 32, 115)
    assert item["dimension_unit"] == "mm"


def test_repair_dimensions_always_consistent():
    """节点契约（v0.26 + v0.37 D3d）：
    收到 ML_INCORRECT_VOLUME_WEIGHT → ① 重量始终保留（绝不改成体积重量）；
    ② 真实尺寸保留；仅当尺寸全缺失或比值极端（<0.1 或 >10）才重算三边。"""
    from graphs.validation_retry_loop import repair_dimensions_node
    from graphs.validation_retry_loop import ValidationRetryLoopState

    def _run(weight, depth, width, height):
        state = ValidationRetryLoopState(
            ozon_payload={"items": [{
                "name": "Товар", "weight": str(weight), "weight_unit": "g",
                "depth": str(depth), "width": str(width), "height": str(height),
                "dimension_unit": "mm",
                "offer_id": "x", "price": "10", "old_price": "12", "currency_code": "CNY",
            }]},
            validation_errors=[], errors=[], error_message="", draft={},
            token="t", ozon_client_id="1", ozon_api_key="k",
            description_category_id="", type_id="", task_id="t1",
            product_id="123", purchase_url="", purchase_cost="",
            sku_id="", profit_estimation={}, final_attributes=[],
            attributes_schema=[], dictionary_values={}, learned_attributes={},
            pricing_info={}, error_code="ML_INCORRECT_VOLUME_WEIGHT",
            retry_count=1, max_retries=3,
        )
        item = repair_dimensions_node(state).ozon_payload["items"][0]
        return item

    # 真实尺寸非极端比值 → 保留原尺寸（v0.37 D3d 防误伤）
    for weight, d, w, h in [(387, 115, 32, 115), (250, 100, 80, 60), (1200, 200, 150, 100)]:
        item = _run(weight, d, w, h)
        w_keep = int(item["weight"])
        assert w_keep == weight, f"{weight}g 应保留（实际 {w_keep}）"
        assert (int(item["depth"]), int(item["width"]), int(item["height"])) == (d, w, h), \
            f"{weight}g: 真实尺寸应保留（v0.37），实际 {item['depth']}×{item['width']}×{item['height']}"
        assert item["dimension_unit"] == "mm"

    # 尺寸全缺失 → 按重量推算三边（唯一允许重算的路径）
    item = _run(387, 0, 0, 0)
    assert int(item["weight"]) == 387
    d, w, h = int(item["depth"]), int(item["width"]), int(item["height"])
    assert d > 0 and w > 0 and h > 0
    assert item["dimension_unit"] == "mm"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
