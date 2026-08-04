"""类目映射学习单测（v0.25 T1）— 数字 ID 优先查询 + 成功回写。

实现说明：复用 LocalDBManager 既有 category_mapping 读写（leaf 版已存在），
T1 增量 = source_category_id 支持 + follow 管线接入 L0。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.category_mapping_learn import lookup_mapping, record_mapping
from utils.local_db_manager import LocalDBManager


def _row(dc=17027918, tp=971311385, succ=3, conf=0.9):
    return {"description_category_id": dc, "type_id": tp,
            "success_count": succ, "confidence": conf}


def test_lookup_prefers_source_category_id():
    with mock.patch.object(LocalDBManager, "get_category_mapping_by_source_id",
                           return_value=[_row()]) as m_id, \
         mock.patch.object(LocalDBManager, "get_category_mapping_by_leaf",
                           return_value=[_row()]) as m_leaf:
        got = lookup_mapping(source_category_id=12345, leaf_name="女袜")
    assert got == {"dc": "17027918", "tp": "971311385", "confidence": 0.9}
    m_id.assert_called_once_with(12345)
    m_leaf.assert_not_called()  # 数字 ID 命中则不再查 leaf


def test_lookup_falls_back_to_leaf_name():
    with mock.patch.object(LocalDBManager, "get_category_mapping_by_source_id",
                           return_value=[]), \
         mock.patch.object(LocalDBManager, "get_category_mapping_by_leaf",
                           return_value=[_row(dc=123, tp=456)]) as m_leaf:
        got = lookup_mapping(source_category_id=12345, leaf_name="女袜")
    assert got["dc"] == "123"
    m_leaf.assert_called_once_with("女袜")


def test_lookup_skips_low_confidence():
    with mock.patch.object(LocalDBManager, "get_category_mapping_by_source_id",
                           return_value=[_row(succ=0, conf=0.5)]):
        assert lookup_mapping(source_category_id=12345, leaf_name="女袜") is None


def test_record_mapping_passes_source_category_id():
    with mock.patch.object(LocalDBManager, "add_category_mapping") as m_add:
        record_mapping(12345, "女袜", 17027918, 971311385, path_zh="袜子")
    m_add.assert_called_once()
    kwargs = m_add.call_args.kwargs
    assert kwargs["source_category_id"] == 12345
    assert kwargs["source_category_leaf"] == "女袜"
    assert kwargs["description_category_id"] == 17027918
    assert kwargs["type_id"] == 971311385


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
