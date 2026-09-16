# -*- coding: utf-8 -*-
"""ISSUE-4（report 22e45744）：check seller 假阳性根治——会话活性真探针。

背景：check_seller_login 只判 sc_company_id cookie 存在（silent-first），
而 __Secure-access_token 是分钟级寿命/用后轮换型（v0.74 实机结论）——
死会话下 check 报「已登录」、what_to_sell 全 401、选品运营列全空。
probe_seller_session_alive 发最小真实请求按 HTTP 状态分类：
  200/400 → alive=True；401 → alive=False(unauthenticated)；
  403 → alive=False(forbidden_or_challenge)；其余/异常 → alive=None。

锁定：
1. 状态码分流矩阵（200/400/401/403/500/网络异常/无公司 cookie）。
2. 红线：响应体（含挑战串）绝不进返回值；返回键恒 {alive, http_status, reason}。
3. 副作用零：不触发 _mark_direct_blocked（check 不改变 discover 直调/CDP 路径）。
4. cookies 显式传入时不再经 CDP 取（_fetch 零调用）。

全部 mock：不发真网络请求、不连 CDP、不写缓存。
运行: cd skill && .venv314/bin/python -m pytest tests/test_seller_session_probe.py -q
"""
from __future__ import annotations

import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import ozon_seller_analytics as osa  # noqa: E402


_GOOD_COOKIES = {"sc_company_id": "12345", "__Secure-access_token": "dead"}


class _FakeResp:
    def __init__(self, status_code: int, body: str = "{}"):
        self.status_code = status_code
        self.text = body

    def json(self):
        raise ValueError("not json in tests")


def _probe_with_status(status_code: int, body: str = "{}",
                       cookies: dict | None = _GOOD_COOKIES) -> dict:
    with mock.patch.object(osa, "requests") as fake_requests, \
         mock.patch.object(osa, "_fetch_seller_session_cookies",
                           return_value=dict(_GOOD_COOKIES)) as fake_fetch:
        fake_requests.post.return_value = _FakeResp(status_code, body)
        result = osa.probe_seller_session_alive(cookies=cookies)
    if cookies is not None:
        fake_fetch.assert_not_called(), "显式 cookies 时不得再走 CDP 取"
    return result


def test_200_session_alive():
    r = _probe_with_status(200)
    assert r == {"alive": True, "http_status": 200, "reason": "ok"}


def test_400_treated_alive_auth_layer_passed():
    """空 sku 最小体可能被参数层拒——鉴权已过即会话可用（与 token 无关）。"""
    r = _probe_with_status(400)
    assert r["alive"] is True and r["http_status"] == 400


def test_401_dead_session_unauthenticated():
    r = _probe_with_status(401)
    assert r == {"alive": False, "http_status": 401,
                 "reason": "unauthenticated"}


def test_403_dead_or_challenge():
    r = _probe_with_status(403)
    assert r == {"alive": False, "http_status": 403,
                 "reason": "forbidden_or_challenge"}


def test_500_undetermined_not_false_positive():
    r = _probe_with_status(500)
    assert r["alive"] is None and r["reason"] == "unknown"


def test_network_error_undetermined():
    with mock.patch.object(osa, "requests") as fake_requests, \
         mock.patch.object(osa, "_fetch_seller_session_cookies",
                           return_value=dict(_GOOD_COOKIES)):
        fake_requests.post.side_effect = ConnectionError("net down")
        r = osa.probe_seller_session_alive()
    assert r == {"alive": None, "http_status": None, "reason": "network_error"}


def test_no_company_cookie_undetermined():
    with mock.patch.object(osa, "_fetch_seller_session_cookies",
                           return_value={"other": "x"}):
        r = osa.probe_seller_session_alive()
    assert r == {"alive": None, "http_status": None,
                 "reason": "no_company_cookie"}


def test_response_body_never_leaks_into_result():
    """红线：响应体（DataDome 挑战串等）绝不进返回值。"""
    secret = "fab_chlg=SECRETCHALLENGE"
    r = _probe_with_status(403, body=f'{{"challenge":"{secret}"}}')
    assert secret not in str(r)
    assert set(r) == {"alive", "http_status", "reason"}


def test_403_challenge_does_not_mark_direct_blocked():
    """副作用零：探针不触发直调短路标记（check 不改变 discover 路径选择）。"""
    with mock.patch.object(osa, "requests") as fake_requests, \
         mock.patch.object(osa, "_fetch_seller_session_cookies",
                           return_value=dict(_GOOD_COOKIES)), \
         mock.patch.object(osa, "_mark_direct_blocked") as fake_mark:
        fake_requests.post.return_value = _FakeResp(
            403, '{"fab_chlg":"x","challengeURL":"https://x"}')
        osa.probe_seller_session_alive()
    fake_mark.assert_not_called()


def test_cookies_none_fetches_via_cdp():
    """cookies 缺省 → 自行经 CDP 静默取（不导航）。"""
    with mock.patch.object(osa, "requests") as fake_requests, \
         mock.patch.object(osa, "_fetch_seller_session_cookies",
                           return_value=dict(_GOOD_COOKIES)) as fake_fetch:
        fake_requests.post.return_value = _FakeResp(200)
        r = osa.probe_seller_session_alive()
    fake_fetch.assert_called_once()
    assert r["alive"] is True
