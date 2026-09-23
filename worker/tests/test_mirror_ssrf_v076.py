# worker/tests/test_mirror_ssrf_v076.py
"""v0.76 T14(inj-C1): 草稿镜像链内网 URL 必须被拒——不发起任何请求。

审计背景：`_mirror_one` 曾对用户可控 URL 裸 `requests.get`（无内网防护），
响应体外带公开 COS bucket——内网全读 SSRF + 数据外带双料（探针实证）。
本文件锁新行为：镜像下载统一走 utils.secure_fetch.safe_fetch（解析 IP 校验
+ 逐跳复核），内网/保留/链路本地段拒绝后按既有降级语义返回 None（保持外链、
不阻断草稿保存）。

测试纪律：全程 monkeypatch `utils.secure_fetch.requests.request` 假 HTTP，
零真实出站（DNS 也 fake，对齐 T13 test_secure_fetch_v076 手法）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import utils.secure_fetch as sf  # noqa: E402
from services import draft_image_mirror as dim  # noqa: E402

_PUBLIC_IP = "93.184.216.34"  # 公共 IP（T13 同款），不在 _BLOCKED_NETS 任一段


def _fake_dns(monkeypatch):
    """全部 hostname 解析到公共 IP——杜绝真实 DNS 出站，测试完全封闭。

    注（对齐 T13 _fake_dns 语义）：字面 IP host 必须原样直通（真实 getaddrinfo
    对字面 IP 不走 DNS）——否则会把 `http://169.254.169.254/x` 洗成公共 IP，
    第二跳校验被自己的 fake 洗白导致漏拦（本文件 test_mirror_blocks_redirect_
    to_internal 实测踩过）。
    """
    import ipaddress

    def _resolve(host, port, *a, **k):
        try:
            ipaddress.ip_address(host)
            return [(2, 1, 6, "", (host, port))]
        except ValueError:
            return [(2, 1, 6, "", (_PUBLIC_IP, port))]

    monkeypatch.setattr(sf.socket, "getaddrinfo", _resolve)


def _net_touch_guard(monkeypatch):
    """requests 层零触达哨兵：get/request 任一被调即计数并抛 AssertionError。

    注：相对 brief 原稿的最小修正——原稿只 patch `requests.request`，而旧实现
    走 `requests.get`（不同函数对象），旧代码会真实连 127.0.0.1:18477（连接
    拒绝被 except 吞掉）→ 计数恒 0 → 测试对带洞代码假绿。两个入口都拦，
    确保红→绿真实成立。
    """
    called = {"n": 0}

    def _touch(*a, **k):
        called["n"] += 1
        raise AssertionError("network touched: mirror chain bypassed safe_fetch")

    monkeypatch.setattr("requests.get", _touch)
    monkeypatch.setattr(sf.requests, "request", _touch)
    return called


def test_mirror_rejects_loopback_zero_network(monkeypatch):
    """内网（loopback）URL → _mirror_one 返回 None 且 requests 层零触达。"""
    called = _net_touch_guard(monkeypatch)
    assert dim._mirror_one("http://127.0.0.1:18477/ssrf-poc") is None
    assert called["n"] == 0


def test_mirror_rejects_metadata_ip_zero_network(monkeypatch):
    """云 metadata 地址（169.254.169.254，字面 IP）→ 拒绝且零触达。"""
    called = _net_touch_guard(monkeypatch)
    assert dim._mirror_one("http://169.254.169.254/latest/meta-data/") is None
    assert called["n"] == 0


class _FakeResp:
    """带重定向判定属性的假 200 响应（safe_fetch 会读 is_redirect）。"""

    status_code = 200
    content = b"img"
    is_redirect = False
    is_permanent_redirect = False


def test_mirror_allows_public_and_uploads_cos(monkeypatch):
    """公网 URL → safe_fetch 放行 → 下载字节转存 COS，返回 COS URL。"""
    _fake_dns(monkeypatch)
    seen = {}

    def fake_request(method, url, **kw):
        seen["method"], seen["url"] = method, url
        return _FakeResp()

    monkeypatch.setattr(sf.requests, "request", fake_request)
    monkeypatch.setattr(dim, "cos_upload_bytes",
                        lambda data, key, content_type=None: f"https://cos/{key}")
    out = dim._mirror_one("https://img.example.com/a.jpg")
    assert out and out.startswith("https://cos/draft-images/")
    assert seen["method"] == "GET"
    assert seen["url"] == "https://img.example.com/a.jpg"


def test_mirror_blocks_redirect_to_internal(monkeypatch):
    """公网 URL 302 跳内网 → 第二跳在地址校验即拦（零请求发给 metadata）。"""
    _fake_dns(monkeypatch)
    calls = {"n": 0}

    class RedirectResp:
        status_code = 302
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        is_redirect = True
        is_permanent_redirect = False

    def fake_request(method, url, **kw):
        calls["n"] += 1
        return RedirectResp()

    monkeypatch.setattr(sf.requests, "request", fake_request)
    assert dim._mirror_one("https://jump.example/pic.jpg") is None
    assert calls["n"] == 1  # 第二跳未发出（逐跳复核拦在请求前）


def test_mirror_draft_images_keeps_unsafe_url_as_is(monkeypatch):
    """mirror_draft_images 行为锁：内网图 → 镜像失败保持原外链（changed=False，
    不破坏图片列表、不阻断草稿保存——降级语义与旧失败路径一致）。"""
    called = _net_touch_guard(monkeypatch)
    monkeypatch.setattr(dim, "cos_enabled", lambda: True)
    payload = {"draft": {"images": ["http://127.0.0.1:18477/ssrf-poc"]}}
    new_images, changed = dim.mirror_draft_images(payload)
    assert changed is False
    assert new_images == ["http://127.0.0.1:18477/ssrf-poc"]
    assert called["n"] == 0
