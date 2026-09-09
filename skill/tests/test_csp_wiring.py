"""CSP 剥除接线审计：seller 借道与 widget 路径必须在取 tab 后剥 CSP。
（实机行为 gate 由控制器另行执行；本测试锁接线不缺失。）
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402
from scripts.lib import ozon_widget  # noqa: E402


def test_seller_analytics_paths_call_set_bypass_csp():
    src = inspect.getsource(osa)
    assert src.count("set_bypass_csp()") >= 2  # 复用 tab 路径 + 新建 tab 路径


def test_widget_ensure_tab_calls_set_bypass_csp():
    src = inspect.getsource(ozon_widget._ensure_ozon_tab)
    assert "set_bypass_csp()" in src
