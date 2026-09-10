"""跨平台货源 URL 解析（纯函数，零 I/O）— 货源平台扩展 v1 批2。

四平台识别：1688 / 淘宝(taobao) / 天猫(tmall) / 拼多多(pdd)。
`parse_platform_url` 是全链路唯一 URL→平台入口：cli cmd_graph item_id 提取、
batch_test URL 分派、browser_probe 硬门白名单、taobao/pdd 适配器共用。

**1688 byte-compat 红线**：1688 的三种模式（/offer/ 路径、纯数字 id、
id=/offerId=/offer_id= query）与 canonical 口径从
`ak_1688_client.parse_product_url` 原样并入——offer 路径/纯数字规范化为
``https://detail.1688.com/offer/{id}.html``，**query 形态 canonical 保留
原值不重写**（同 ak 行为）。test_source_platforms_parse.py 用 ak 解析器
做逐字段对照回归；改本模块前先跑它。

**pdd 红线（批3 前）**：parse 可识别 pdd，但 `probe_platform_supported`
恒 False——browser_probe 硬门据此拒绝（拼多多适配器在批3 提供）。

canonical 口径（新平台统一规范化，供信封 purchase_url/offer_id 解析）:
- taobao → ``https://item.taobao.com/item.htm?id={id}``
- tmall  → ``https://detail.tmall.com/item.htm?id={id}``
- pdd    → ``https://mobile.yangkeduo.com/goods.html?goods_id={id}``
短链（e.tb.cn / p.pinduoduo.com 分享口令）不支持——需要 HTTP 解析非纯函数，
批5 实机 gate 如遇到再议。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

SUPPORTED_PLATFORMS = ("1688", "taobao", "tmall", "pdd")

# 抓取白名单：1688 现状 + 批2 淘宝/天猫适配器；pdd 批3 才提供适配器
PROBE_SUPPORTED_PLATFORMS = ("1688", "taobao", "tmall")


@dataclass(frozen=True)
class PlatformTarget:
    """parse_platform_url 结果：平台 + item_id + 规范化 URL。"""

    platform: str  # "1688" | "taobao" | "tmall" | "pdd"
    item_id: str
    canonical_url: str


# ── 1688（与 ak_1688_client.parse_product_url 逐字节一致的三段）──

_1688_OFFER_PATH_RE = re.compile(r"/offer/(\d+)")
_1688_QUERY_KEYS = ("id", "offerId", "offer_id")

# ── taobao / tmall：item.htm / detail 形态 + id= 数字 query ──
_TAOBAO_TMALL_ID_RE = re.compile(r"[?&]id=(\d+)")
_TAOBAO_WORLD_ITEM_RE = re.compile(r"/item/(\d+)\.htm")

# ── pdd：goods / goods1 / goods2.html + goods_id= ──
_PDD_GOODS_ID_RE = re.compile(r"[?&]goods_id=(\d+)")


def _parse_1688(value: str, parsed) -> PlatformTarget | None:
    """1688 三模式（纯数字已在 parse_platform_url 入口先行处理）。

    ⚠️ query 形态 canonical=原值——与 ak_1688_client.parse_product_url
    逐字节一致（见模块红线），勿"顺手"规范化。
    """
    path = parsed.path or ""
    query = parsed.query or ""
    m = _1688_OFFER_PATH_RE.search(path)
    if m:
        pid = m.group(1)
        return PlatformTarget("1688", pid, f"https://detail.1688.com/offer/{pid}.html")
    for key in _1688_QUERY_KEYS:
        m = re.search(rf"{key}=(\d+)", query)
        if m:
            return PlatformTarget("1688", m.group(1), value)
    return None


def parse_platform_url(url) -> PlatformTarget | None:
    """识别四平台货源 URL。

    Returns:
        PlatformTarget(platform, item_id, canonical_url)，无法识别 → None。
        None 不是错误——调用方（cli/batch）自行决定报错或回落旧正则。
    """
    value = str(url or "").strip()
    if not value:
        return None

    # 纯数字 id（1688 约定，6-18 位）——ak_1688_client 首段原样并入
    if re.fullmatch(r"\d{6,18}", value):
        return PlatformTarget(
            "1688", value, f"https://detail.1688.com/offer/{value}.html")

    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    query = parsed.query or ""
    if not host:
        return None

    # ── 1688 ──
    if host == "1688.com" or host.endswith(".1688.com"):
        return _parse_1688(value, parsed)

    # ── taobao：item.htm / detail 形态 + id=；world.taobao.com/item/{id}.htm ──
    if host == "taobao.com" or host.endswith(".taobao.com"):
        item_id = _taobao_tmall_id(path, query)
        if item_id:
            return PlatformTarget(
                "taobao", item_id, f"https://item.taobao.com/item.htm?id={item_id}")
        m = _TAOBAO_WORLD_ITEM_RE.search(path)
        if m:
            return PlatformTarget(
                "taobao", m.group(1),
                f"https://item.taobao.com/item.htm?id={m.group(1)}")
        return None

    # ── tmall：任意 *.tmall.com 详情形态 + id= ──
    if host == "tmall.com" or host.endswith(".tmall.com"):
        item_id = _taobao_tmall_id(path, query)
        if item_id:
            return PlatformTarget(
                "tmall", item_id, f"https://detail.tmall.com/item.htm?id={item_id}")
        return None

    # ── pdd：goods[1|2].html + goods_id= ──
    if host == "yangkeduo.com" or host.endswith(".yangkeduo.com") \
            or host == "pinduoduo.com" or host.endswith(".pinduoduo.com"):
        if re.search(r"/goods\d*\.html", path):
            m = _PDD_GOODS_ID_RE.search("?" + query if query else "")
            if m:
                pid = m.group(1)
                return PlatformTarget(
                    "pdd", pid,
                    f"https://mobile.yangkeduo.com/goods.html?goods_id={pid}")
        return None

    return None


def _taobao_tmall_id(path: str, query: str) -> str | None:
    """淘宝系（taobao/tmall）item id 提取：详情形态路径 + [?&]id=数字。

    路径形态覆盖 item.htm / detail.htm / detail/index.html（m 站详情）；
    只认数字 id——非数字（sku 名/分享码）不算。
    """
    if not ("item" in path or "detail" in path):
        return None
    m = _TAOBAO_TMALL_ID_RE.search("?" + query if query else "")
    return m.group(1) if m else None


def probe_platform_supported(platform: str) -> bool:
    """该平台的抓取适配器是否已上线（pdd 批2/批3 前为 False）。"""
    return platform in PROBE_SUPPORTED_PLATFORMS
