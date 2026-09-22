# worker/tests/test_image_url_guard_v076.py
"""v0.76 T15(inj-H1): 图床白名单按 hostname 精确/后缀匹配——query 垫片失效。

审计实证：旧实现 `any(dom in lowered)` 子串匹配可被 `http://127.0.0.1:8080/?pad=
alicdn.com` 垫片绕过——内网 URL 借 query 参数冒充图床域直通 E1 转存/生图参考
白名单。改 hostname 精确/后缀匹配后垫片失效；webp 拒绝与缩略后缀判定仍在
lowered 全串上，语义不变。

同文件后半（TestSalvageSafeFetchWiring）：controller 追加范围——E1 转存链
（cos_uploader.salvage_original_images）对用户 URL 的裸 requests.get 接
utils.secure_fetch.safe_fetch（白名单过后的解析 IP 校验 + 逐跳复核）。

测试纪律：全程 monkeypatch 假 HTTP/假 DNS（对齐 T13/T14 手法），零真实出站。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.image_url_guard import is_product_image_candidate  # noqa: E402


def test_query_pad_bypass_blocked():
    assert not is_product_image_candidate("http://127.0.0.1:8080/admin?pad=alicdn.com/img/ibank/x.jpg")
    assert not is_product_image_candidate("http://evil.example/pad=1688.com/x.jpg")


def test_real_bed_domains_pass():
    assert is_product_image_candidate("https://img.alicdn.com/imgextra/x.jpg")
    assert is_product_image_candidate("https://cbu01.alicdn.com/img/ibank/y.jpg")
    assert is_product_image_candidate("https://img.1688.com/kf/z.jpg")


def test_lookalike_domain_blocked():
    assert not is_product_image_candidate("https://alicdn.com.evil.example/x.jpg")
    assert not is_product_image_candidate("https://notalicdn.com/x.jpg")


# ── controller 追加范围：E1 转存链接 safe_fetch ──
# salvage_original_images 消费 guard 白名单后曾对用户 URL 裸 requests.get——
# 白名单过了但域名仍可被内网 DNS 指向（解析 IP 无校验）。接线后 safe_fetch
# 补解析 IP 校验与逐跳复核；allowed_host_suffixes 双保险（重定向跳转域同锁）。

import utils.secure_fetch as sf  # noqa: E402
from utils.cos_uploader import salvage_original_images  # noqa: E402

_ALICDN_ORIGINAL = "https://cbu01.alicdn.com/img/ibank/O1CNx_!!123-0-cib.jpg"
_PUBLIC_IP = "93.184.216.34"  # 公共 IP（T13/T14 同款），不在 _BLOCKED_NETS 任一段


def _net_touch_guard(monkeypatch):
    """requests 层零触达哨兵：get/request 任一被调即计数并抛 AssertionError
    （T13/T14 同款双入口——requests.get 与 sf.requests.request 都拦）。"""
    called = {"n": 0}

    def _touch(*a, **k):
        called["n"] += 1
        raise AssertionError("network touched: salvage chain bypassed safe_fetch")

    monkeypatch.setattr("requests.get", _touch)
    monkeypatch.setattr(sf.requests, "request", _touch)
    return called


def _fake_dns(monkeypatch, ip):
    """全部 hostname 解析到指定 IP；字面 IP host 原样直通（T14 同款语义，
    防字面内网 IP 被自己的 fake 洗白）。"""
    import ipaddress

    def _resolve(host, port, *a, **k):
        try:
            ipaddress.ip_address(host)
            return [(2, 1, 6, "", (host, port))]
        except ValueError:
            return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", _resolve)


def _enable_cos(monkeypatch):
    import utils.cos_uploader as cu

    monkeypatch.setattr(cu, "cos_enabled", lambda: True)
    monkeypatch.setattr(cu, "cos_upload_bytes",
                        lambda data, key, content_type=None: f"https://cos.test/{key}")


class _FakeResp:
    """带重定向判定属性的假 200 响应（safe_fetch 会读 is_redirect）。"""

    status_code = 200
    content = b"img-bytes"
    is_redirect = False
    is_permanent_redirect = False


def test_salvage_skips_whitelisted_host_resolving_internal(monkeypatch):
    """白名单域（guard 放行）但解析到内网 IP → safe_fetch 拒绝 → 跳过不抓。"""
    called = _net_touch_guard(monkeypatch)
    _enable_cos(monkeypatch)

    def _internal(host, port, *a, **k):
        return [(2, 1, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", _internal)
    assert salvage_original_images([_ALICDN_ORIGINAL]) == []
    assert called["n"] == 0  # 请求从未发出（拦在校验层）


def test_salvage_fetches_public_whitelisted_host(monkeypatch):
    """公网图床 URL 正常抓取转存——接线不改变成功路径行为。"""
    _fake_dns(monkeypatch, _PUBLIC_IP)
    _enable_cos(monkeypatch)
    seen = {}

    def fake_request(method, url, timeout=None, headers=None, **kw):
        seen["method"], seen["url"], seen["headers"] = method, url, dict(headers or {})
        return _FakeResp()

    monkeypatch.setattr(sf.requests, "request", fake_request)
    out = salvage_original_images([_ALICDN_ORIGINAL])
    assert out and out[0].startswith("https://cos.test/ozon-1688/salvage/")
    assert seen["method"] == "GET"
    assert seen["url"] == _ALICDN_ORIGINAL
    assert seen["headers"]["Referer"] == "https://detail.1688.com/"  # Referer 分派保留
    assert "User-Agent" in seen["headers"]


def test_salvage_blocks_redirect_to_non_whitelisted_host(monkeypatch):
    """白名单域 302 跳非白名单公网域 → 第二跳 allowlist 复核拦截（双保险）。"""
    _fake_dns(monkeypatch, _PUBLIC_IP)
    _enable_cos(monkeypatch)
    calls = {"n": 0}

    class RedirectResp:
        status_code = 302
        headers = {"Location": "https://cdn.example.com/evil.jpg"}
        is_redirect = True
        is_permanent_redirect = False

    def fake_request(method, url, timeout=None, headers=None, **kw):
        calls["n"] += 1
        return RedirectResp()

    monkeypatch.setattr(sf.requests, "request", fake_request)
    assert salvage_original_images([_ALICDN_ORIGINAL]) == []
    assert calls["n"] == 1  # 第二跳未发出（allowlist 拦在请求前）


def test_salvage_skips_malformed_port_without_raise(monkeypatch):
    """畸形端口（guard 只读 hostname 放行，safe_fetch 读 port 抛裸 ValueError）
    → 宽 except 兜住，跳过该图不炸整批。"""
    called = _net_touch_guard(monkeypatch)
    _enable_cos(monkeypatch)
    bad = "http://img.alicdn.com:70000/x.jpg"
    assert is_product_image_candidate(bad) is True  # guard 侧 hostname 合法
    assert salvage_original_images([bad]) == []
    assert called["n"] == 0
