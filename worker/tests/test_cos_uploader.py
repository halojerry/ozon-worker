"""v0.28.5 E1: COS 转存兜底回归 — 未配置时优雅降级, 不阻断主流程。

⚠️ 环境隔离(v0.29.3): 生产已配置 COS_* 凭证时, 本模块测试会因
"已配置"而失败(预期行为变化, 非 bug)。测试前显式清空 COS_* 环境变量,
保证断言前提成立。
"""
import os
import sys
from pathlib import Path

import pytest

# 环境隔离: 清空 COS_* 凭证, 保证"未配置"断言前提(COS_* 可能来自生产 .env)
for _k in list(os.environ.keys()):
    if _k.startswith("COS_"):
        os.environ.pop(_k, None)


@pytest.fixture(autouse=True)
def _no_cos_env():
    """每个用例运行前强制清空 COS_*（import 期清理不够：storage/db.py 模块级
    load_dotenv() 会在其他测试文件的 import/fixture 链中注入 worker/.env 的
    COS_*，时序与本文件无关）。"""
    for _k in list(os.environ.keys()):
        if _k.startswith("COS_"):
            os.environ.pop(_k, None)
    yield


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.cos_uploader import (
    _is_reference_image,
    cos_enabled,
    cos_upload_bytes,
    salvage_original_images,
    _stable_key,
)


def test_cos_disabled_by_default():
    """未配置 COS 凭证 → cos_enabled False。"""
    assert cos_enabled() is False


def test_upload_none_when_disabled():
    """未配置 → cos_upload_bytes 返回 None(不抛异常)。"""
    assert cos_upload_bytes(b"data", "x/y.jpg") is None


def test_salvage_empty_when_disabled():
    """未配置 → salvage 返回 [](不下载不报错)。"""
    assert salvage_original_images(["https://cbu01.alicdn.com/img/1.jpg"]) == []


def test_salvage_empty_input():
    """空输入 → []。"""
    assert salvage_original_images([]) == []


def test_salvage_skips_competitor_images():
    """竞品图(ir.ozone.ru)被跳过(防侵权图补位)。"""
    # 未配置 COS 时直接返回 [], 无副作用; 断言不会尝试下载竞品图(无网络调用)
    assert salvage_original_images(["https://ir.ozone.ru/s3/multimedia-1/1.jpg"]) == []


def test_stable_key_deterministic():
    """同 URL → 同 key(跨进程一致)。"""
    k1 = _stable_key("https://cbu01.alicdn.com/img/a.jpg", "ozon-1688")
    k2 = _stable_key("https://cbu01.alicdn.com/img/a.jpg", "ozon-1688")
    assert k1 == k2
    assert k1.startswith("ozon-1688/salvage/")
    assert k1.endswith(".jpg")


# ═══ fix/image-ref-pollution: E1 白名单化（拒缩略图/非alicdn域）═══
# 线上事故 payload 的真实串图形态（1688 搜索兜底来的别家 310x310 缩略图）
FOREIGN_THUMB = ("https://cbu01.alicdn.com/img/ibank/"
                 "O1CN018Sj4ys2BTJkwUJ3Lc_!!2208080228339-0-cib.310x310.jpg")


def test_reference_rejects_search_thumbnail():
    """alicdn 域的 310x310 搜索缩略图（串图元凶形态）→ 拒。"""
    assert _is_reference_image(FOREIGN_THUMB) is True


def test_reference_rejects_ozone_all_subdomains():
    """Ozon 任意形态域名 → 拒（白名单语义，不枚举黑名单）。"""
    for url in ("https://ir.ozone.ru/s3/a.jpg",
                "https://cdn1.ozone.ru/s3/b.jpg",
                "https://ir-20.ozonstatic.cn/c.jpg"):
        assert _is_reference_image(url) is True, url


def test_reference_accepts_alicdn_original():
    """alicdn 原尺寸图 → 非参考图（E1 可转存）。"""
    assert _is_reference_image(
        "https://cbu01.alicdn.com/img/ibank/O1CNx_!!123-0-cib.jpg") is False


def test_reference_rejects_garbage():
    """非字符串/空串 → 拒。"""
    assert _is_reference_image(None) is True  # type: ignore[arg-type]
    assert _is_reference_image("") is True
    assert _is_reference_image("   ") is True
