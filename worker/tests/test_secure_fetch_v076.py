# worker/tests/test_secure_fetch_v076.py
"""v0.76 T13(inj-C1): SSRF 防线核心——解析 IP 校验 + 域名精确匹配 + 逐跳重定向复核。"""
import pytest
from utils.secure_fetch import assert_safe_remote_url, UnsafeUrlError


def _fake_dns(monkeypatch, mapping):
    # 注：相对 brief 原稿的最小修正——mapping 外主机若本身是字面 IP 则原样直通
    # （对齐真实 getaddrinfo 对字面 IP 不走 DNS 的语义），否则回公共 IP。
    # 原稿一律回公共 IP 会把 `http://[fd00::1]/x` 洗成公网地址导致漏拦（DID NOT RAISE）。
    import ipaddress
    import utils.secure_fetch as sf

    def _resolve(host, port, *a, **k):
        if host in mapping:
            return [(2, 1, 6, "", (mapping[host], port))]
        try:
            ipaddress.ip_address(host)
            return [(2, 1, 6, "", (host, port))]
        except ValueError:
            return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", _resolve)


def test_loopback_blocked():
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("http://127.0.0.1:8080/x")


def test_metadata_ip_blocked(monkeypatch):
    _fake_dns(monkeypatch, {"evil.example": "169.254.169.254"})
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("http://evil.example/latest/meta-data/")


def test_private_v4_and_v6_blocked(monkeypatch):
    _fake_dns(monkeypatch, {"a.example": "10.1.2.3", "b.example": "fd00::1"})
    for u in ("http://a.example/x", "http://[fd00::1]/x"):
        with pytest.raises(UnsafeUrlError):
            assert_safe_remote_url(u)


def test_scheme_restricted():
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("file:///etc/passwd")


def test_allowlist_exact_host_not_substring(monkeypatch):
    # 域名白名单按 hostname 精确/后缀匹配——query 垫片失效（inj-H1 根因）
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("http://127.0.0.1:8080/admin?pad=alicdn.com/img/x.jpg",
                               allowed_host_suffixes=("alicdn.com",))
    _fake_dns(monkeypatch, {"img.alicdn.com": "93.184.216.34"})
    assert_safe_remote_url("https://img.alicdn.com/img/x.jpg",
                           allowed_host_suffixes=("alicdn.com",))   # 子域后缀匹配通过


def test_redirect_hop_revalidated(monkeypatch):
    import utils.secure_fetch as sf
    _fake_dns(monkeypatch, {"jump.example": "93.184.216.34"})
    class FakeResp:
        status_code = 302
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        # 注：相对 brief 原稿的最小修正——补 requests.Response 的重定向判定属性，
        # 否则 safe_fetch 内 `resp.is_redirect` 直接 AttributeError。
        is_redirect = True
        is_permanent_redirect = False
    monkeypatch.setattr(sf.requests, "request", lambda *a, **k: FakeResp())
    with pytest.raises(UnsafeUrlError):
        sf.safe_fetch("http://jump.example/start")


def test_public_ip_ok(monkeypatch):
    _fake_dns(monkeypatch, {"ok.example": "93.184.216.34"})
    assert_safe_remote_url("http://ok.example/x")
