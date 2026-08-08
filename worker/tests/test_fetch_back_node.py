#!/usr/bin/env python3
"""fetch_back_node 单测（PR-0）— 回读 diff / 学习门 / 遥测。

运行：
    cd worker && PYTHONPATH=src python3 tests/test_fetch_back_node.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graphs.nodes.fetch_back_node import _normalize_stored_attrs, _contains_cjk


def test_normalize_stored_attrs_basic():
    """/v4 返回的 attributes[] 归一为 {id: {dict_id, value, values}}。"""
    stored = [{"id": 8229, "values": [{"dictionary_value_id": 99385, "value": "杀虫剂"}]},
              {"id": 9048, "values": [{"dictionary_value_id": 0, "value": "A123"}]}]
    out = _normalize_stored_attrs({"attributes": stored})
    assert out[8229]["dictionary_value_id"] == 99385
    assert out[8229]["value"] == "杀虫剂"
    assert out[9048]["dictionary_value_id"] == 0
    assert out[9048]["value"] == "A123"


def test_normalize_skips_bad_ids():
    """无 id / 非法 id 的属性跳过。"""
    stored = [{"values": [{"dictionary_value_id": 1, "value": "x"}]},
              {"id": "abc", "values": [{"dictionary_value_id": 2, "value": "y"}]},
              {"id": 0, "values": []}]
    assert _normalize_stored_attrs({"attributes": stored}) == {}


def test_contains_cjk():
    assert _contains_cjk("杀虫剂") is True
    assert _contains_cjk("Китай") is False
    assert _contains_cjk("") is False


def test_fetch_back_node_diff_dict_drift():
    """dict_id 漂移被检出：sent 61571 vs stored 99385 → mismatch 写入结果。"""
    from graphs.nodes.fetch_back_node import fetch_back_node
    from graphs.state import FetchBackInput

    fake_resp = mock.MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "result": [{
            "id": "123456",
            "attributes": [
                {"id": 8229, "values": [{"dictionary_value_id": 99385, "value": "杀虫剂"}]},
            ],
            "attributes_with_defaults": [10096],
        }]
    }
    with mock.patch("graphs.nodes.fetch_back_node.session") as _session:
        _session.post.return_value = fake_resp
        state = FetchBackInput(
            product_id="123456",
            ozon_client_id="cid",
            ozon_api_key="key",
            final_attributes=[
                {"id": 8229, "attribute_id": 8229, "dictionary_value_id": 61571, "value": "白色"},
            ],
        )
        out = fetch_back_node(state, None, mock.MagicMock())
    res = out.fetch_back_result
    assert len(res["mismatches"]) == 1
    m = res["mismatches"][0]
    assert m["attribute_id"] == 8229
    assert m["sent_dictionary_value_id"] == 61571
    assert m["stored_dictionary_value_id"] == 99385
    # 学习门输入：defaulted_by_ozon 应含 10096
    assert res["defaulted_by_ozon"] == [10096]


def test_fetch_back_node_erased_detected():
    """我们发了但 Ozon 没存（erased）→ 记录。"""
    from graphs.nodes.fetch_back_node import fetch_back_node
    from graphs.state import FetchBackInput

    fake_resp = mock.MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"result": [{"id": "1", "attributes": [], "attributes_with_defaults": []}]}
    with mock.patch("graphs.nodes.fetch_back_node.session") as _session:
        _session.post.return_value = fake_resp
        state = FetchBackInput(
            product_id="1", ozon_client_id="c", ozon_api_key="k",
            final_attributes=[{"id": 9782, "dictionary_value_id": 970593900, "value": "Не опасен"}],
        )
        out = fetch_back_node(state, None, mock.MagicMock())
    assert out.fetch_back_result["erased"] == [9782]


def test_fetch_back_node_no_product_id_skips():
    """无 product_id → 跳过回读，不调 API。"""
    from graphs.nodes.fetch_back_node import fetch_back_node
    from graphs.state import FetchBackInput
    with mock.patch("graphs.nodes.fetch_back_node.session") as _session:
        out = fetch_back_node(FetchBackInput(product_id="", ozon_client_id="c", ozon_api_key="k"), None, mock.MagicMock())
        _session.post.assert_not_called()
    assert out.fetch_back_result == {}


def test_fetch_back_api_failure_graceful():
    """API 失败 → 空结果，不抛异常。"""
    from graphs.nodes.fetch_back_node import fetch_back_node
    from graphs.state import FetchBackInput
    with mock.patch("graphs.nodes.fetch_back_node.session") as _session:
        _session.post.side_effect = Exception("network")
        out = fetch_back_node(
            FetchBackInput(product_id="9", ozon_client_id="c", ozon_api_key="k",
                           final_attributes=[{"id": 85, "dictionary_value_id": 1, "value": "x"}]),
            None, mock.MagicMock(),
        )
    assert out.fetch_back_result == {}


def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    total = len(fns)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
