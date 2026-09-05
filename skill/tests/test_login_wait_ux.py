# -*- coding: utf-8 -*-
"""v0.63.3 登录等待 UX 修复回归（用户反馈：还没登录页面/浏览器就被关掉）。

锁定三个行为：
1. 1688 登录等待超时【不关登录页】（此前 finally tab.close() + cdp.close() 远程
   关 tab → 工具 Chrome 窗口仅剩登录页时整个窗口/浏览器消失）。
2. 超时分级下限：TTY（人工）≥300s 且可交互续等；非 TTY（agent/管道）≥90s 快速返回。
   返回契约与 Q3 一致：{ok: False, session: None, reason: 'timeout'}（test_login_misjudge 锁定）。
3. seller.ozon.ru 登录等待：未登录自动开页并保留（release 出连接管理，连接关闭不连带关）、
   轮询命中 → True；超时 → False 且 tab 保留。

⚠️ 时间 mock 必须用 ``mock.patch("time.time", new=_now)`` 函数直替（CPython 3.14 实测：
MagicMock(side_effect=lambda: next(count())) 风格在本场景与 input-EOF 路径交互会假死循环）。

运行: cd skill && .venv314/bin/python -m pytest tests/test_login_wait_ux.py -q
"""
from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 预导入 service：模块体依赖 scripts._const 创建真实 data/ 目录
import scripts.capabilities.browser_probe.service  # noqa: F401  isort:skip
import scripts.lib.ozon_seller_analytics as osa  # noqa: E402


def _enter(mocks):
    stack = ExitStack()
    for m in mocks:
        stack.enter_context(m)
    return stack


def _fake_stdin(tty: bool):
    return SimpleNamespace(isatty=lambda: tty)


def _fake_clock():
    """确定性假时钟：每次调用 +1（秒）。返回 (now函数, 计数dict)。"""
    state = {"now": 0}

    def _now():
        state["now"] += 1
        return state["now"]

    return _now, state


def _login_mocks(session, *, login_required=None, login_required_fn=None):
    """_wait_for_login_session 依赖全部受控（同 test_login_misjudge 手法）。"""
    tab = mock.Mock()
    tab.close = mock.Mock()
    cdp = mock.Mock()
    cdp.new_tab.return_value = tab
    cdp.close = mock.Mock()
    if login_required_fn is not None:
        snap = mock.patch("scripts.capabilities.browser_probe.service._snapshot_login_required",
                          side_effect=login_required_fn)
    else:
        snap = mock.patch("scripts.capabilities.browser_probe.service._snapshot_login_required",
                          return_value=login_required)
    stack = [
        mock.patch("scripts.capabilities.browser_probe.service._login_in_progress", False, create=True),
        mock.patch("scripts.capabilities.browser_probe.service._resolve_browser_session", return_value=session),
        mock.patch("scripts.capabilities.browser_probe.service._find_live_cdp_session_for_profile", return_value=None),
        mock.patch("scripts.capabilities.browser_probe.service._write_browser_session", return_value=None),
        mock.patch("scripts.capabilities.browser_probe.service.CdpConnection", return_value=cdp),
        mock.patch("scripts.capabilities.browser_probe.service._extract_qr_code_base64", return_value=None),
        mock.patch("scripts.capabilities.browser_probe.service._probe_login_snapshot",
                   return_value={"url": "https://login.1688.com/", "bodyText": "扫码登录"}),
        snap,
        mock.patch("time.sleep", new=lambda *_a, **_k: None),
    ]
    return {"tab": tab, "cdp": cdp, "stack": stack}


# ═══════════ 1. 1688 登录等待：超时不关登录页 ═══════════

def test_wait_login_timeout_keeps_login_tab():
    """非 TTY 超时 → 契约返回 + 登录页保留（tab.close 不调用、cdp 只关 WS）。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    session = {"cdp_url": "http://127.0.0.1:9999", "profile": "default"}
    m = _login_mocks(session=session, login_required=True)
    _now, _clock = _fake_clock()
    with _enter(m["stack"]), \
            mock.patch("time.time", new=_now), \
            mock.patch("sys.stdin", _fake_stdin(tty=False)):
        result = _wait_for_login_session(
            "https://detail.1688.com/offer/1.html",
            profile_name="default",
            browser_path="/usr/bin/google-chrome",
            timeout_seconds=30,
        )
    assert result == {"ok": False, "session": None, "reason": "timeout"}
    assert not m["tab"].close.called, "超时绝不能远程关登录页"
    m["cdp"].close.assert_called_once_with(close_remote=False), "连接只能关 WS，不得连带关 tab"


def test_wait_login_tty_timeout_prompts_then_eof_returns():
    """TTY 超时 → 提示续等；input EOF（agent 场景）→ 契约返回且登录页保留。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    session = {"cdp_url": "http://127.0.0.1:9999", "profile": "default"}
    m = _login_mocks(session=session, login_required=True)
    _now, _clock = _fake_clock()
    with _enter(m["stack"]), \
            mock.patch("time.time", new=_now), \
            mock.patch("sys.stdin", _fake_stdin(tty=True)), \
            mock.patch("builtins.input", side_effect=EOFError):
        result = _wait_for_login_session(
            "https://detail.1688.com/offer/1.html",
            profile_name="default",
            browser_path="/usr/bin/google-chrome",
            timeout_seconds=30,
        )
    assert result == {"ok": False, "session": None, "reason": "timeout"}
    assert not m["tab"].close.called


def test_wait_login_tty_enter_continues_until_login():
    """TTY 超时后按 Enter → 不限时继续轮询 → 登录检测成功（ok=True）。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    session = {"cdp_url": "http://127.0.0.1:9999", "profile": "default"}
    polls = {"n": 0}

    def _login_required(_url, _body):
        polls["n"] += 1
        return polls["n"] <= 300  # 前 300 次未登录（覆盖 300s TTY 下限），之后已登录

    m = _login_mocks(session=session, login_required_fn=_login_required)
    _now, _clock = _fake_clock()
    with _enter(m["stack"]), \
            mock.patch("time.time", new=_now), \
            mock.patch("sys.stdin", _fake_stdin(tty=True)), \
            mock.patch("builtins.input", return_value=""):
        result = _wait_for_login_session(
            "https://detail.1688.com/offer/1.html",
            profile_name="default",
            browser_path="/usr/bin/google-chrome",
            timeout_seconds=30,
        )
    assert result["ok"] is True and result["reason"] is None
    assert result["session"]["login_detected"] is True
    assert polls["n"] > 300, "按 Enter 后应不限时继续轮询（至少跑到 300s 下限之后）"


def test_wait_login_tty_floor_is_300s():
    """TTY 下超时下限 300s（旧 30s 传参不再生效）——以轮询次数验证。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    session = {"cdp_url": "http://127.0.0.1:9999", "profile": "default"}
    polls = {"n": 0}

    def _req(_u, _b):
        polls["n"] += 1
        return True

    m = _login_mocks(session=session, login_required_fn=_req)
    _now, _clock = _fake_clock()
    with _enter(m["stack"]), \
            mock.patch("time.time", new=_now), \
            mock.patch("sys.stdin", _fake_stdin(tty=True)), \
            mock.patch("builtins.input", side_effect=EOFError):
        result = _wait_for_login_session(
            "https://detail.1688.com/offer/1.html",
            profile_name="default",
            browser_path="/usr/bin/google-chrome",
            timeout_seconds=30,
        )
    assert result["reason"] == "timeout"
    # start 占时钟第 1 次调用：第 k 次迭代时钟值 = k+1，300s 下限 → ~299 次轮询
    assert polls["n"] >= 295, f"TTY 下限应为 300s，实际只轮询了 {polls['n']} 次"


def test_wait_login_non_tty_floor_is_90s():
    """非 TTY 下超时下限 90s（<300，agent 场景不长时间阻塞）。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    session = {"cdp_url": "http://127.0.0.1:9999", "profile": "default"}
    polls = {"n": 0}

    def _req(_u, _b):
        polls["n"] += 1
        return True

    m = _login_mocks(session=session, login_required_fn=_req)
    _now, _clock = _fake_clock()
    with _enter(m["stack"]), \
            mock.patch("time.time", new=_now), \
            mock.patch("sys.stdin", _fake_stdin(tty=False)):
        result = _wait_for_login_session(
            "https://detail.1688.com/offer/1.html",
            profile_name="default",
            browser_path="/usr/bin/google-chrome",
            timeout_seconds=30,
        )
    assert result["reason"] == "timeout"
    assert 85 <= polls["n"] < 295, f"非 TTY 下限应为 90s，实际轮询 {polls['n']} 次"


# ═══════════ 2. seller.ozon.ru 登录等待 ═══════════

def test_wait_seller_already_logged_in_returns_true():
    """已登录 → 立即 True，不开 tab、不打扰。"""
    cdp = mock.Mock()
    with mock.patch.object(osa, "check_seller_login", return_value=True):
        assert osa.wait_for_seller_login(cdp, timeout_seconds=5) is True
    cdp.new_tab.assert_not_called()
    cdp.release.assert_not_called()


def test_wait_seller_poll_hit_returns_true_and_keeps_tab():
    """未登录 → 开页（release 出连接管理）→ 轮询命中 → True；tab 不被关。"""
    cdp = mock.Mock()
    tab = mock.Mock()
    tab.close = mock.Mock()
    with mock.patch.object(osa, "check_seller_login", side_effect=[False, True]), \
            mock.patch.object(osa, "_tab_for_seller", return_value=(tab, False)), \
            mock.patch("time.sleep", new=lambda *_a, **_k: None):
        assert osa.wait_for_seller_login(cdp, timeout_seconds=10) is True
    cdp.release.assert_called_once_with(tab), "tab 必须 release 出连接管理（防 conn.close 连带关闭）"
    assert not tab.close.called


def test_wait_seller_timeout_non_tty_returns_false_keeps_tab():
    """非 TTY 超时 → False；tab 保留（绝不 close）。"""
    cdp = mock.Mock()
    tab = mock.Mock()
    tab.close = mock.Mock()
    _now, _clock = _fake_clock()
    with mock.patch.object(osa, "check_seller_login", return_value=False), \
            mock.patch.object(osa, "_tab_for_seller", return_value=(tab, False)), \
            mock.patch("time.sleep", new=lambda *_a, **_k: None), \
            mock.patch("time.time", new=_now), \
            mock.patch("sys.stdin", _fake_stdin(tty=False)):
        assert osa.wait_for_seller_login(cdp, timeout_seconds=5) is False
    cdp.release.assert_called_once_with(tab)
    assert not tab.close.called


def test_wait_seller_tty_floor_is_300s():
    """TTY 下超时下限 300s——以轮询次数验证。"""
    cdp = mock.Mock()
    tab = mock.Mock()
    polls = {"n": 0}

    def _check(_c):
        polls["n"] += 1
        return False

    _now, _clock = _fake_clock()
    with mock.patch.object(osa, "check_seller_login", side_effect=_check), \
            mock.patch.object(osa, "_tab_for_seller", return_value=(tab, False)), \
            mock.patch("time.sleep", new=lambda *_a, **_k: None), \
            mock.patch("time.time", new=_now), \
            mock.patch("sys.stdin", _fake_stdin(tty=True)), \
            mock.patch("builtins.input", side_effect=EOFError):
        assert osa.wait_for_seller_login(cdp, timeout_seconds=5) is False
    assert polls["n"] >= 295, f"TTY 下限应为 300s，实际只轮询了 {polls['n']} 次"
