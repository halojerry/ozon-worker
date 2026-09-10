"""跨平台货源扩展 v1 批1:图片下载 Referer 分派单测。

pdd 图床有热链校验(无 Referer 可能 403,PLAN §2 A4);淘宝系 taobaocdn 同理。
分派规则:1688/alicdn 域保持现状(detail.1688.com,逐字节不变,含 img.alicdn.com
——天猫/淘宝主图床,不重派淘宝 Referer);taobaocdn → item.taobao.com;
pdd 系三域 → mobile.yangkeduo.com。无规则命中不加头(行为同今日)。

运行(无需 PG/GPU):
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_referer_dispatch_multi_platform.py -q
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import image_url_processor

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class _CaptureGet:
    """替换 requests.get,捕获调用 headers,返回 200 图字节。"""

    def __init__(self):
        self.headers = None

    def __call__(self, url, headers=None, timeout=30):
        self.headers = dict(headers or {})
        return SimpleNamespace(status_code=200, content=b"img-bytes")


def _download_with(monkeypatch, url, status=200):
    cap = _CaptureGet()
    monkeypatch.setattr(image_url_processor.requests, "get",
                        lambda u, headers=None, timeout=30: (
                            cap(u, headers, timeout)
                            if status == 200 else
                            SimpleNamespace(status_code=status, content=b"")))
    out = image_url_processor._download_image(url)
    return out, cap.headers


def test_1688_referer_unchanged(monkeypatch):
    """1688/alicdn 域 → detail.1688.com + 既有 UA(现状逐字节)。"""
    _, h1 = _download_with(monkeypatch, "https://cbu01.alicdn.com/img/ibank/a.jpg")
    assert h1 == {"Referer": "https://detail.1688.com/", "User-Agent": UA}
    _, h2 = _download_with(monkeypatch, "https://img.1688.com/page/a.jpg")
    assert h2 == {"Referer": "https://detail.1688.com/", "User-Agent": UA}


def test_alicdn_not_redispatched(monkeypatch):
    """img.alicdn.com(天猫/淘宝主图床)保持 detail.1688.com——不重派淘宝 Referer。"""
    _, h = _download_with(monkeypatch, "https://img.alicdn.com/imgextra/i2/O1CNmain.jpg")
    assert h["Referer"] == "https://detail.1688.com/"
    assert h["User-Agent"] == UA


def test_taobaocdn_referer(monkeypatch):
    """taobaocdn → item.taobao.com。"""
    _, h = _download_with(monkeypatch, "https://img.taobaocdn.com/bao/uploaded/i1/T1abc.jpg")
    assert h["Referer"] == "https://item.taobao.com/"
    assert h["User-Agent"] == UA


def test_pdd_referer(monkeypatch):
    """pdd 系(pddpic/yangkeduo/pinduoduo) → mobile.yangkeduo.com。"""
    for url in (
        "https://img.pddpic.com/mms-material-img/2024-06-11/abcdef.jpeg",
        "https://t00img.yangkeduo.com/goods/images/abc.jpeg",
        "https://mobile.pinduoduo.com/goods/images/abc.jpg",
    ):
        _, h = _download_with(monkeypatch, url)
        assert h["Referer"] == "https://mobile.yangkeduo.com/", url
        assert h["User-Agent"] == UA, url


def test_unknown_domain_no_headers(monkeypatch):
    """无规则命中(外域图)→ 不加 Referer/UA(行为同今日)。"""
    _, h = _download_with(monkeypatch, "https://cdn.example.com/x.jpg")
    assert h == {}


def test_referer_helper_pure(monkeypatch):
    """_referer_for_url 纯函数口径:命中表/未命中 None。"""
    f = image_url_processor._referer_for_url
    assert f("https://cbu01.alicdn.com/a.jpg") == "https://detail.1688.com/"
    assert f("https://img.taobaocdn.com/a.jpg") == "https://item.taobao.com/"
    assert f("https://img.pddpic.com/a.jpeg") == "https://mobile.yangkeduo.com/"
    assert f("https://cdn.example.com/a.jpg") is None
    assert f(None) is None
    assert f(123) is None


def test_failure_degradation_unchanged(monkeypatch):
    """下载失败降级路径不变:非 200 → None。"""
    out, _ = _download_with(monkeypatch, "https://img.pddpic.com/x.jpeg", status=404)
    assert out is None
