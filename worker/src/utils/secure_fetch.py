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

safe_fetch 手动跟随重定向（allow_redirects=False），每跳重新过 1-3 全套校验；
端点三元组（scheme+hostname+有效端口）任一变化的跳自动剥除 Authorization/Cookie
headers 与 auth/cookies 参数（凭据绝不重放给跨端点跳）。
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
    except (OSError, UnicodeError, ValueError):
        # T13 评审：畸形 IDN（label>63 等）在 CPython 纯 Python 层 idna 编码即抛
        # UnicodeError——必须收口进 fail-closed（不解析成功 = 不放行），绝不让裸
        # 异常逃出 assert_safe_remote_url 的 UnsafeUrlError 契约。
        # r2 并入 ValueError：Linux CPython 对 null 字节 host 抛
        # ValueError("embedded null byte")，非 UnicodeError 子类。
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


_CREDENTIAL_HEADER_NAMES = frozenset(("authorization", "cookie"))


def _strip_hop_credentials(kwargs: dict) -> dict:
    """返回剥除凭据后的 kwargs 副本（headers 大小写不敏感删 Authorization/Cookie；
    auth/cookies 请求参数直接删）。只对跨端点跳调用（端点三元组见 _endpoint_key），
    同端点跳保持原样。（措辞勘误 T13 评审：r1 口径「跨 host」已改「跨端点」。）"""
    stripped = dict(kwargs)
    headers = stripped.get("headers")
    if headers:
        stripped["headers"] = {k: v for k, v in dict(headers).items()
                               if str(k).lower() not in _CREDENTIAL_HEADER_NAMES}
    stripped.pop("auth", None)
    stripped.pop("cookies", None)
    return stripped


def _endpoint_key(url: str) -> tuple:
    """凭据剥离判定的端点三元组（r2：对齐 requests rebuild_auth 全口径）：
    scheme + hostname + 有效端口（显式端口优先，缺省按 scheme 归一 http=80/https=443）——
    http://host → https://host 的同 host 跨 scheme 跳同样判为跨端点、剥凭据。"""
    parsed = urlparse(url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return (parsed.scheme, parsed.hostname, port)


def safe_fetch(url: str, method: str = "get", timeout: float = 10.0,
               max_redirects: int = 3, allowed_host_suffixes: Optional[Iterable[str]] = None,
               **kwargs) -> requests.Response:
    """安全抓取：每跳（含重定向 Location）重新过全套校验；跨端点跳剥除凭据。"""
    current = str(url).strip()
    current_key = _endpoint_key(current)
    hop_kwargs = kwargs
    for hop in range(max_redirects + 1):
        assert_safe_remote_url(current, allowed_host_suffixes=allowed_host_suffixes)
        resp = requests.request(method.upper(), current, timeout=timeout,
                                allow_redirects=False, **hop_kwargs)
        if resp.is_redirect or resp.is_permanent_redirect:
            loc = resp.headers.get("Location", "")
            if not loc:
                return resp
            from urllib.parse import urljoin
            current = urljoin(current, loc)
            next_key = _endpoint_key(current)
            if next_key != current_key:
                # T13 评审 Advisory：requests allow_redirects=True 的跨 host 凭据剥除语义，手动循环必须自己实现
                hop_kwargs = _strip_hop_credentials(kwargs)
            current_key = next_key
            continue
        return resp
    raise UnsafeUrlError(f"too many redirects (> {max_redirects})")
