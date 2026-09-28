"""v0.83.1 safe_fetch fake-ip 逃生门回归（2026-09-28 本地采集箱重提全 422 实锤）。

背景：宿主 Mac 代理（Clash/Surge fake-ip 模式）接管 Docker DNS 后，所有外网域名
解析进 198.18.0.0/15（IANA 基准测试段）→ safe_fetch 保留段检查全拦 → 草稿图
镜像全失败。修复 = 显式 env ``SAFE_FETCH_ALLOW_FAKE_IP=1`` 放行**仅该段**；
生产默认不变（其余内网/保留段恒拦）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_safe_fetch_fakeip_v0831.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.secure_fetch import _effective_blocked_nets, _ip_is_blocked  # noqa: E402


def test_default_blocks_fake_ip_range():
    """默认（无 env）：198.18.x 仍拦（生产语义零变化）。"""
    assert _ip_is_blocked("198.18.1.228")
    assert _ip_is_blocked("198.19.0.1")


def test_env_optin_allows_fake_ip_only(monkeypatch):
    """opt-in：fake-ip 段放行，其余内网/保留段恒拦。"""
    monkeypatch.setenv("SAFE_FETCH_ALLOW_FAKE_IP", "1")
    assert not _ip_is_blocked("198.18.1.228")
    assert not _ip_is_blocked("198.19.255.255")
    # 内网/环回/组播不放行
    for bad in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.1",
                "169.254.1.1", "224.0.0.1", "240.0.0.1"):
        assert _ip_is_blocked(bad), f"{bad} 必须保持拦截"
    assert len(_effective_blocked_nets()) >= 12


def test_env_truthy_variants(monkeypatch):
    for v in ("1", "true", "YES", " True "):
        monkeypatch.setenv("SAFE_FETCH_ALLOW_FAKE_IP", v)
        assert not _ip_is_blocked("198.18.0.1"), f"truthy 值 {v!r} 应放行"
    for v in ("", "0", "no", "false"):
        monkeypatch.setenv("SAFE_FETCH_ALLOW_FAKE_IP", v)
        assert _ip_is_blocked("198.18.0.1"), f"falsy 值 {v!r} 应保持拦截"
