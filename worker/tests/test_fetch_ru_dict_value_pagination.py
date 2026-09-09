"""R2 (v0.62): 字典值补查分页 — fetch_ru_dict_value 翻页命中，修复 >5000 条字典漏查。

覆盖：
- 首页命中
- 第 3 页命中（分页驱动）
- 翻完未命中返回 fallback
- HTTP != 200 返回 fallback
- has_next=False 提前退出
- dict_id=0 直接返回 fallback
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


from utils.ozon_errors import OzonServerError  # noqa: E402


def _make_pages(page_size=2):
    """构造 3 页字典值，目标 id=105 在第 3 页。"""
    all_vals = [
        {"id": 101, "value": "v101"},
        {"id": 102, "value": "v102"},
        {"id": 103, "value": "v103"},
        {"id": 104, "value": "v104"},
        {"id": 105, "value": "v105"},
        {"id": 106, "value": "v106"},
    ]
    pages = []
    for i in range(0, len(all_vals), page_size):
        chunk = all_vals[i : i + page_size]
        has_next = i + page_size < len(all_vals)
        pages.append({"result": chunk, "has_next": has_next})
    return pages


# F-F01: 字典 HTTP 收敛 ozon_post 后，mock 直接以解析后 dict 应答
def _fake_ozon_post(pages):
    class FakeCaller:
        def __init__(self):
            self.posted_last_ids = []

        def __call__(self, client_id, api_key, endpoint, body=None, timeout=15, **kw):
            self.posted_last_ids.append((body or {}).get("last_value_id", 0))
            idx = len(self.posted_last_ids) - 1
            return pages[min(idx, len(pages) - 1)]

    return FakeCaller()


def _patch_ozon_post(monkeypatch, caller):
    import utils.ozon_dict_values as odv

    monkeypatch.setattr(odv, "ozon_post", caller)
    return odv


def test_hit_on_first_page(monkeypatch):
    pages = [{"result": [{"id": 200, "value": "目标"}, {"id": 201, "value": "x"}], "has_next": False}]
    s = _fake_ozon_post(pages)
    odv = _patch_ozon_post(monkeypatch, s)

    out = odv.fetch_ru_dict_value("c", "k", 1, 2, 8229, dict_id=200, fallback="fb")
    assert out == "目标"
    assert s.posted_last_ids == [0], "首页命中不应翻页"


def test_hit_on_third_page(monkeypatch):
    pages = _make_pages(page_size=2)
    s = _fake_ozon_post(pages)
    odv = _patch_ozon_post(monkeypatch, s)

    out = odv.fetch_ru_dict_value("c", "k", 1, 2, 8229, dict_id=105, fallback="fb")
    assert out == "v105"
    assert s.posted_last_ids == [0, 102, 104], "应翻 3 页：0 → 102 → 104"


def test_not_found_returns_fallback(monkeypatch):
    pages = _make_pages(page_size=2)
    s = _fake_ozon_post(pages)
    odv = _patch_ozon_post(monkeypatch, s)

    out = odv.fetch_ru_dict_value("c", "k", 1, 2, 8229, dict_id=999, fallback="fb")
    assert out == "fb"
    assert s.posted_last_ids == [0, 102, 104], "翻完 3 页未命中"


def test_http_error_returns_fallback(monkeypatch):
    class FakeCaller:
        def __call__(self, *a, **kw):
            raise OzonServerError("HTTP 500", status_code=500)

    odv = _patch_ozon_post(monkeypatch, FakeCaller())
    out = odv.fetch_ru_dict_value("c", "k", 1, 2, 8229, dict_id=105, fallback="fb")
    assert out == "fb"


def test_has_next_false_early_exit(monkeypatch):
    pages = [
        {"result": [{"id": 101, "value": "v101"}], "has_next": False},
        {"result": [{"id": 105, "value": "v105"}], "has_next": False},
    ]
    s = _fake_ozon_post(pages)
    odv = _patch_ozon_post(monkeypatch, s)

    out = odv.fetch_ru_dict_value("c", "k", 1, 2, 8229, dict_id=105, fallback="fb")
    assert out == "fb", "第一页 has_next=False 即停，不应请求第二页"
    assert s.posted_last_ids == [0], "has_next=False 应提前退出"


def test_zero_dict_id_returns_fallback(monkeypatch):
    odv = _patch_ozon_post(monkeypatch, object())
    assert odv.fetch_ru_dict_value("c", "k", 1, 2, 8229, dict_id=0, fallback="fb") == "fb"
