"""v0.76 T16 (inj-H2) — 其余图片探测点接入 safe_fetch：盲 SSRF/内网探测面收口。

覆盖三处裸 requests 探测（URL 均源自信封/请求参数，用户可控）：
  1. utils/image_quality_evaluator.check_url_alive（GET+Range）——
     消费方 services/image_service.update_product_images（在线改图死 URL 过滤）
  2. utils/image_quality_evaluator.evaluate_image_quality——
     消费方 white_bg/multi_angle 生图节点（state.original_images 参考图探测）
  3. graphs/nodes/ozon_validate_node Step3.5 图片可达性探测（requests.head）——
     信封 item.images[:3]+primary_image

契约：内网/保留段/非法 scheme URL 一律 **不发起任何 HTTP 请求**（safe_fetch
解析侧拦截 UnsafeUrlError）→ 探测语义「失败=False / 跳过」保持：
  - validate 侧：落既有 except 分支，降级 warning 不拦截（网络失败≠图片失效）
  - image_service 侧：check_url_alive → False，URL 进 dead 过滤
  - evaluator 侧：跳过该图（既有 warning 路径）

哨兵手法：monkeypatch requests.get/head/request（裸调与 utils.secure_fetch
共用同一 requests 模块对象）计数并 fail-on-touch；socket.getaddrinfo fake 到
公共 IP——零真实出站（纪律：本文件不允许任何 socket 触达）。

运行（纯 mock，无需 PG/网络）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_probe_points_ssrf_v076.py -q
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from graphs.nodes import ozon_validate_node as ovn  # noqa: E402
from graphs.nodes.ozon_validate_node import ozon_validate_node  # noqa: E402
from graphs.state import OzonValidateInput  # noqa: E402
from utils import image_quality_evaluator as iqe  # noqa: E402
from utils import image_url_processor  # noqa: E402

_LOOPBACK = "http://127.0.0.1:1/x.jpg"
_PUB = "https://cdn.example.com/x.jpg"


class Sentinel(Exception):
    pass


class _FakeResp(SimpleNamespace):
    """safe_fetch 返回的响应会被 `with` 关闭（stream=True 语义）——补上下文协议。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_dns(monkeypatch):
    """getaddrinfo fake：hostname → 公共 IP（safe_fetch 解析校验可过且零真实
    DNS 查询）；**IP 字面量原样返回**——127.0.0.1 必须仍解析成 loopback，
    safe_fetch 才会在发请求前拒绝（本文件的核心被测行为）。"""
    import ipaddress

    def _fake_getaddrinfo(host, port=None, *a, **k):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [(2, 1, 6, "", ("93.184.216.34", port or 0))]
        return [(2, 1, 6, "", (host, port or 0))]

    monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo)


@pytest.fixture
def no_network(monkeypatch):
    """requests.get/head/request/post/put 打桩：触达即计数 + 抛 Sentinel
    （裸调与 secure_fetch 路径都逃不过），并 fake DNS。零真实出站。"""
    calls = {"n": 0}

    def _touch(*a, **k):
        calls["n"] += 1
        raise Sentinel("network touched")

    for name in ("get", "head", "request", "post", "put"):
        monkeypatch.setattr(f"requests.{name}", _touch)
    _fake_dns(monkeypatch)
    return calls


# ── 1) check_url_alive（image_service 在线改图消费）────────────────────────

def test_check_url_alive_loopback_no_request(no_network):
    """内网 URL → False 且零 HTTP 请求（接线前裸 requests.get 会真连 → 红）。"""
    assert iqe.check_url_alive(_LOOPBACK) is False
    assert no_network["n"] == 0


def test_check_url_alive_public_alive_via_safe_fetch(monkeypatch):
    """公网 URL 存活语义保持：safe_fetch → 200 → True（走 requests.request）。"""
    _fake_dns(monkeypatch)
    touched = {"n": 0}

    def _fake_request(method, url, **kw):
        touched["n"] += 1
        assert kw.get("allow_redirects") is False  # safe_fetch 手动逐跳复核
        return _FakeResp(status_code=200, headers={},
                         is_redirect=False, is_permanent_redirect=False)

    monkeypatch.setattr("requests.request", _fake_request)
    assert iqe.check_url_alive(_PUB) is True
    assert touched["n"] == 1


# ── 2) evaluate_image_quality（生图节点参考图探测消费）─────────────────────

def test_evaluate_image_quality_loopback_no_request(no_network):
    """内网 URL → 跳过（空结果）且零 HTTP 请求（公网 URL 会正常发起 1 次
    safe_fetch 请求，不属本断言面）。"""
    assert iqe.evaluate_image_quality([_LOOPBACK]) == []
    assert no_network["n"] == 0


def test_evaluate_image_quality_public_still_scores(monkeypatch):
    """公网 URL 评分语义保持：Content-Range 总大小进 metadata。"""
    _fake_dns(monkeypatch)
    monkeypatch.setattr(
        "requests.request",
        lambda *a, **k: _FakeResp(
            status_code=206, is_redirect=False, is_permanent_redirect=False,
            headers={"Content-Range": "bytes 0-0/300000", "Content-Type": "image/jpeg"}))
    out = iqe.evaluate_image_quality([_PUB])
    assert len(out) == 1 and out[0]["size"] == 300000


# ── 3) ozon_validate_node Step3.5 图片可达性探测 ────────────────────────────

@pytest.fixture(autouse=True)
def _hermetic_category(monkeypatch):
    """RU 类目路径 PG 直查打桩（对齐存量 validate 测试手法，脱离 PG）。"""
    monkeypatch.setattr(ovn, "_fetch_ru_category_path",
                        lambda dc, tp: "", raising=False)


def _run_validate(items):
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c",
        ozon_api_key="k",
        attributes_schema=[],
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


def _item(images):
    return {
        "name": "Трещотка набор 1/4", "offer_id": "sku1", "price": "1990",
        "old_price": "2390", "vat": "0", "weight": 300, "weight_unit": "g",
        "depth": 100, "width": 100, "height": 50, "dimension_unit": "mm",
        "images": images, "primary_image": images[0] if images else "",
        "description_category_id": 17028653, "type_id": 92147,
        "attributes": [],
    }


def test_validate_probe_loopback_no_request_degrades(no_network):
    """信封图片为内网 URL：safe_fetch 解析侧拒绝 → 落既有异常分支（降级
    warning 不拦截、不计「不可访问」失败），且绝不发起任何 HTTP 请求。"""
    out = _run_validate([_item([_LOOPBACK])])
    assert not any("不可访问" in e for e in out.validation_errors), \
        f"UnsafeUrlError 不得计为图片失效: {out.validation_errors}"
    assert no_network["n"] == 0


def test_validate_probe_http404_still_blocks(monkeypatch):
    """接线不改变探测语义：HTTP 404（明确失效）仍计失败 → 全部不可达 → 阻断。"""
    _fake_dns(monkeypatch)
    monkeypatch.setattr(
        "requests.request",
        lambda *a, **k: SimpleNamespace(status_code=404, headers={},
                                        is_redirect=False,
                                        is_permanent_redirect=False))
    out = _run_validate([_item([_PUB])])
    assert any("不可访问" in e for e in out.validation_errors), \
        f"HTTP≥400 必须仍判图片失效: {out.validation_errors}"
    assert out.is_valid is False


# ── 4) 下载出口安全锁：image_url_processor._download_image（T16 + v0.78 合并）──
#
# T16 曾以「零生产调用方 + 裸 requests.get」删除该函数；合并 dev 后 v0.78 批F
# card_image_assert 需要它取卡图字节做 3:4 比例断言（删除会让断言恒 unverified，
# 宽 except 静默吞 ImportError），故恢复为 safe_fetch 包装。安全属性不得回退：
# 本锁从「函数不存在」升级为「存在但只走 safe_fetch、源码零裸 requests」。

def test_download_image_routes_through_safe_fetch(monkeypatch):
    """_download_image 必须经 secure_fetch.safe_fetch 下载，且保持 Referer 分派。"""
    import inspect

    src = inspect.getsource(image_url_processor._download_image)
    assert "requests." not in src, f"下载函数出现裸 requests 调用: {src}"

    calls = {}

    class _Resp:
        status_code = 200
        content = b"img-bytes"

    def _fake_fetch(url, timeout=30, headers=None, **kw):
        calls["url"] = url
        calls["headers"] = headers or {}
        return _Resp()

    monkeypatch.setattr(image_url_processor, "safe_fetch", _fake_fetch)
    assert image_url_processor._download_image("https://cbu01.alicdn.com/a.jpg") == b"img-bytes"
    assert calls["url"] == "https://cbu01.alicdn.com/a.jpg"
    # Referer 防盗链分派保持（1688 域 → detail.1688.com）
    assert calls["headers"].get("Referer") == "https://detail.1688.com/"
