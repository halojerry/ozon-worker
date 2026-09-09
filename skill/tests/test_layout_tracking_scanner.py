# skill/tests/test_layout_tracking_scanner.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.lib.ozon_widget import extract_layout_tracking_info  # noqa: E402

WIDGET = {
    "webReview": "{\"state\":\"ok\"}",
    "layoutTracking": "{\"layoutTrackingInfo\":{\"categoryId\":17027928,"
                     "\"categoryPath\":[\"Дом\",\"Термосы\"],"
                     "\"breadcrumbs\":[\"Термосы\"]}}",
}


def test_scanner_finds_category():
    out = extract_layout_tracking_info(WIDGET)
    assert out["categoryId"] == 17027928
    assert out["category_path"][-1] == "Термосы"


def test_scanner_none_on_absence():
    assert extract_layout_tracking_info({"x": "{\"a\":1}"}) is None


def test_fetch_product_info_carries_layout_tracking(monkeypatch):
    """fetch_product_info 结果携带 layout_tracking（无真值时省略键）。"""
    import inspect
    from scripts.lib import ozon_widget
    src = inspect.getsource(ozon_widget.fetch_product_info)
    assert "layout_tracking" in src and "extract_layout_tracking_info" in src
