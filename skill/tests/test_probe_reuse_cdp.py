#!/usr/bin/env python3
"""P5: probe_1688_page / probe_1688_page_safe 外部 CDP 连接复用回归测试（TDD）。

背景：`probe_1688_page` 内部自行 `_resolve_browser_session` + `_connect_existing_chrome`。
P5 新增可选 `cdp` 参数：调用方已有外部 CdpConnection 时直接传入复用，跳过
浏览器查找/会话解析/登录检查/连接建立，用传入连接 find_tab/new_tab。

本测试锁定：
- 传入 cdp → `_connect_existing_chrome`/`_resolve_browser_session`/
  `find_browser_executable`/`_wait_for_login_session` 全部跳过
  （patch side_effect=AssertionError，被调用即失败）；
- 探测 tab 在传入连接上获取（find_tab / new_tab）；
- 外部连接**不被关闭**（调用方所有，finally 只关自建 tab）；
- cdp=None（默认）→ 既有行为不变（仍走 `_connect_existing_chrome`）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_probe_reuse_cdp.py -q
    cd skill && .venv314/bin/python tests/test_probe_reuse_cdp.py
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 预导入 service（同 test_login_misjudge.py）。
import scripts.capabilities.browser_probe.service  # noqa: F401  isort:skip

URL = "https://detail.1688.com/offer/980815374096.html"


def _probe_data():
    return {
        "ready": True,
        "images": ["http://img/1.jpg"],
        "title": "宠物自动饮水器",
    }


def _fake_conn():
    """P5 假外部连接：cdp_url + find_tab/new_tab Mock。"""
    conn = mock.Mock()
    conn.cdp_url = "http://127.0.0.1:9222"
    conn.find_tab.return_value = None
    tab = mock.Mock(_closed=False)
    conn.new_tab.return_value = tab
    return conn


def _enter(mocks):
    stack = ExitStack()
    for m in mocks:
        stack.enter_context(m)
    return stack


def _probe_mocks(conn):
    """外部 cdp 路径通用 mock：cache 全 miss + 浏览器/会话全拦截 + 探测子步骤受控。"""
    tmp = Path(tempfile.mkdtemp(prefix="probe_reuse_"))
    return [
        mock.patch("scripts.lib.cache.cache_get", return_value=None),
        mock.patch("scripts.lib.cache.cache_set", return_value=None),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_find_cached_probe",
            return_value=None,
        ),
        mock.patch("time.sleep", return_value=None),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "find_browser_executable",
            side_effect=AssertionError("外部 cdp 连接不应触发 find_browser_executable"),
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_resolve_browser_session",
            side_effect=AssertionError("外部 cdp 连接不应触发 _resolve_browser_session"),
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_wait_for_login_session",
            side_effect=AssertionError("外部 cdp 连接不应触发 _wait_for_login_session"),
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_check_1688_login_live",
            side_effect=AssertionError("外部 cdp 连接不应触发 _check_1688_login_live"),
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_connect_existing_chrome",
            side_effect=AssertionError("外部 cdp 连接不应触发 _connect_existing_chrome"),
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_artifact_path",
            return_value=tmp / "probe.json",
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "current_task_id",
            return_value="t1",
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_extract_offer_id",
            return_value="980815374096",
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service,
            "_open_target_page_in_existing_browser",
            return_value=_fake_conn().new_tab.return_value,
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service,
            "_probe_opened_target_page_with_retries",
            return_value=_probe_data(),
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_filter_probe_images",
            side_effect=lambda imgs: list(imgs),
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_build_summary",
            return_value={"ok": True},
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_looks_like_failure_page",
            return_value=False,
        ),
        mock.patch.object(
            scripts.capabilities.browser_probe.service, "_looks_like_captcha_intercept",
            return_value=False,
        ),
    ]


def test_probe_external_cdp_skips_connect_and_uses_passed_conn():
    """probe_1688_page(url, cdp=conn) → 跳过连接建立/会话解析，探测走传入连接。"""
    from scripts.capabilities.browser_probe import service as svc

    conn = _fake_conn()
    with _enter(_probe_mocks(conn)):
        result = svc.probe_1688_page(URL, cdp=conn, timeout_seconds=30)

    assert result.get("ready") is True
    # 探测 tab 通过传入连接的 find_tab/new_tab 获取
    assert conn.find_tab.call_count >= 1, "应在传入连接上 find_tab"
    # 外部连接归调用方所有——绝不能在这里被关闭
    conn.close.assert_not_called()


def test_probe_external_cdp_does_not_close_caller_connection_on_error():
    """外部连接在探测异常时也保持打开（finally 只关自建 tab）。"""
    from scripts.capabilities.browser_probe import service as svc

    conn = _fake_conn()
    with _enter(_probe_mocks(conn)):
        # 模拟探测阶段抛异常
        with mock.patch.object(
            svc, "_probe_opened_target_page_with_retries",
            side_effect=RuntimeError("probe boom"),
        ):
            try:
                svc.probe_1688_page(URL, cdp=conn, timeout_seconds=30)
                raise AssertionError("探测异常应向上抛出")
            except Exception as exc:
                assert "probe boom" in str(exc)
    conn.close.assert_not_called()


def test_probe_without_cdp_keeps_connect_existing_chrome():
    """cdp=None（默认）→ 既有行为不变：仍走 _connect_existing_chrome。"""
    from scripts.capabilities.browser_probe import service as svc

    tmp = Path(tempfile.mkdtemp(prefix="probe_reuse_"))
    cdp = _fake_conn()
    tab = cdp.new_tab.return_value
    with _enter([
        mock.patch("scripts.lib.cache.cache_get", return_value=None),
        mock.patch("scripts.lib.cache.cache_set", return_value=None),
        mock.patch.object(svc, "_find_cached_probe", return_value=None),
        mock.patch("time.sleep", return_value=None),
        mock.patch.object(svc, "find_browser_executable", return_value="/fake/chrome"),
        mock.patch.object(svc, "get_config_profile", return_value="default"),
        mock.patch.object(svc, "_profile_dir", return_value=tmp),
        mock.patch.object(svc, "_artifact_path", return_value=tmp / "probe.json"),
        mock.patch.object(svc, "current_task_id", return_value="t1"),
        mock.patch.object(svc, "_resolve_browser_session",
                          return_value={"cdp_url": "http://127.0.0.1:9222",
                                        "login_detected": True}),
        mock.patch.object(svc, "_cdp_available", return_value=True),
        mock.patch.object(svc, "_connect_existing_chrome", return_value=(cdp, True)),
        mock.patch.object(svc, "_extract_offer_id", return_value="980815374096"),
        mock.patch.object(svc, "_open_target_page_in_existing_browser", return_value=tab),
        mock.patch.object(svc, "_probe_opened_target_page_with_retries",
                          return_value=_probe_data()),
        mock.patch.object(svc, "_filter_probe_images", side_effect=lambda imgs: list(imgs)),
        mock.patch.object(svc, "_build_summary", return_value={"ok": True}),
        mock.patch.object(svc, "_looks_like_failure_page", return_value=False),
        mock.patch.object(svc, "_looks_like_captcha_intercept", return_value=False),
    ]):
        result = svc.probe_1688_page(URL, timeout_seconds=30)

    assert result.get("ready") is True
    # cdp=None 时连接为自建 → finally 会关闭（既有行为，未被本改动破坏）
    cdp.close.assert_called()


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
