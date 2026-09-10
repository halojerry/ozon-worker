"""premium 解锁全 seller tab 路径覆盖审计——每个取 seller.ozon.ru tab 做
页面工作的函数必须挂 premium 解锁（_install_premium_unlock 或等价伪装）。

审计方法（Task 5.2）：全仓 grep `find_tab(` / `new_tab(` / `_tab_for_seller` /
`_install_premium_unlock`，并以 Task 4.2 的 CSP 接线面（dd9f24e3，
`set_bypass_csp` 调点位）核对人群一致。纯源码断言级（inspect.getsource），
防未来新 seller 路径漏挂解锁。

⚠️ 函数引用在本模块 import 时绑定（tests/conftest.py 的 autouse 夹具会在
测试 setup 阶段把 osa.wait_for_seller_login/check_seller_login 替换成
lambda——getattr 到的是 lambda，getsource 拿不到真实源码。import 时绑定
即夹具 docstring 说的「模块顶层直接绑定真实函数引用」模式）。

排除清单（仅 CDP cookie 读取，about:blank 零导航、无页面求值，故不需要解锁；
negative 用例锁死「排除函数不得偷偷加页面求值」，一旦加了必须移入审计人群）：
- ozon_seller_analytics._read_seller_cookies_silent（Network.getCookies）
- ozon_seller_analytics._cdp_get_cookies_sequence（Network/Storage.getCookies）
- ozon_seller_analytics._fetch_seller_session_cookies / get_seller_session_cookies
  （上两项的封装）
- ozon_seller_analytics.check_seller_login（静默 cookie 罐检测）

等价实现（自带 premium 伪装，不重复挂 osa 解锁）：
- ozon_seller.fetch_analytics_via_premium_spoof（自带 SPOOF_JS
  add_init_script，XHR premium/status 伪装后导航 graphs 页）

非 seller 域不在本审计人群（premium 解锁只对 seller.ozon.ru 门户接口有意义；
www 域的 CSP 剥除面见 test_csp_wiring.py）：ozon_widget._ensure_ozon_tab、
ozon_discovery（highlight/search 页）、cli.py（登录辅助 tab / 滚动采集）、
batch_test.py、ozon_scraper.py、ozon_fission.py、readiness.py、ozon_session.py。
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402
from scripts.lib import ozon_seller  # noqa: E402
from scripts.lib import ozon_widget  # noqa: E402

# (module, 函数名, import 时绑定的真实函数, 可接受的解锁/委托令牌)
# 令牌语义：直挂 _install_premium_unlock；或经 _tab_for_seller /
# _tab_for_variant_truth helper（helper 自身挂解锁，见
# test_central_helpers_install_unlock）；或传递委托到已审计 fetch；或等价伪装。
_SELLER_TAB_FNS = [
    # ── ozon_seller_analytics（中心 helper _tab_for_seller 已挂解锁，复用+新建双分支）──
    (osa, "wait_for_seller_login", osa.wait_for_seller_login, ("_tab_for_seller",)),
    (osa, "fetch_sales_analytics", osa.fetch_sales_analytics, ("_tab_for_seller",)),
    (osa, "fetch_all_queries", osa.fetch_all_queries, ("_tab_for_seller",)),
    (osa, "fetch_ozon_bestsellers", osa.fetch_ozon_bestsellers, ("_tab_for_seller",)),
    (osa, "fetch_market_bestsellers", osa.fetch_market_bestsellers, ("_tab_for_seller",)),
    (osa, "fetch_bestseller_metrics_map", osa.fetch_bestseller_metrics_map,
     ("fetch_ozon_bestsellers",)),  # 传递委托
    # ── ozon_widget（variant 真值链，Task 5.2 补挂）──
    (ozon_widget, "_tab_for_variant_truth", ozon_widget._tab_for_variant_truth,
     ("_install_premium_unlock",)),
    (ozon_widget, "_fetch_variant_truth_via", ozon_widget._fetch_variant_truth_via,
     ("_tab_for_variant_truth",)),
    # ── 等价实现 ──
    (ozon_seller, "fetch_analytics_via_premium_spoof",
     ozon_seller.fetch_analytics_via_premium_spoof, ("SPOOF_JS",)),
]

# 排除清单（cookie-only，无页面求值——见模块 docstring；同样 import 时绑定）
_COOKIE_ONLY_FNS = [
    (osa, "_read_seller_cookies_silent", osa._read_seller_cookies_silent),
    (osa, "_cdp_get_cookies_sequence", osa._cdp_get_cookies_sequence),
    (osa, "_fetch_seller_session_cookies", osa._fetch_seller_session_cookies),
    (osa, "get_seller_session_cookies", osa.get_seller_session_cookies),
    (osa, "check_seller_login", osa.check_seller_login),
]


def test_central_helpers_install_unlock():
    """中心 helper 自身必须挂解锁：osa 复用走运行时注入、新建走
    add_init_script 预注入；widget helper（Task 5.2 补挂）两分支运行时注入。"""
    src_osa = inspect.getsource(osa._tab_for_seller)
    assert "_install_premium_unlock" in src_osa, "_tab_for_seller 缺 _install_premium_unlock"
    assert "add_init_script" in src_osa, "_tab_for_seller 新建分支缺 add_init_script 预注入"
    src_widget = inspect.getsource(ozon_widget._tab_for_variant_truth)
    assert "_install_premium_unlock" in src_widget, "_tab_for_variant_truth 缺 _install_premium_unlock"


def test_all_seller_tab_paths_install_premium_unlock():
    for module, fn_name, fn, tokens in _SELLER_TAB_FNS:
        assert getattr(module, fn_name, None) is not None, (
            f"{module.__name__}.{fn_name} 不存在（函数改名须同步本审计清单）")
        src = inspect.getsource(fn)
        accepted = ("_install_premium_unlock",) + tuple(tokens)
        assert any(t in src for t in accepted), (
            f"{module.__name__}.{fn_name} 取 seller tab 做页面工作但未挂 premium 解锁"
            f"（令牌均缺失：{accepted}）"
        )


def test_cookie_only_exclusions_stay_evaluation_free():
    """排除清单守护：cookie-only 函数一旦加了页面求值（tab.evaluate），
    就脱离排除语义，必须移入 _SELLER_TAB_FNS 审计人群并挂解锁。"""
    for module, fn_name, fn in _COOKIE_ONLY_FNS:
        assert getattr(module, fn_name, None) is not None, (
            f"{module.__name__}.{fn_name} 不存在（函数改名须同步排除清单）")
        src = inspect.getsource(fn)
        assert ".evaluate(" not in src, (
            f"{module.__name__}.{fn_name} 声称 cookie-only 却含页面求值——"
            "请移入 _SELLER_TAB_FNS 审计人群并挂 _install_premium_unlock"
        )
