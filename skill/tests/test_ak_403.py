#!/usr/bin/env python3
"""AK 403 自动检测单测（T6a：HTTP 403 / 业务 msgCode 403 → 自动刷新 AK → 重试一次）。

覆盖:
  - `_post_1688` HTTP 分支: 403 → `_try_refresh_ak()` 一次；成功 → `_with_retry` 重试；
    失败 → AkAuthError（含 get_ak 指引）
  - `_post_1688` 业务 msgCode 分支: "403" → 同样 refresh→retry→expired
  - cloud_probe `_search_1688_with_fallback`: AkAuthError 必须上抛（不再静默吞掉 → []）；
    通用 Exception 仍降级返回 []

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_ak_403.py -q
    cd skill && .venv314/bin/python tests/test_ak_403.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.lib.ak_1688_client  # noqa: F401  isort:skip
from scripts.cloud_probe import _search_1688_with_fallback  # noqa: E402
from scripts.lib.ak_1688_client import (  # noqa: E402
    AkAuthError,
    _post_1688,
)


# ── helpers ────────────────────────────────────────────────────────────


def _http_error_resp(status: int) -> mock.Mock:
    """构造 raise_for_status() 抛 HTTPError(status) 的响应 mock。"""
    err = requests.exceptions.HTTPError(f"HTTP {status}")
    resp = mock.Mock()
    resp.status_code = status
    err.response = resp
    resp.raise_for_status.side_effect = err
    return resp


def _json_resp(payload: dict) -> mock.Mock:
    """构造 200 且 json() 返回 payload 的响应 mock。"""
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _post_mocks():
    """签名 + 重试休眠的公共 mock（_post_1688 内部不触碰真实 AK/网络延时）。"""
    return [
        mock.patch("scripts.lib.ak_1688_client._signature_headers", return_value={}),
        mock.patch("scripts.lib.ak_1688_client.time.sleep"),
    ]


# ═══════════════════════════════════════════════════════════════════════
# HTTP 403 分支
# ═══════════════════════════════════════════════════════════════════════

def test_http_403_refresh_ok_retries_once():
    """HTTP 403 → 刷新 AK 成功 → _with_retry 用新 AK 重试 → 最终成功。

    requests.post 调 2 次，_try_refresh_ak 恰 1 次。
    """
    with mock.patch("requests.post", side_effect=[_http_error_resp(403), _json_resp({"success": True})]) as post, \
         mock.patch("scripts.lib.ak_1688_client._try_refresh_ak", return_value=True) as refresh, \
         mock.patch("scripts.lib.ak_1688_client.time.sleep"), \
         mock.patch("scripts.lib.ak_1688_client._signature_headers", return_value={}):
        result = _post_1688("/api/find_product/1.0.0", {"q": "test"})

    assert result["success"] is True
    assert post.call_count == 2, f"403 刷新后应重试一次，实际 {post.call_count} 次"
    assert refresh.call_count == 1, f"_try_refresh_ak 应只调 1 次，实际 {refresh.call_count} 次"


def test_http_403_refresh_fail_raises_akauth():
    """HTTP 403 → 刷新 AK 失败 → 抛 AkAuthError（含 get_ak 指引），不再重试。"""
    with mock.patch("requests.post", side_effect=[_http_error_resp(403)]) as post, \
         mock.patch("scripts.lib.ak_1688_client._try_refresh_ak", return_value=False) as refresh, \
         mock.patch("scripts.lib.ak_1688_client.time.sleep"), \
         mock.patch("scripts.lib.ak_1688_client._signature_headers", return_value={}):
        with pytest.raises(AkAuthError) as exc_info:
            _post_1688("/api/find_product/1.0.0", {"q": "test"})

    assert "get_ak" in str(exc_info.value), "AkAuthError 应包含 get_ak 获取指引"
    assert post.call_count == 1, "刷新失败不应重试"
    assert refresh.call_count == 1


# ═══════════════════════════════════════════════════════════════════════
# 业务 msgCode=403 分支
# ═══════════════════════════════════════════════════════════════════════

def test_business_403_refresh_ok_retries_once():
    """HTTP 200 但业务 msgCode="403" → 刷新成功 → 重试 → 成功。"""
    resp_403 = _json_resp({"success": False, "msgCode": "403", "msgInfo": "无权限"})
    resp_ok = _json_resp({"success": True})
    with mock.patch("requests.post", side_effect=[resp_403, resp_ok]) as post, \
         mock.patch("scripts.lib.ak_1688_client._try_refresh_ak", return_value=True) as refresh, \
         mock.patch("scripts.lib.ak_1688_client.time.sleep"), \
         mock.patch("scripts.lib.ak_1688_client._signature_headers", return_value={}):
        result = _post_1688("/api/find_product/1.0.0", {"q": "test"})

    assert result["success"] is True
    assert post.call_count == 2, f"msgCode 403 刷新后应重试一次，实际 {post.call_count} 次"
    assert refresh.call_count == 1


def test_business_403_refresh_fail_raises_akauth():
    """HTTP 200 但业务 msgCode="403" → 刷新失败 → 抛 AkAuthError（含 get_ak 指引）。"""
    resp_403 = _json_resp({"success": False, "msgCode": "403", "msgInfo": "无权限"})
    with mock.patch("requests.post", side_effect=[resp_403]) as post, \
         mock.patch("scripts.lib.ak_1688_client._try_refresh_ak", return_value=False) as refresh, \
         mock.patch("scripts.lib.ak_1688_client.time.sleep"), \
         mock.patch("scripts.lib.ak_1688_client._signature_headers", return_value={}):
        with pytest.raises(AkAuthError) as exc_info:
            _post_1688("/api/find_product/1.0.0", {"q": "test"})

    assert "get_ak" in str(exc_info.value)
    assert post.call_count == 1, "刷新失败不应重试"
    assert refresh.call_count == 1


# ═══════════════════════════════════════════════════════════════════════
# cloud_probe 不再静默吞掉 AkAuthError
# ═══════════════════════════════════════════════════════════════════════

def test_fallback_akauth_propagates():
    """_search_1688_with_fallback: search_products 抛 AkAuthError → 上抛，不得降级为 []。"""
    with mock.patch("scripts.lib.ak_1688_client.search_products",
                    side_effect=AkAuthError("1688 AK 已过期或无效")):
        with pytest.raises(AkAuthError):
            _search_1688_with_fallback("宠物饮水机")


def test_fallback_generic_exception_still_empty():
    """_search_1688_with_fallback: 通用异常（ValueError）仍降级返回 []（原行为不变）。"""
    with mock.patch("scripts.lib.ak_1688_client.search_products",
                    side_effect=ValueError("boom")):
        result = _search_1688_with_fallback("宠物饮水机")

    assert result == []


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
