#!/usr/bin/env python3
"""登录误判修复单测（Q3 _wait_for_login_session 结构化返回 + Q4 启动失败/登录超时三态区分）。

Q3: service._wait_for_login_session 返回结构化结果 {ok, session, reason}：
    - ok=True  → session = 登录成功的会话 dict（含 cdp_url/login_detected，兼容旧调用方），reason=None
    - ok=False → session = None，reason ∈ {'no_cdp', 'timeout', 'cdp_error'}
Q4: ak_1688_client.enrich_product_with_cdp 在 session_alive=False 时区分：
    - 浏览器不存在（find_browser_executable 返回 None）→ degraded_reason「未找到浏览器」
    - 浏览器存在但 CDP 会话未建立（reason=no_cdp/cdp_error）→ 「浏览器启动失败（请手动启动 Chrome 或检查 profile）」
    - CDP 可用但用户未完成扫码（reason=timeout）→ 「等待 1688 登录超时」

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_login_misjudge.py -q
    cd skill && .venv314/bin/python tests/test_login_misjudge.py
"""
from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 预导入 service：模块体依赖 scripts._const 创建真实 data/ 目录（mock 上下文内导入抛 FileExistsError）
import scripts.capabilities.browser_probe.service  # noqa: F401  isort:skip


def _enter(mocks):
    stack = ExitStack()
    for m in mocks:
        stack.enter_context(m)
    return stack


def _login_mocks(session, snapshot_url, login_required):
    """Q3 通用 mock：_wait_for_login_session 内依赖全部受控。

    - session: _resolve_browser_session 返回值（cdp_url 命中则走连接路径）
    - snapshot_url / login_required: _probe_login_snapshot 返回的页面快照参数
    """
    tab = mock.Mock()
    tab.close = mock.Mock()
    cdp = mock.Mock()
    cdp.new_tab.return_value = tab
    cdp.close = mock.Mock()
    return [
        mock.patch("scripts.capabilities.browser_probe.service._login_in_progress", False, create=True),
        mock.patch("scripts.capabilities.browser_probe.service._resolve_browser_session", return_value=session),
        mock.patch("scripts.capabilities.browser_probe.service._find_live_cdp_session_for_profile", return_value=None),
        mock.patch("scripts.capabilities.browser_probe.service._write_browser_session", return_value=None),
        mock.patch("scripts.capabilities.browser_probe.service.CdpConnection", return_value=cdp),
        mock.patch("scripts.capabilities.browser_probe.service._extract_qr_code_base64", return_value=None),
        mock.patch("scripts.capabilities.browser_probe.service._probe_login_snapshot",
                   return_value={"url": snapshot_url, "bodyText": "扫码登录"}),
        mock.patch("scripts.capabilities.browser_probe.service._snapshot_login_required", return_value=login_required),
        mock.patch("time.sleep", return_value=None),
    ]


# ═══════════════════════════════════════════════════════════════════════
# Q3: _wait_for_login_session 结构化返回
# ═══════════════════════════════════════════════════════════════════════

def test_wait_login_no_cdp_returns_reason():
    """无可用 CDP 会话 → {ok: False, reason: 'no_cdp'}（旧行为返回 None）。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    with _enter(_login_mocks(session={}, snapshot_url="https://login.1688.com/", login_required=True)):
        with mock.patch("time.time", side_effect=lambda: 0.0):
            result = _wait_for_login_session(
                "https://detail.1688.com/offer/1.html",
                profile_name="default",
                browser_path="/usr/bin/google-chrome",
                timeout_seconds=30,
            )
    assert result == {"ok": False, "session": None, "reason": "no_cdp"}


def test_wait_login_timeout_returns_reason():
    """CDP 可用但轮询超时 → {ok: False, reason: 'timeout'}（旧行为返回 None）。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    session = {"cdp_url": "http://127.0.0.1:9999", "profile": "default"}
    # 第一次 time.time()（start）返回 0，第二次（循环条件）返回 1000 → 立即超时
    time_values = iter([0.0, 1000.0])
    with _enter(_login_mocks(session=session, snapshot_url="https://login.1688.com/", login_required=True)):
        with mock.patch("time.time", side_effect=lambda: next(time_values)):
            result = _wait_for_login_session(
                "https://detail.1688.com/offer/1.html",
                profile_name="default",
                browser_path="/usr/bin/google-chrome",
                timeout_seconds=30,
            )
    assert result == {"ok": False, "session": None, "reason": "timeout"}


def test_wait_login_cdp_error_returns_reason():
    """CDP 连接/执行异常 → {ok: False, reason: 'cdp_error'}（旧行为返回 None）。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    session = {"cdp_url": "http://127.0.0.1:9999", "profile": "default"}
    mocks = _login_mocks(session=session, snapshot_url="https://login.1688.com/", login_required=True)
    mocks[5] = mock.patch("scripts.capabilities.browser_probe.service.CdpConnection",
                          side_effect=ConnectionError("CDP 连接被拒"))
    with _enter(mocks):
        with mock.patch("time.time", side_effect=lambda: 0.0):
            result = _wait_for_login_session(
                "https://detail.1688.com/offer/1.html",
                profile_name="default",
                browser_path="/usr/bin/google-chrome",
                timeout_seconds=30,
            )
    assert result == {"ok": False, "session": None, "reason": "cdp_error"}


def test_wait_login_success_returns_structured():
    """检测到登录 → {ok: True, session: <含 cdp_url/login_detected 的会话>, reason: None}。"""
    from scripts.capabilities.browser_probe.service import _wait_for_login_session
    session = {"cdp_url": "http://127.0.0.1:9999", "profile": "default"}
    with _enter(_login_mocks(session=session, snapshot_url="https://detail.1688.com/offer/1.html", login_required=False)):
        with mock.patch("time.time", side_effect=lambda: 0.0):
            result = _wait_for_login_session(
                "https://detail.1688.com/offer/1.html",
                profile_name="default",
                browser_path="/usr/bin/google-chrome",
                timeout_seconds=30,
            )
    assert result["ok"] is True
    assert result["reason"] is None
    assert result["session"] is not None
    # 兼容旧调用方：session 内 cdp_url / login_detected 可直接读取
    assert result["session"]["cdp_url"] == "http://127.0.0.1:9999"
    assert result["session"]["login_detected"] is True


# ═══════════════════════════════════════════════════════════════════════
# Q4: enrich_product_with_cdp 三态区分
# ═══════════════════════════════════════════════════════════════════════

def _enrich_mocks(find_browser, login_wait):
    """Q4 通用 mock：enrich_product_with_cdp 走到登录分支的依赖全部受控。"""
    return [
        mock.patch("scripts.lib.config_store._require_auth", return_value=None),
        mock.patch("scripts.capabilities.browser_probe.service.check_cdp_prerequisites",
                   return_value={"browser_available": True, "login_required": True,
                                 "issues": [], "suggestions": []}),
        mock.patch("scripts.capabilities.browser_probe.service._resolve_browser_session", return_value={}),
        mock.patch("scripts.capabilities.browser_probe.service._cdp_available", return_value=False),
        mock.patch("scripts.capabilities.browser_probe.service.find_browser_executable", return_value=find_browser),
        mock.patch("scripts.capabilities.browser_probe.service._wait_for_login_session", return_value=login_wait),
    ]


def _run_enrich(api_data=None):
    from scripts.lib.ak_1688_client import enrich_product_with_cdp
    return enrich_product_with_cdp(
        "https://detail.1688.com/offer/123.html",
        api_data=api_data or {"title": "测试商品", "images": []},
        timeout_seconds=30,
    )


def test_enrich_browser_missing():
    """浏览器不存在（find_browser_executable=None）→ degraded_reason「未找到浏览器」。"""
    with _enter(_enrich_mocks(find_browser=None, login_wait={})):
        result = _run_enrich()
    assert "未找到" in result["degraded_reason"] and "浏览器" in result["degraded_reason"]
    assert result["degraded"] is True


def test_enrich_launch_failure_not_login_timeout():
    """浏览器存在但 CDP 会话未建立（reason=no_cdp）→ 「浏览器启动失败」，绝不误报「登录超时」。"""
    login_wait = {"ok": False, "session": None, "reason": "no_cdp"}
    with _enter(_enrich_mocks(find_browser="/usr/bin/google-chrome", login_wait=login_wait)):
        result = _run_enrich()
    assert "浏览器启动失败" in result["degraded_reason"]
    assert "登录超时" not in result["degraded_reason"]


def test_enrich_login_timeout_kept():
    """CDP 可用但扫码超时（reason=timeout）→ 「等待 1688 登录超时」语义保持。"""
    login_wait = {"ok": False, "session": None, "reason": "timeout"}
    with _enter(_enrich_mocks(find_browser="/usr/bin/google-chrome", login_wait=login_wait)):
        result = _run_enrich()
    assert "登录超时" in result["degraded_reason"]
    assert "浏览器启动失败" not in result["degraded_reason"]


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
