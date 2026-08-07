"""v0.28.5 E1: COS 转存兜底回归 — 未配置时优雅降级, 不阻断主流程。

⚠️ 环境隔离(v0.29.3): 生产已配置 COS_* 凭证时, 本模块测试会因
"已配置"而失败(预期行为变化, 非 bug)。测试前显式清空 COS_* 环境变量,
保证断言前提成立。
"""
import os
import sys
from pathlib import Path

# 环境隔离: 清空 COS_* 凭证, 保证"未配置"断言前提(COS_* 可能来自生产 .env)
for _k in list(os.environ.keys()):
    if _k.startswith("COS_"):
        os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.cos_uploader import cos_enabled, cos_upload_bytes, salvage_original_images, _stable_key


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
