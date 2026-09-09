"""fetch_dictionary_values 单测（F-F01 字典 HTTP 收敛，2026-09-09）。

锁定 v0.72 三桶纪律的分页语义：
- 首页即 has_next（巨型字典）→ 取首页即止，绝不深翻（品牌 85 无底洞防线）；
- has_next=False 正常收尾；翻页游标推进且防死循环；
- limit 契约钳 2000；失败返回 None（区别于空字典 []）。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.ozon_dict_values import fetch_dictionary_values

_EP = "/v1/description-category/attribute/values"


def _page(ids, has_next):
    return {"result": [{"id": i, "value": f"v{i}"} for i in ids], "has_next": has_next}


def _capture(responses):
    """返回 (fake_ozon_post, calls)；responses 按 call 顺序弹出。"""
    calls = []

    def _fake(client_id, api_key, endpoint, body=None, timeout=30, **kw):
        calls.append({"endpoint": endpoint, "body": body})
        assert endpoint == _EP
        resp = responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    return _fake, calls


def test_first_page_giant_dict_stops_immediately():
    """首页即 has_next（>2000 巨型字典）→ 只调一次，取首页即止。"""
    fake, calls = _capture([_page([1, 2, 3], has_next=True)])
    with mock.patch("utils.ozon_dict_values.ozon_post", side_effect=fake):
        out = fetch_dictionary_values("c", "k", 85, 1, 2)
    assert out is not None and len(out) == 3
    assert len(calls) == 1, "巨型字典首页即止，不得翻页"
    assert calls[0]["body"]["last_value_id"] == 0


def test_normal_pagination_walks_all_pages():
    """首页 has_next=False 常规字典 → 单页收尾。"""
    fake, calls = _capture([_page([1, 2], has_next=False)])
    with mock.patch("utils.ozon_dict_values.ozon_post", side_effect=fake):
        out = fetch_dictionary_values("c", "k", 8229, 1, 2)
    assert [v["id"] for v in out] == [1, 2]
    assert len(calls) == 1


def test_first_page_has_next_always_stops():
    """v0.72 契约：首页 has_next=True 恒取首页即止（哪怕字典只差几条就翻页）——
    物化拦截交由缓存层，value→id 精确查走 /values/search。"""
    fake, calls = _capture([
        _page([1, 2], has_next=True),
        _page([999], has_next=False),  # 若被翻到即失败
    ])
    with mock.patch("utils.ozon_dict_values.ozon_post", side_effect=fake):
        out = fetch_dictionary_values("c", "k", 4389, 1, 2)
    assert [v["id"] for v in out] == [1, 2]
    assert len(calls) == 1, "首页即 has_next 必须停止，不得发起第二页"


def test_limit_clamped_to_contract_max():
    """limit 钳 2000（契约 max，5000+ 属违约调用）。"""
    fake, calls = _capture([_page([1], has_next=False)])
    with mock.patch("utils.ozon_dict_values.ozon_post", side_effect=fake):
        fetch_dictionary_values("c", "k", 85, 1, 2, limit=5000)
    assert calls[0]["body"]["limit"] == 2000


def test_failure_returns_none_not_empty():
    """API 失败 → None（调用方据此判断不写缓存），绝不与空字典 [] 混淆。"""
    from utils.ozon_errors import OzonServerError
    fake, _calls = _capture([OzonServerError("boom", status_code=500)])
    with mock.patch("utils.ozon_dict_values.ozon_post", side_effect=fake):
        out = fetch_dictionary_values("c", "k", 85, 1, 2)
    assert out is None


def test_empty_page_returns_empty_list():
    """空 result 页 → []（合法空字典），不误报失败。"""
    fake, _calls = _capture([{"result": [], "has_next": False}])
    with mock.patch("utils.ozon_dict_values.ozon_post", side_effect=fake):
        out = fetch_dictionary_values("c", "k", 85, 1, 2)
    assert out == []


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
