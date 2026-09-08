"""skill 测试共享夹具。

v0.70 发版实证（2026-09-08）：enrich 链路测试 mock 了 analytics 抓取层
（fetch_bestseller_metrics_map/fetch_sales_analytics）但没 mock 登录层——
`_enrich_with_seller_metrics` 未登录分支会调 `wait_for_seller_login`
（非 TTY 下限 90s、默认 300s），而用例普遍 `mock.patch("time.sleep")`，
等待变成纯 CPU 空转 → 每个踩中用例烧 ~300s（全量套件 23 分钟的主因，
曾误判为死循环挂死）。

本夹具 autouse 把登录检测默认为「已登录」，登录等待零触发；登录行为
专项测试（下方排除清单）自测真实函数，不受影响——它们要么模块顶层
直接绑定真实函数引用，要么显式 mock.patch（内层 patch 覆盖本夹具）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 登录/就绪专项测试：断言未登录等待/降级行为，保持真实语义
_LOGIN_SPECIFIC_MODULES = {
    "test_login_wait_ux.py",
    "test_seller_silent_login_v069.py",
    "test_readiness.py",
    "test_seller_direct_enrichment.py",
    "test_analytics_upload.py",
    "test_what_to_sell_endpoints.py",
    "test_blue_ocean_live_queries.py",
}


@pytest.fixture(autouse=True)
def _assume_seller_logged_in(request, monkeypatch):
    if os.path.basename(str(request.node.fspath)) in _LOGIN_SPECIFIC_MODULES:
        yield
        return
    import scripts.lib.ozon_seller_analytics as osa

    monkeypatch.setattr(osa, "check_seller_login", lambda cdp: True)
    monkeypatch.setattr(osa, "wait_for_seller_login", lambda cdp, **kwargs: True)
    yield
