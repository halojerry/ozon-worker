"""v0.26 P1-2 数字属性类型校验回归测试。

Ozon 实证（VALUE_MUST_BE_INTEGER ×10 / VALUE_MUST_BE_DECIMAL ×8）：
- 8205 保质期天数 → Integer（"12 месяцев" 被填文本）
- 11650 厂包装数量 → Integer（猫头鹰摆件）
- 4497 带包装重量 → Decimal（"1000 г"）
- 7444 长度 cm → Decimal

修复：按 schema type 提取数字转换；无法解析 → 跳过该属性。

运行（Docker 内）：
    docker run --rm -v /Volumes/os/dev/ozon-worker/worker:/app -w /app \
      -e PYTHONPATH=/app/src -e APP_WORKSPACE_PATH=/app -e GRSAI_API_KEY= \
      ozon-worker:latest python tests/test_numeric_attribute_types.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _call(attrs, schema):
    from graphs.nodes.prepare_ozon_upload_node import _convert_numeric_attrs
    return _convert_numeric_attrs(attrs, schema)


SCHEMA = [
    {"id": 8205, "name": "Срок годности в днях", "type": "Integer"},
    {"id": 11650, "name": "Количество заводских упаковок", "type": "Integer"},
    {"id": 4497, "name": "Вес с упаковкой, г", "type": "Decimal"},
    {"id": 7444, "name": "Длина, см", "type": "Decimal"},
    {"id": 10096, "name": "Цвет", "type": "String"},  # 非数字属性不受影响
]


def test_integer_conversion():
    """"12 месяцев" → 12、"5 шт" → 5（文本→Integer）。"""
    out = _call([
        {"attribute_id": 8205, "value": "12 месяцев"},
        {"attribute_id": 11650, "value": "5 шт"},
    ], SCHEMA)
    m = {a["attribute_id"]: a["value"] for a in out}
    assert m[8205] == "12"
    assert m[11650] == "5"


def test_decimal_conversion():
    """"1000 г" → "1000"、"20.5 см" → "20.5"（文本→Decimal）。"""
    out = _call([
        {"attribute_id": 4497, "value": "1000 г"},
        {"attribute_id": 7444, "value": "20.5 см"},
    ], SCHEMA)
    m = {a["attribute_id"]: a["value"] for a in out}
    assert m[4497] == "1000"
    assert m[7444] == "20.5"


def test_unparseable_skipped():
    """无法解析的数字属性 → 跳过（不提交文本给 Ozon）。"""
    out = _call([
        {"attribute_id": 8205, "value": "неизвестно"},
    ], SCHEMA)
    assert out == [], f"应跳过无法解析属性，实际 {out}"


def test_string_attr_untouched():
    """非数字属性（颜色等）不受影响。"""
    out = _call([
        {"attribute_id": 10096, "value": "Черный"},
    ], SCHEMA)
    assert len(out) == 1
    assert out[0]["value"] == "Черный"


def test_integer_rounds_decimal():
    """"3.7" Integer → 3（int 截断）。"""
    out = _call([
        {"attribute_id": 11650, "value": "3.7"},
    ], SCHEMA)
    assert out[0]["value"] == "3"


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
