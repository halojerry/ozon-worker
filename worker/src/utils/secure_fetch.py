# worker/src/utils/secure_fetch.py
# Engine: worker | Python 3.12+ | 语言: Python
# Run: 由 worker 内图片抓取/探测链路调用（T14-T16 接线）
# Deps: requests（worker 现有依赖）
"""SSRF 防线唯一入口（v0.76 security-remediation T13，审计 inj-C1/H1/H2）。

三层防线：
1. scheme 白名单 http/https；hostname 必须存在。
2. getaddrinfo 解析 hostname 的**全部**地址，任一命中保留/内网/链路本地段即拒
   （防 DNS rebinding 的解析侧；TOCTOU 残余风险接受——worker 无凭据型内网服务可达性差异）。
3. 可选 allowed_host_suffixes：hostname 精确或「.suffix」后缀匹配（绝不做子串 in，
   那会被 `?pad=alicdn.com` 垫片绕过——审计实证）。

safe_fetch 手动跟随重定向（allow_redirects=False），每跳重新过 1-3 全套校验。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
    "::1/128", "fc00::/7", "fe80::/10", "::ffff:0:0/96",
))


class UnsafeUrlError(ValueError):
    """URL 未通过安全校验（内网地址/非法 scheme/白名单外域名）。"""


def _ip_is_blocked(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True  # 解析不出合法 IP = 不放行
    return any(ip in net for net in _BLOCKED_NETS) or ip.is_reserved or ip.is_multicast


def _host_is_safe(host: str) -> bool:
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    return not any(_ip_is_blocked(info[4][0]) for info in infos)


def _host_in_allowlist(hostname: str, suffixes: Iterable[str]) -> bool:
    h = hostname.lower()
    for s in suffixes:
        s = s.lower().lstrip(".")
        if h == s or h.endswith("." + s):
            return True
    return False


def assert_safe_remote_url(url: str, allowed_host_suffixes: Optional[Iterable[str]] = None) -> None:
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"scheme not allowed: {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("missing hostname")
    if allowed_host_suffixes is not None and not _host_in_allowlist(hostname, allowed_host_suffixes):
        raise UnsafeUrlError(f"host not in allowlist: {hostname}")
    if hostname in ("localhost",) or hostname.endswith(".localhost"):
        raise UnsafeUrlError("localhost blocked")
    if not _host_is_safe(hostname):
        raise UnsafeUrlError(f"host resolves to blocked address: {hostname}")


def safe_fetch(url: str, method: str = "get", timeout: float = 10.0,
               max_redirects: int = 3, allowed_host_suffixes: Optional[Iterable[str]] = None,
               **kwargs) -> requests.Response:
    """安全抓取：每跳（含重定向 Location）重新过全套校验。"""
    current = str(url).strip()
    for hop in range(max_redirects + 1):
        assert_safe_remote_url(current, allowed_host_suffixes=allowed_host_suffixes)
        resp = requests.request(method.upper(), current, timeout=timeout,
                                allow_redirects=False, **kwargs)
        if resp.is_redirect or resp.is_permanent_redirect:
            loc = resp.headers.get("Location", "")
            if not loc:
                return resp
            from urllib.parse import urljoin
            current = urljoin(current, loc)
            continue
        return resp
    raise UnsafeUrlError(f"too many redirects (> {max_redirects})")
