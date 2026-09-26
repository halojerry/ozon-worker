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


# Task 2.3 起 `_enrich_with_seller_metrics` 入口先查数据池（metrics_pool_client.
# query_sku_metrics）：不 mock 的话，配置了 ~/.pounding token 的机器上任何未
# 显式 patch 池缝的富化用例都会真实打 worker（工作区纪律红线：测试禁打生产）。
# 默认按「池不可用（None）」处理——与登录夹具同款排除清单模式；数据池专项
# 测试（test_metrics_pool_client.py 测真实函数）除外。各用例显式 monkeypatch
# 可覆盖本夹具（同一 function-scoped monkeypatch 实例，内层 setattr 胜出）。
_POOL_QUERY_REAL_MODULES = {"test_metrics_pool_client.py"}


@pytest.fixture(autouse=True)
def _pool_query_off_by_default(request, monkeypatch):
    if os.path.basename(str(request.node.fspath)) in _POOL_QUERY_REAL_MODULES:
        yield
        return
    import scripts.lib.metrics_pool_client as mpc

    monkeypatch.setattr(mpc, "query_sku_metrics", lambda skus, **kwargs: None)
    yield


# T3（fix/skill-concurrency-v1）起 aibuy 导航刷新 claim 从 settings.json 键改为
# 真实占位文件 data/config/.aibuy_refresh_claim.json——测试若不隔离会把文件落进
# 开发机真实 data/config/（工作区污染），且 600s 冷却跨测试串扰（前一个用例的
# claim 拦掉后一个用例的导航刷新分支）。全量把 CONFIG_DIR 指到 tmp：本仓库唯一
# 运行时动态读 scripts._const.CONFIG_DIR 的就是 claim 路径派生（settings.json/
# stores.json 路径在 config_store import 时绑定，不受影响）；各用例显式
# monkeypatch 可覆盖（同一 function-scoped monkeypatch 实例，内层 setattr 胜出）。
@pytest.fixture(autouse=True)
def _aibuy_refresh_claim_isolated(tmp_path, monkeypatch):
    import scripts._const

    monkeypatch.setattr(scripts._const, "CONFIG_DIR", tmp_path)
    yield


# arch-findings #5（信封三腿统一）起 follow/discover 降级腿也调 get_template_profile
# （token 非空即真实 GET worker /api/v1/templates）。测试若不默认关掉，配置了
# ~/.pounding token 的机器上任何走到信封组装的用例都会真实打 worker（工作区纪律
# 红线：测试禁打生产）。默认按「无模板（None）」处理——与登录/数据池夹具同款
# 排除清单模式；模板专项测试（test_template_profile.py 测真实函数）除外。各用例
# 显式 mock.patch 可覆盖本夹具（内层 patch 胜出）。
_TEMPLATE_REAL_MODULES = {"test_template_profile.py"}


@pytest.fixture(autouse=True)
def _template_fetch_off_by_default(request, monkeypatch):
    if os.path.basename(str(request.node.fspath)) in _TEMPLATE_REAL_MODULES:
        yield
        return
    import scripts.lib.config_store as _cs

    monkeypatch.setattr(_cs, "get_template_profile", lambda *a, **k: None)
    yield
