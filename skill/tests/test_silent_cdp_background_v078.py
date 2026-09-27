#!/usr/bin/env python3
"""批A A1（fix/skill-silent-cdp-v1）：静默 CDP 路径全部改后台 tab 回归。

背景：cdp_client.new_tab() 默认 background=False 走 PUT /json/new——Chrome
创建并**激活** tab 到前台。静默场景（cookie 探针/图搜/seller 数据/discover
采集/淘宝拼多多抓取）此前全部默认前台 → 实跑中 Chrome 反复弹前台（1688 首页 /
seller.ozon.ru/ch/ / about:blank 闪动）。本文件逐调用点锁定 background=True；
v0.78 时的前台白名单（ozon_scraper / cli._open_tab / 登录引导页）——其中
ozon_scraper 已在 v0.81 教义翻转为「后台 tab + force_active」（懒加载渲染
不再依赖抢前台，实机实证见 test_ozon_scraper_silent_doctrine_v081），
cli._open_tab 与登录引导页保持显式前台（给人看的页面）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_silent_cdp_background_v078.py -q
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_image_search as ois  # noqa: E402
from scripts.lib import ozon_seller_analytics as osa  # noqa: E402
from scripts.lib import readiness as rd  # noqa: E402
from scripts.lib import taobao_client as tc  # noqa: E402
from scripts.lib import pdd_client as pd  # noqa: E402
from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib import cdp_client  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# conftest autouse 夹具会把 osa.wait_for_seller_login stub 掉（非登录专项文件）——
# 白名单用例须测真实登录引导行为，import 时绑真身（函数运行时经模块全局解析
# check_seller_login/_tab_for_seller，用例内 monkeypatch 仍生效）。
_WAIT_FOR_SELLER_LOGIN_REAL = osa.wait_for_seller_login

# 4 个 1688 反爬 cookie 全齐且 _m_h5_tk 非空（_aibuy_token_valid 的可用形状）
_VALID_1688_COOKIE_JS = "_m_h5_tk=tok_123; _m_h5_tk_enc=enc_456; tfstk=tf; isg=isg"
_VALID_1688_COOKIES = [
    {"name": "_m_h5_tk", "value": "tok_123"},
    {"name": "_m_h5_tk_enc", "value": "enc_456"},
    {"name": "tfstk", "value": "tf"},
    {"name": "isg", "value": "isg"},
]


class _FakeTab:
    """假 CdpTab：导航/关闭记录在案，_send/_recv 与 evaluate 按注入值应答。"""

    def __init__(self, evaluate_value: str = "", cookies_resp: dict | None = None,
                 url: str = ""):
        self.navigated: list[str] = []
        self.closed = False
        self.url = url
        self._evaluate_value = evaluate_value
        self._cookies_resp = cookies_resp or {"result": {"cookies": []}}

    def navigate(self, url, **kw):
        self.navigated.append(url)

    def wait_for_load(self, timeout=15):
        pass

    def set_bypass_csp(self):
        pass

    def add_init_script(self, js):
        pass

    def evaluate(self, js, **kw):
        return self._evaluate_value

    def _send(self, method, params=None):
        return 42  # msg_id（假）

    def _recv_until_id(self, msg_id, timeout=10):
        return self._cookies_resp

    def close(self, *a, **k):
        self.closed = True


class _FakeConn:
    """假 CdpConnection：new_tab 记录 (url, background)，find_tab 恒未命中。"""

    def __init__(self, tab: _FakeTab | None = None):
        self.tab = tab or _FakeTab()
        self.calls: list[tuple[str, bool]] = []
        self.released: list[object] = []

    def new_tab(self, url="about:blank", background=False, **kw):
        self.calls.append((url, background))
        return self.tab

    def find_tab(self, pattern):
        return None

    def release(self, tab):
        self.released.append(tab)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ── A1 逐调用点：静默路径必须 background=True ──────────────────────────────


def test_aibuy_cookie_fetch_uses_background_tab(monkeypatch):
    """ois._fetch_aibuy_cookies_from_chrome（前台开 1688 首页元凶）→ 后台 tab。"""
    conn = _FakeConn(_FakeTab(evaluate_value=_VALID_1688_COOKIE_JS))
    monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", lambda *a, **k: conn)
    cookies = ois._fetch_aibuy_cookies_from_chrome("http://127.0.0.1:9222")
    assert cookies.get("_m_h5_tk") == "tok_123", "探针行为不变：仍应读到有效 cookie"
    assert conn.calls == [("about:blank", True)], "建 tab 必须后台（不激活到前台）"


def test_read_1688_cookies_silent_uses_background_tab(monkeypatch):
    conn = _FakeConn(_FakeTab(cookies_resp={"result": {"cookies": _VALID_1688_COOKIES}}))
    monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", lambda *a, **k: conn)
    cookies = ois._read_1688_cookies_silent("http://127.0.0.1:9222")
    assert cookies.get("_m_h5_tk") == "tok_123"
    assert conn.calls == [("about:blank", True)]


def test_tab_for_seller_uses_background_tab(monkeypatch):
    """osa._tab_for_seller 新建 seller 数据 tab → 后台（登录引导页走显式白名单参数）。"""
    monkeypatch.setattr("time.sleep", lambda s: None)
    cdp = _FakeConn()
    tab, reused = osa._tab_for_seller(cdp)
    assert reused is False and tab is not None
    assert cdp.calls == [("about:blank", True)]


def test_read_seller_cookies_silent_uses_background_tab(monkeypatch):
    resp = {"result": {"cookies": [{"name": "sc_company_id", "value": "42"}]}}
    cdp = _FakeConn(_FakeTab(cookies_resp=resp))
    cookies = osa._read_seller_cookies_silent(cdp)
    assert cookies.get("sc_company_id") == "42"
    assert cdp.calls == [("about:blank", True)]


def test_cdp_get_cookies_sequence_uses_background_tab():
    resp = {"result": {"cookies": [{"name": "sc_company_id", "value": "42"}]}}
    conn = _FakeConn(_FakeTab(cookies_resp=resp))
    network_resp, storage_resp = osa._cdp_get_cookies_sequence(conn)
    assert network_resp and storage_resp
    assert conn.calls == [("about:blank", True)]


def test_probe_alibaba_login_uses_background_tab(monkeypatch):
    """readiness 1688 登录探针：行为不变（cookie2 在场→True），建 tab 方式变后台。"""
    conn = _FakeConn(_FakeTab(cookies_resp={"result": {"cookies": [
        {"name": "cookie2", "value": "x"}]}}))
    monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", lambda *a, **k: conn)
    assert rd.probe_alibaba_login("http://127.0.0.1:9222") is True
    assert conn.calls == [("about:blank", True)]


def test_probe_ozon_datadome_uses_background_tab(monkeypatch):
    """readiness DataDome 探针（无已开 product tab 时临时导航 ozon.ru）→ 后台。"""
    conn = _FakeConn(_FakeTab(evaluate_value="ok"))
    monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", lambda *a, **k: conn)
    monkeypatch.setattr("requests.get", lambda *a, **k: (_ for _ in ()).throw(
        ConnectionError("no /json in test")))
    assert rd.probe_ozon_datadome("http://127.0.0.1:9222") is True
    assert conn.calls == [("https://www.ozon.ru/", True)]


def test_discover_collect_stage1_uses_background_tab(monkeypatch):
    """discover 阶段① 搜索/亮点页滚动采集 tab → 后台（空结果快速走完全程）。"""
    conn = _FakeConn()
    monkeypatch.setattr("scripts.lib.cdp_client.CdpConnection", lambda *a, **k: conn)
    monkeypatch.setattr(od, "_lazy_collect_rows", lambda *a, **k: [])
    monkeypatch.setattr(od, "_lazy_collect_urls", lambda *a, **k: [])
    monkeypatch.setattr("time.sleep", lambda s: None)
    url = "https://www.ozon.ru/search/?text=test"
    candidates = od.collect_and_analyze("http://127.0.0.1:9222", url=url)
    assert candidates == []
    assert conn.calls == [(url, True)], "discover 阶段① 采集 tab 必须后台"


def test_taobao_fetch_product_uses_background_tab(monkeypatch):
    """taobao_client.fetch_product 商品页抓取 tab → 后台。"""
    cdp = _FakeConn()
    monkeypatch.setattr(tc, "_evaluate_fetch",
                        lambda tab, target, timeout: (_ for _ in ()).throw(
                            RuntimeError("stop-after-newtab")))

    class _Err(Exception):
        pass

    monkeypatch.setattr(tc, "TaobaoFetchError", _Err, raising=False)
    with pytest.raises(Exception):
        tc.fetch_product("http://127.0.0.1:9222",
                         "https://item.taobao.com/item.htm?id=123456", cdp=cdp)
    assert cdp.calls == [("https://item.taobao.com/item.htm?id=123456", True)]


def test_taobao_wait_for_login_uses_background_tab(monkeypatch):
    """taobao_client.wait_for_login 登录页 new_tab 兜底 → 后台（简报 A1 清单）。"""
    cdp = _FakeConn(_FakeTab(url=""))  # url 恒空 → 不判登录完成 → 轮询即睡
    sentinel = RuntimeError("stop-poll")

    def _boom(_s):
        raise sentinel

    monkeypatch.setattr("time.sleep", _boom)
    assert tc.wait_for_login("http://127.0.0.1:9222", cdp=cdp) is False
    assert cdp.calls == [(tc.TAOBAO_LOGIN_URL, True)]


def test_pdd_fetch_product_uses_background_tab(monkeypatch):
    """pdd_client.fetch_product 商品页抓取 tab → 后台。"""
    cdp = _FakeConn()
    monkeypatch.setattr(pd, "_evaluate_extract",
                        lambda tab, target, timeout: (_ for _ in ()).throw(
                            RuntimeError("stop-after-newtab")))

    class _Err(Exception):
        pass

    monkeypatch.setattr(pd, "PddFetchError", _Err, raising=False)
    with pytest.raises(Exception):
        pd.fetch_product("http://127.0.0.1:9222",
                         "https://mobile.yangkeduo.com/goods.html?goods_id=123456",
                         cdp=cdp)
    assert len(cdp.calls) == 1
    url, background = cdp.calls[0]
    assert background is True, "pdd 商品抓取 tab 必须后台"
    assert "goods_id=123456" in url


def test_pdd_wait_for_login_uses_background_tab(monkeypatch):
    """pdd_client.wait_for_login 登录页 new_tab 兜底 → 后台（简报 A1 清单）。"""
    cdp = _FakeConn(_FakeTab(url=""))
    monkeypatch.setattr("time.sleep", lambda s: (_ for _ in ()).throw(
        RuntimeError("stop-poll")))
    assert pd.wait_for_login("http://127.0.0.1:9222", cdp=cdp) is False
    assert len(cdp.calls) == 1
    url, background = cdp.calls[0]
    assert background is True
    assert "login" in url.lower() or "pdd" in url.lower()


# ── 前台白名单：不许改 ────────────────────────────────────────────────────


def test_wait_for_seller_login_guide_stays_foreground(monkeypatch):
    """白名单：wait_for_seller_login 首次登录引导页（人工要登录）保持前台。"""
    monkeypatch.setattr(osa, "seller_login_confirmed_recently", lambda **k: False)
    monkeypatch.setattr(osa, "seller_login_wait_recently_attempted",
                        lambda ttl=180.0: False)
    monkeypatch.setattr(osa, "check_seller_login", lambda cdp: False)
    cdp = _FakeConn()
    monkeypatch.setattr("time.sleep", lambda s: (_ for _ in ()).throw(
        RuntimeError("stop-poll")))
    with pytest.raises(RuntimeError):
        _WAIT_FOR_SELLER_LOGIN_REAL(cdp, timeout_seconds=5, poll_interval=0.1)
    assert cdp.calls == [("about:blank", False)], "登录引导页必须保持前台（background=False）"


def test_ozon_scraper_silent_doctrine_v081():
    """v0.81 教义翻转：ozon_scraper 主路径后台 tab（零弹窗），验证码人工重试保持可见。

    2026-09-26 实机实证：后台 tab + CdpTab.force_active()（setWebLifecycleState
    active + setFocusEmulationEnabled）→ visibilityState=visible、rAF 64fps、
    IO 正常派发——懒加载渲染不再依赖抢 macOS 前台。批量抓取每商品 4-6 次
    前台激活被用户判「电脑完全没法做事情」，主路径翻后台；滑块重试 tab
    是给人看的，保持显式前台。
    """
    src = (_SCRIPTS / "lib" / "ozon_scraper.py").read_text(encoding="utf-8")
    assert "new_tab(background=True)" in src, "商品页抓取主路径必须是后台 tab"
    assert "force_active()" in src, "后台 tab 必须接 force_active 可见化渲染"
    assert "conn.new_tab(ozon_url, background=False)" in src, "验证码人工重试重开可见 tab（给人看）"


def test_cli_open_tab_whitelist_stays_foreground():
    """白名单：cli._open_tab（check 诊断，用户主动触发）保持前台。"""
    src = (_SCRIPTS / "cli.py").read_text(encoding="utf-8")
    fn = src.split("def _open_tab(", 1)[1].split("\n    def ", 1)[0]
    assert "new_tab(url, background=False)" in fn, "_open_tab 显式前台（用户要亲自看）"


def test_new_tab_interface_unchanged():
    """接口锁定：CdpConnection.new_tab 签名形状不变；v0.81 起默认后台。"""
    sig = inspect.signature(cdp_client.CdpConnection.new_tab)
    assert list(sig.parameters) == ["self", "url", "background"]
    assert sig.parameters["url"].default == "about:blank"
    assert sig.parameters["background"].default is True
