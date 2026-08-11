#!/usr/bin/env python3
"""P5: enrich_product_with_cdp 外部 CDP 连接复用回归测试（TDD）。

背景：`enrich_product_with_cdp` 内部自己走 `_resolve_browser_session` + 登录等待，
在无浏览器环境（服务器/CI）必然失败。P5 新增可选 `cdp` 参数：调用方已有外部
CdpConnection 时直接传入复用，跳过整段会话解析/登录等待/CDP 可用性检查。

本测试锁定：
- 传入 cdp → `_resolve_browser_session`/登录等待/CDP 可用性检查全部跳过
  （patch side_effect=AssertionError，被调用即失败）；
- probe 路径收到同一个 cdp 连接；
- cdp=None（默认）→ 既有行为不变（仍走 `_resolve_browser_session`）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_enrich_reuse_conn.py -q
    cd skill && .venv314/bin/python tests/test_enrich_reuse_conn.py
"""
from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 预导入 service：模块体依赖 scripts._const 创建真实 data/ 目录（与
# test_login_misjudge.py 同款——mock 上下文内导入抛 FileExistsError）。
import scripts.capabilities.browser_probe.service  # noqa: F401  isort:skip

DETAIL_URL = "https://detail.1688.com/offer/980815374096.html"


def _fake_cdp_conn():
    """P5 假外部连接：cdp_url + find_tab/new_tab Mock（模拟 CdpConnection 接口）。"""
    conn = mock.Mock()
    conn.cdp_url = "http://127.0.0.1:9222"
    conn.find_tab.return_value = None
    conn.new_tab.return_value = mock.Mock(_closed=False)
    return conn


def _probe_ok_result():
    return {
        "ok": True,
        "degraded": False,
        "data": {
            "title": "宠物自动饮水器",
            "price": "5.50",
            "brand": "",
            "seller": "",
            "images": ["http://img/1.jpg"],
            "weight_grams": 200,
            "packaging_rows": [],
            "shipping": {},
            "description": "",
            "sku_details": [],
            "attributes": [],
            "option_groups": [],
        },
    }


def test_enrich_external_cdp_skips_session_resolution():
    """传入 cdp → 会话解析/登录等待/CDP 检查全部跳过，probe 收到同一 cdp。"""
    from scripts.lib.ak_1688_client import enrich_product_with_cdp

    conn = _fake_cdp_conn()
    with ExitStack() as stack:
        stack.enter_context(mock.patch("scripts.lib.config_store._require_auth"))
        stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service._resolve_browser_session",
            side_effect=AssertionError("外部 cdp 连接不应触发 _resolve_browser_session"),
        ))
        stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service._wait_for_login_session",
            side_effect=AssertionError("外部 cdp 连接不应触发登录等待"),
        ))
        stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service.check_cdp_prerequisites",
            side_effect=AssertionError("外部 cdp 连接不应触发 check_cdp_prerequisites"),
        ))
        m_probe = stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service.probe_1688_page_safe",
            return_value=_probe_ok_result(),
        ))
        result = enrich_product_with_cdp(
            DETAIL_URL,
            api_data={"title": "宠物自动饮水器", "images": []},
            timeout_seconds=30,
            cdp=conn,
        )

    assert result["ok"] is True
    assert result["source"] == "api+cdp"
    assert m_probe.call_count == 1, f"probe 应被调用一次，实际 {m_probe.call_count}"
    call_kwargs = m_probe.call_args.kwargs
    assert call_kwargs.get("cdp") is conn, f"probe 应收到外部 cdp 连接，实际 {call_kwargs.get('cdp')!r}"
    assert call_kwargs.get("timeout_seconds") == 30


def test_enrich_external_cdp_probe_uses_passed_connection():
    """外部 cdp 连接直接用于探测（find_tab/new_tab 均在该连接上调用）。"""
    from scripts.lib.ak_1688_client import enrich_product_with_cdp

    conn = _fake_cdp_conn()
    with ExitStack() as stack:
        stack.enter_context(mock.patch("scripts.lib.config_store._require_auth"))
        stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service._resolve_browser_session",
            side_effect=AssertionError("外部 cdp 连接不应触发 _resolve_browser_session"),
        ))
        stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service.check_cdp_prerequisites",
            side_effect=AssertionError("外部 cdp 连接不应触发 check_cdp_prerequisites"),
        ))
        m_probe = stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service.probe_1688_page_safe",
            return_value=_probe_ok_result(),
        ))
        enrich_product_with_cdp(
            DETAIL_URL,
            api_data={"title": "宠物自动饮水器", "images": []},
            timeout_seconds=30,
            cdp=conn,
        )

    assert m_probe.call_args.kwargs.get("cdp") is conn
    # 同一连接对象透传到底层 probe（而非新建连接）——接口契约不变
    assert m_probe.call_args.args == (DETAIL_URL,)


def test_enrich_without_cdp_keeps_existing_behavior():
    """cdp=None（默认）→ 既有行为不变：仍走 _resolve_browser_session。"""
    from scripts.lib.ak_1688_client import enrich_product_with_cdp

    session = {"cdp_url": "http://127.0.0.1:9222", "login_detected": True}
    with ExitStack() as stack:
        stack.enter_context(mock.patch("scripts.lib.config_store._require_auth"))
        stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service.check_cdp_prerequisites",
            return_value={"browser_available": True, "login_required": False,
                          "issues": [], "suggestions": []},
        ))
        m_resolve = stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service._resolve_browser_session",
            return_value=session,
        ))
        stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service._cdp_available",
            return_value=True,
        ))
        m_probe = stack.enter_context(mock.patch(
            "scripts.capabilities.browser_probe.service.probe_1688_page_safe",
            return_value=_probe_ok_result(),
        ))
        result = enrich_product_with_cdp(
            DETAIL_URL,
            api_data={"title": "宠物自动饮水器", "images": []},
            timeout_seconds=30,
        )

    assert m_resolve.call_count == 1, "cdp=None 时应照常解析浏览器会话"
    assert result["ok"] is True
    assert m_probe.call_args.kwargs.get("cdp") is None


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
