#!/usr/bin/env python3
"""arch-findings #1: check 登录检测门控回归（纯 mock，零 Chrome/网络）。

历史 bug：`if session_ok and alibaba_cdp_ok or session_ok:` 因运算符优先级
等价于 `if session_ok:`——alibaba_cdp_ok 恒无效果（死操作数）。

按历史意图收敛为显式 `if session_ok:`（登录检测 = cookie 探针
probe_alibaba_login，不要求已开 1688 标签页，见 37ef948d 注释）。
本文件锁定该语义，防止「顺手改回 `session_ok and alibaba_cdp_ok`」把
无 1688 标签页场景的登录漏报/未登录漏拦 bug 重新引入。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_check_login_gate_v080.py -q
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402


def _run_check(login_probe_ok: bool, *, session_ok: bool):
    """全 mock 跑 cmd_check 环境诊断，返回 (返回码, 输出文本)。

    - session_ok 由 chrome_launcher.get_chrome_info 的 cdp_available 控制；
    - 1688 标签页恒不存在（requests.get 打 9222 即抛）→ alibaba_cdp_ok=False；
    - 登录探针 probe_alibaba_login 由参数控制。
    其余探测项全部给「不致命」的 mock 值（凭证缺失只影响 all_ok，不影响断言行）。
    """
    args = cli.build_arg_parser().parse_args(["check"])

    def _fake_get(url, *a, **kw):
        if "127.0.0.1:9222" in url:
            raise ConnectionError("no cdp tabs in test")
        if "/api/v1/health" in url:
            resp = mock.Mock()
            resp.status_code = 200
            resp.json = lambda: {"message": "ok", "db": "ok"}
            return resp
        raise ConnectionError(url)

    def _fake_post(url, *a, **kw):
        raise ConnectionError(url)

    chrome_info = {"chrome_found": False, "cdp_available": session_ok}
    patches = [
        mock.patch("scripts.capabilities.browser_probe.service._candidate_browser_paths",
                   return_value=[]),
        mock.patch("scripts.lib.chrome_launcher.get_chrome_info",
                   return_value=chrome_info),
        # CDP 不可用时 check 会尝试自启 Chrome——按场景给启动结果，绝不真启
        mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                   return_value=(session_ok, f"mock: session_ok={session_ok}")),
        mock.patch("requests.get", side_effect=_fake_get),
        mock.patch("requests.post", side_effect=_fake_post),
        mock.patch("scripts.lib.readiness.probe_alibaba_login",
                   return_value=login_probe_ok),
        mock.patch("scripts.lib.readiness.probe_ozon_datadome",
                   return_value=True),
        # aibuy 反爬 cookie 就绪（避免走 _open_tab 预热支路）
        mock.patch("scripts.lib.ozon_image_search._read_1688_cookies_silent",
                   return_value=True),
        # seller 探针（cookie 判过 + 会话探针活）
        mock.patch("scripts.lib.cdp_client.CdpConnection", mock.MagicMock()),
        mock.patch("scripts.lib.ozon_seller_analytics.check_seller_login",
                   return_value=True),
        mock.patch("scripts.lib.ozon_seller_analytics.probe_seller_session_alive",
                   return_value={"alive": True}),
        # 凭证全缺（只影响 all_ok 汇总，不影响登录门控行）
        mock.patch("scripts.lib.config_store.get_mxou_token", return_value=None),
        mock.patch("scripts.lib.config_store.get_ali_1688_ak", return_value=None),
        mock.patch("scripts.lib.config_store.list_stores", return_value={}),
        mock.patch("scripts.lib.config_store.check_config", return_value={}),
        mock.patch.object(cli, "_init_sentry", return_value=True),
        mock.patch("scripts._const.CLOUD_API_BASE", "http://mock-worker.test"),
    ]
    out = io.StringIO()
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_check(args)
    return rc, out.getvalue()


def test_login_reported_when_no_1688_tab_but_cookie_probe_ok():
    """①无 1688 标签页（alibaba_cdp_ok=False）+ cookie 探针已登录 → 仍报已登录。

    若门控被改回 `session_ok and alibaba_cdp_ok`，此场景整块被跳过（漏报）。
    """
    rc, out = _run_check(login_probe_ok=True, session_ok=True)
    assert "1688 已登录" in out
    assert "✅ 1688 已登录" in out
    assert "需登录 1688" not in out


def test_login_missing_reported_and_fails_all_ok_when_no_tab():
    """②无标签页 + 探针未登录 → 如实报未登录并计入 all_ok（漏拦防线）。"""
    rc, out = _run_check(login_probe_ok=False, session_ok=True)
    assert "❌ 1688 已登录" in out
    assert "需登录 1688" in out
    assert rc == 1


def test_login_block_skipped_when_cdp_down():
    """③CDP 未启动（session_ok=False）→ 登录块整体跳过（探针也无处跑）。"""
    rc, out = _run_check(login_probe_ok=False, session_ok=False)
    assert "1688 已登录" not in out


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
