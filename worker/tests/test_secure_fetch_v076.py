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
    calls = []

    class FakeResp:
        status_code = 302
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        # 注：相对 brief 原稿的最小修正——补 requests.Response 的重定向判定属性，
        # 否则 safe_fetch 内 `resp.is_redirect` 直接 AttributeError。
        is_redirect = True
        is_permanent_redirect = False

    def counting_request(*a, **k):
        calls.append((a, k))
        return FakeResp()

    monkeypatch.setattr(sf.requests, "request", counting_request)
    with pytest.raises(UnsafeUrlError):
        sf.safe_fetch("http://jump.example/start")
    # T13 评审：计数断言 ==1——第二跳在地址校验即拦（169.254.169.254 被拒），
    # 区分「地址拦截」与「重定向次数耗尽」两种 UnsafeUrlError 成因。
    assert len(calls) == 1


class _Resp:
    """带 is_redirect 判定的假响应（按 status 派生，贴近 requests.Response 语义）。"""
    def __init__(self, status, headers):
        self.status_code = status
        self.headers = headers
        self.is_redirect = status in (301, 302, 303, 307, 308)
        self.is_permanent_redirect = status in (301, 308)


def test_redirect_cross_host_strips_credentials(monkeypatch):
    # T13 评审 Advisory 主用例：跨 host 重定向绝不能重放 Authorization/Cookie/auth/cookies
    import utils.secure_fetch as sf
    _fake_dns(monkeypatch, {"start.example": "93.184.216.34",
                            "elsewhere.example": "93.184.216.34"})
    seen = []

    def fake_request(method, url, **kw):
        seen.append({"url": url, "headers": dict(kw.get("headers") or {}), "kw": kw})
        if url == "http://start.example/start":
            return _Resp(302, {"Location": "http://elsewhere.example/landing"})
        return _Resp(200, {})

    monkeypatch.setattr(sf.requests, "request", fake_request)
    resp = sf.safe_fetch("http://start.example/start",
                         headers={"Authorization": "Bearer secret", "Cookie": "sid=1",
                                  "X-Keep": "yes"},
                         auth=("user", "pass"), cookies={"session": "abc"})
    assert resp.status_code == 200
    assert seen[0]["url"] == "http://start.example/start"
    assert seen[0]["headers"]["Authorization"] == "Bearer secret"   # 第一跳凭据在场
    assert "auth" in seen[0]["kw"] and "cookies" in seen[0]["kw"]
    assert seen[1]["url"] == "http://elsewhere.example/landing"     # 跳转 URL 正确
    assert "Authorization" not in seen[1]["headers"]                # 第二跳剥除
    assert "Cookie" not in seen[1]["headers"]
    assert "auth" not in seen[1]["kw"] and "cookies" not in seen[1]["kw"]
    assert seen[1]["headers"]["X-Keep"] == "yes"                    # 非凭据 header 保留


def test_redirect_same_host_keeps_credentials(monkeypatch):
    # 同 host 跳保持原样——剥除只针对跨 host（对齐 requests 原生语义的剥除最小面）
    import utils.secure_fetch as sf
    _fake_dns(monkeypatch, {"stay.example": "93.184.216.34"})
    seen = []

    def fake_request(method, url, **kw):
        seen.append({"url": url, "headers": dict(kw.get("headers") or {}), "kw": kw})
        if url.endswith("/start"):
            return _Resp(302, {"Location": "http://stay.example/next"})
        return _Resp(200, {})

    monkeypatch.setattr(sf.requests, "request", fake_request)
    sf.safe_fetch("http://stay.example/start",
                  headers={"Authorization": "Bearer keep"})
    assert len(seen) == 2
    assert seen[1]["url"] == "http://stay.example/next"
    assert seen[1]["headers"]["Authorization"] == "Bearer keep"     # 同 host 不剥


def test_redirect_scheme_change_strips_credentials(monkeypatch):
    # 评审 r2 ①：同 host http→https 302——判定收紧为 (scheme, hostname, 有效端口)
    # 三元组（对齐 requests rebuild_auth 全口径），跨 scheme 跳凭据必剥
    import utils.secure_fetch as sf
    _fake_dns(monkeypatch, {"stay.example": "93.184.216.34"})
    seen = []

    def fake_request(method, url, **kw):
        seen.append({"url": url, "headers": dict(kw.get("headers") or {}), "kw": kw})
        if url.endswith("/start"):
            return _Resp(302, {"Location": "https://stay.example/secure"})
        return _Resp(200, {})

    monkeypatch.setattr(sf.requests, "request", fake_request)
    sf.safe_fetch("http://stay.example/start",
                  headers={"Authorization": "Bearer nope"})
    assert len(seen) == 2
    assert seen[1]["url"] == "https://stay.example/secure"
    assert "Authorization" not in seen[1]["headers"]                # 跨 scheme 即剥


def test_null_byte_host_fail_closed(monkeypatch):
    # 评审 r2 ②：Linux CPython 对 null 字节 host 抛 ValueError("embedded null byte")，
    # 非 UnicodeError 子类——except 需并 ValueError 收口为 UnsafeUrlError。
    # 本机（macOS）getaddrinfo 对 null 字节不抛，monkeypatch 模拟 Linux 行为。
    import utils.secure_fetch as sf

    def _linux_getaddrinfo(host, port, *a, **k):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(sf.socket, "getaddrinfo", _linux_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("http://exa\x00mple.com/x")


def test_malformed_idn_host_fail_closed():
    # T13 评审：非 ASCII 畸形 IDN（label>63）在 CPython 纯 Python 层 idna 编码即抛
    # UnicodeEncodeError（UnicodeError 子类，不出网）——必须收口为 UnsafeUrlError，
    # 绝不允许裸异常逃出 fail-closed 判定。
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("http://" + "日" * 64 + ".com/x")


def test_public_ip_ok(monkeypatch):
    _fake_dns(monkeypatch, {"ok.example": "93.184.216.34"})
    assert_safe_remote_url("http://ok.example/x")
