"""尺码表导入解析单测（v0.24 F1a）— CSV → (table_type, input_value, ru_size)。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from import_size_tables import parse_csv_rows


def test_parse_male_table():
    rows = parse_csv_rows(os.path.join(os.environ["APP_WORKSPACE_PATH"], "assets", "男性服装尺码表.csv"), "male")
    int_map = {r["input_value"]: r["ru_size"] for r in rows if r["source_col"] == "INT"}
    assert int_map.get("M") == "48"
    assert int_map.get("L") == "50"
    assert int_map.get("XS") == "44"
    # RU 自身列也入库：输入 48 → RU 48
    ru_map = {r["input_value"]: r["ru_size"] for r in rows if r["source_col"] == "RU"}
    assert ru_map.get("48") == "48"


def test_parse_shoes_table():
    rows = parse_csv_rows(os.path.join(os.environ["APP_WORKSPACE_PATH"], "assets", "鞋子尺码对应表.csv"), "shoes")
    cn_map = {r["input_value"]: r["ru_size"] for r in rows if r["source_col"] == "CN"}
    assert cn_map.get("38") == "37"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
