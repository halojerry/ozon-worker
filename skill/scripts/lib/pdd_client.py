"""拼多多商品抓取适配器 — 跨平台货源 v1 批3（taobao_client 同模式）。

一次 ``fetch_product``：CDP 打开/复用商品页 → 页上下文读取页嵌
``window.rawData``（三形态：``store.initDataObj.goods`` / ``store.goods`` /
``goods``）→ 归一为与 taobao_client 完全同形的统一 ProductInfo；rawData 全缺
→ **页内** fetch 兜底 ``{origin}/proxy/api/api/oak/integration/render/sku?pdduid={pdd_user_id}``
（pdd_user_id 从页内 ``document.cookie`` 读取——**cookie 明文绝不离开页面**，
回传 Python 的载荷只带 ``pdduid_present`` 布尔）。

参考实现（字段权威）：
- goldminer-collector-extension v2.43 ``background.js``：``readPddGoodsRuntimeInfo``
  （:13723 三形态路径）/ ``buildPddGoodsSnapshot``（:13591 字段名与优先序）/
  ``fetchPddGoodsFallback``（:13740 兜底请求体与响应映射）；
- ozonAI V2.3.3 ``pdd-main.js``：goodsName / topGallery[].url / detailGallery/
  goodsProperty{key,values} / skus[].specs[{spec_key,spec_value}] / skus[].thumbUrl /
  skus[].groupPrice / skus[].limitQuantity / maxGroupPrice /
  ``pdd-go-to-app-pc``（强制 App 引导条标记）；
- ``docs/competitor/maozier-plugin-full.md`` §pdd（rawData + render/sku 兜底同构）。

⚠️ 风险标注（计划批3 item 3，改本模块前必读）：pdd web 反爬迭代频繁、rawData
结构随版本漂移——本适配器纪律为「**失败出声、绝不半猜**」：标题/图/价格任一
缺失、rawData 三形态全缺且兜底失败 → 抛人话异常（PddFetchError 家族，错误
消息点名已尝试的形态），绝不返回半猜数据；运费未知 → shipping 缺 freightCny
键（不是 0）；重量缺失 → ``weight_grams=None``（不是 0）。

价格单位口径（照抄参考实现，勿"顺手"改）：rawData 页嵌字段（camelCase 优先）
是展示态**元**；兜底 render/sku 响应的 ``group_price`` 是**分**（goldminer
``fromFen: true`` 先例）→ 本模块在 Python 侧按 source 统一换算
（``FALLBACK_SOURCE`` 时 /100），页内 JS 只做键名归一不做单位换算。

其它红线：
- **登录态用用户自己 Chrome 会话（CDP）**，本模块不搬 cookie、不落任何凭证；
  ``wait_for_login`` 复用 taobao_client 的 skill-login-wait 分级 UX 先例
  （TTY ≥300s / 非 TTY ≥90s + Enter 续等，登录页保留在浏览器）。
- tab 复用纪律对齐 taobao_client：find_tab 命中用户 tab → release()（绝不随
  连接 close 远程关用户页）；自建 tab 用后关闭。
- 运费文案解析/重量文案解析复用 taobao_client 的唯一实现（不内联复制）。

测试：``tests/test_pdd_client_normalize.py``（全 mock CDP，手工构造夹具，
零真实 cookie/凭证/截图入库；真实浏览器链路在批5 实机 gate）。
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Any

from scripts.lib.cdp_client import CdpConnection
from scripts.lib.source_platforms import PlatformTarget, parse_platform_url
from scripts.lib.taobao_client import (  # 共享文案解析（唯一实现，勿内联复制）
    _weight_grams_from_attributes,
    parse_freight_cny,
)

logger = logging.getLogger(__name__)

# 登录/验证页标记（goldminer isLoginUrl 正则的 pdd 段 :1169 + mobile 登录路径）。
# 仅用于页内检测与页内 URL 分类（location.href 恒为 pdd 域或其登录跳转，无跨域误配）。
PDD_LOGIN_URL_MARKERS = (
    "passport.pinduoduo.com",
    "renzheng.pinduoduo.com",
    "/login",
)
# 登录页打开地址（host 权威来自参考实现；路径 best-effort，mobile web 登录页）
PDD_LOGIN_URL = "https://mobile.yangkeduo.com/login.html"
# wait_for_login 专用 tab 匹配/完成判定：pdd 域限定——泛化 "/login" 会误配
# 其他平台登录 tab（如 https://login.taobao.com 含 "/login"），跨平台串台。
PDD_LOGIN_TAB_PATTERNS = (
    "passport.pinduoduo.com",
    "renzheng.pinduoduo.com",
    "yangkeduo.com/login",
    "pinduoduo.com/login",
)

# rawData 三形态（goldminer readPddGoodsRuntimeInfo :13724-13727 原序）
RAWDATA_SHAPE_NAMES = (
    "rawData.store.initDataObj.goods",
    "rawData.store.goods",
    "rawData.goods",
)
FALLBACK_SOURCE = "fallback_render_sku"

LOGIN_HINT = "请在工具 Chrome 登录拼多多后重试"


class PddFetchError(RuntimeError):
    """拼多多抓取失败（人话消息，点名已尝试形态——失败出声）。"""


class PddLoginRequired(PddFetchError):
    """未登录/会话过期/pdd_user_id 缺失 → 请在工具 Chrome 登录拼多多后重试。"""


class PddRiskControlError(PddFetchError):
    """风控/验证码/强制 App 引导拦截 → 请人工处理后重试。"""


# ────────────────────────── 纯函数（可独立测试）──────────────────────────


def _first(*values) -> Any:
    """goldminer firstNonEmpty：第一个非空（非 None/非空串）值。"""
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _https_url(value) -> str:
    """pdd 图 URL 协议相对形态（//img.pddpic.com/...）→ https 绝对化。"""
    u = str(value or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    return u


def _price_number(sku: dict, from_fen: bool = False) -> float | None:
    """sku 价格块 → float；缺失/解析不出/非正 → None。

    from_fen=True（兜底 render/sku 源）→ 分→元（goldminer fromFen 先例：
    ``Math.round(parsed)/100``）。字段优先序照抄 goldminer buildPddGoodsSnapshot：
    groupPrice|group_price|price|normalPrice|normal_price。
    """
    raw = _first(sku.get("groupPrice"), sku.get("group_price"),
                 sku.get("price"), sku.get("normalPrice"), sku.get("normal_price"))
    if raw in (None, ""):
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(raw))
    if not m:
        return None
    try:
        num = float(m.group(0))
    except ValueError:
        return None
    if num <= 0:
        return None
    if from_fen:
        return round(round(num) / 100, 2)
    return num


def _normalize_specs(raw_specs) -> list[tuple[str, str]]:
    """sku specs → [(label, value)]。

    goldminer ``normalizePddSkuSpecs``（:13445-13478）三形态移植：
    对象数组（spec_key/spec_value 优先，ozonAI 同款）/ 字符串 "label:value" /
    对象映射 {label: value}。
    """
    out: list[tuple[str, str]] = []
    if isinstance(raw_specs, dict):
        for label, value in raw_specs.items():
            v = str(_first(value if not isinstance(value, dict) else
                           _first(value.get("value"), value.get("name")), "") or "").strip()
            label = str(label or "").strip() or "规格"
            if v:
                out.append((label, v))
        return out
    if not isinstance(raw_specs, list):
        return out
    for index, item in enumerate(raw_specs):
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            parts = re.split(r"[:：]", text, maxsplit=1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                out.append((parts[0].strip(), parts[1].strip()))
            else:
                out.append((f"规格{index + 1}", text))
            continue
        if not isinstance(item, dict):
            continue
        label = str(_first(item.get("key"), item.get("spec_key"),
                           item.get("specKey"), item.get("parent_spec_name"),
                           item.get("name"), item.get("label"), "") or "").strip()
        value = str(_first(item.get("value"), item.get("spec_value"),
                           item.get("specValue"), item.get("value_name"),
                           item.get("display_value"), item.get("text"), "") or "").strip()
        if not value:
            continue
        out.append((label or f"规格{index + 1}", value))
    return out


def _collect_gallery(items) -> list[str]:
    """图集 → https 绝对化 URL 列表（去重保序）。

    字段优先序照抄 goldminer ``collectPddGalleryImageUrls``（:13480）：
    url|thumbUrl|thumb_url|imageUrl|image_url|hdThumbUrl|hd_thumb_url。
    """
    urls: list[str] = []
    for item in (items or []):
        if not isinstance(item, dict):
            if item:
                urls.append(_https_url(item))
            continue
        u = _https_url(_first(item.get("url"), item.get("thumbUrl"),
                              item.get("thumb_url"), item.get("imageUrl"),
                              item.get("image_url"), item.get("hdThumbUrl"),
                              item.get("hd_thumb_url")))
        if u and u not in urls:
            urls.append(u)
    return urls


def _normalize_skus(goods: dict, from_fen: bool) -> tuple[list[dict], list[dict]]:
    """skus → (sku_details, option_groups)。

    - sku_details：specs 值拼名 + 价格（单位按 from_fen）+ 首个带图值/thumbUrl；
      skuId 缺失回落规格名，重复加 ``_N`` 后缀（goldminer :13629-13636 口径）。
    - option_groups：按 spec label 聚合 values（首个命中 sku 图作 value 图，
      ozonAI pdd-main 同款）。
    """
    raw_skus = goods.get("skus")
    if not isinstance(raw_skus, list):
        raw_skus = goods.get("sku")
    if not isinstance(raw_skus, list):
        raw_skus = []

    sku_details: list[dict] = []
    option_values: dict[str, dict[str, dict]] = {}
    option_order: list[str] = []
    used_ids: set[str] = set()

    for index, raw in enumerate(raw_skus):
        if not isinstance(raw, dict):
            continue
        specs = _normalize_specs(_first(raw.get("specs"), raw.get("spec_list"),
                                        raw.get("specList"), raw.get("spec")))
        names = [v for _, v in specs if v]
        image = _https_url(_first(raw.get("thumbUrl"), raw.get("thumb_url"),
                                  raw.get("imageUrl"), raw.get("image_url")))
        for label, value in specs:
            if not label or not value:
                continue
            if label not in option_values:
                option_values[label] = {}
                option_order.append(label)
            bucket = option_values[label]
            if value not in bucket:
                entry: dict[str, Any] = {"name": value}
                if image:
                    entry["image"] = image
                bucket[value] = entry

        sku_id = str(_first(raw.get("skuId"), raw.get("sku_id"), raw.get("id"),
                            "") or "").strip()
        if not sku_id:
            sku_id = " ".join(names) if names else f"pdd_sku_{index + 1}"
        if sku_id in used_ids:
            sku_id = f"{sku_id}_{index + 1}"
        used_ids.add(sku_id)

        sku_details.append({
            "sku_id": sku_id,
            "name": " ".join(names) if names else sku_id,
            "price": _price_number(raw, from_fen=from_fen),
            "image": image,
        })

    option_groups = [{
        "name": label,
        "values": [option_values[label][v] for v in option_values[label]],
    } for label in option_order]
    return sku_details, option_groups


def _normalize_attrs(goods: dict) -> list[dict]:
    """goodsProperty → [{name, value}]（goldminer normalizePddPropertyRows 移植）。

    items：{key|name|label|prop_name, values[]（{value|name} 或字符串）或
    标量 value|text|display_value}；values 多值 ``;`` 连接（taobao BASE_PROPS
    列表值同口径）。
    """
    raw = _first(goods.get("goodsProperty"), goods.get("goods_property"))
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(_first(item.get("key"), item.get("name"), item.get("label"),
                          item.get("prop_name"), "") or "").strip()
        values_raw = item.get("values")
        if isinstance(values_raw, list):
            values = [str(_first(v.get("value") if isinstance(v, dict) else v,
                                 v.get("name") if isinstance(v, dict) else None,
                                 "") or "").strip()
                      for v in values_raw]
            values = [v for v in values if v]
        else:
            single = str(_first(item.get("value"), item.get("text"),
                                item.get("display_value"), "") or "").strip()
            values = [single] if single else []
        if name and values:
            out.append({"name": name, "value": ";".join(values)})
    return out


def _seller_name(goods: dict) -> str:
    """店铺名 best-effort：pdd 参考实现（goldminer/ozonAI）均未提取 mall 字段——
    探测常见 ``mall.mall_name`` 形态，缺失回空串（不编造）。"""
    mall = goods.get("mall")
    if isinstance(mall, dict):
        name = str(_first(mall.get("mall_name"), mall.get("mallName"),
                          mall.get("name"), "") or "").strip()
        if name:
            return name
    return str(_first(goods.get("mall_name"), goods.get("mallName"), "") or "").strip()


def _weight_grams(goods: dict, attributes: list[dict]) -> int | None:
    """重量：①goodsProperty 重量/净重/毛重（带单位，taobao 同款解析）
    ②skus[].weight 数值 → 克（ozonAI pieceWeight 先例，**best-effort**——
    pdd 参考实现均未标注单位，克为平台惯例口径）；均缺失 → None（绝不 0）。"""
    grams = _weight_grams_from_attributes(attributes)
    if grams is not None:
        return grams
    raw_skus = goods.get("skus") if isinstance(goods.get("skus"), list) else \
        goods.get("sku") if isinstance(goods.get("sku"), list) else []
    for raw in raw_skus:
        if isinstance(raw, dict):
            w = raw.get("weight")
            if isinstance(w, (int, float)) and w > 0:
                return int(round(w))
    return None


_SHAPE_HINT = " / ".join(RAWDATA_SHAPE_NAMES)


def _normalize_product(goods: dict | None, source: str, target: PlatformTarget,
                       freight_text: str | None) -> dict:
    """rawData goods / 兜底映射对象 → 统一 ProductInfo（taobao_client 同形）。

    纯函数。标题/图/价格缺失即抛 PddFetchError（点名已尝试形态）——失败出声，
    绝不编造。兜底源（source=FALLBACK_SOURCE）价格按分→元换算。
    """
    if not isinstance(goods, dict) or not goods:
        raise PddFetchError(
            f"拼多多页面数据未取到商品对象（已尝试 {_SHAPE_HINT} 三形态与 "
            f"render/sku 兜底，source={source or '无'}）——拒绝编造，"
            f"请刷新商品页后重试；{LOGIN_HINT}")
    from_fen = source == FALLBACK_SOURCE
    source_hint = source or "未知来源"

    title = str(_first(goods.get("goodsName"), goods.get("goods_name"),
                       goods.get("goodsTitle"), goods.get("goods_title"),
                       goods.get("title"), "") or "").strip()
    if not title:
        raise PddFetchError(
            f"拼多多页面数据缺商品标题（source={source_hint}，已尝试 {_SHAPE_HINT}）"
            f"——拒绝编造，请刷新商品页后重试")

    # 主图在前、详情图补后（跨集去重）——goldminer mainImages+detailImages 同口径
    images: list[str] = []
    for u in _collect_gallery(goods.get("topGallery")) + \
            _collect_gallery(goods.get("detailGallery")):
        if u and u not in images:
            images.append(u)
    if not images:
        raise PddFetchError(
            f"拼多多页面数据无可用商品图（source={source_hint}）——拒绝编造，"
            f"请刷新商品页后重试")

    sku_details, option_groups = _normalize_skus(goods, from_fen)
    prices = [float(s["price"]) for s in sku_details
              if s.get("price") and float(s["price"]) > 0]
    if prices:
        lo, hi = min(prices), max(prices)
    else:
        # 无 SKU 价 → 商品级 min/max 拼单价兜底（goldminer productPriceCandidates
        # :13666-13669 字段优先序：minGroupPrice|min_group_price|groupPrice|group_price
        # 与 minNormalPrice|min_normal_price）
        group_block = {"groupPrice": goods.get("minGroupPrice"),
                       "group_price": goods.get("min_group_price")}
        normal_block = {"normalPrice": goods.get("minNormalPrice"),
                        "normal_price": goods.get("min_normal_price")}
        candidates = [p for p in (_price_number(group_block, from_fen=from_fen),
                                  _price_number(normal_block, from_fen=from_fen))
                      if p]
        if not candidates:
            raise PddFetchError(
                f"拼多多页面数据无可用价格（sku 价与商品级拼单价均缺失，"
                f"source={source_hint}）——拒绝编造")
        lo = min(candidates)
        hi = max(candidates)
    price_str = f"{lo:.2f}"

    attributes = _normalize_attrs(goods)
    brand = ""
    for a in attributes:
        if a["name"] == "品牌":
            brand = a["value"]
            break

    shipping: dict[str, Any] = {}
    freight = parse_freight_cny(freight_text)
    if freight is not None:
        shipping["freightCny"] = float(freight)
        shipping["displayText"] = str(freight_text or "").strip()
        if freight == 0.0:
            shipping["freeShipping"] = True

    return {
        "platform": "pdd",
        "item_id": target.item_id,
        "canonical_url": target.canonical_url,
        "title": title,
        "price": price_str,
        "price_ranges": [lo, hi],
        "images": images,
        "description": "",
        "attributes": attributes,
        "option_groups": option_groups,
        "sku_details": sku_details,
        "shipping": shipping,
        "weight_grams": _weight_grams(goods, attributes),
        "seller": _seller_name(goods),
        "brand": brand,
    }


# ────────────────────────── 页内 rawData 读取 + 兜底 fetch JS ──────────────────────────
# 单次 evaluate（await_promise）。页内职责：
#   ①登录/验证页与风控检测（passport/renzheng/login 路径 + captcha 容器 +
#     pdd-go-to-app-pc 强制 App 引导条）；
#   ②按 goldminer 三形态读 window.rawData；
#   ③全缺 → 页内读 pdd_user_id cookie（**不出页**）→ 页内 POST render/sku；
#   ④回传 JSON 载荷（白名单键；pdduid 只以 pdduid_present 布尔出现）。
_PDD_EXTRACT_JS = r"""(async () => {
    function detectLoginUrl() {
      const href = String(location.href || '');
      if (/passport\.pinduoduo\.com|renzheng\.pinduoduo\.com/i.test(href)) return true;
      return /\/login/i.test(String(location.pathname || ''));
    }
    function detectCaptcha() {
      try {
        return !!(
          document.querySelector('[class*="captcha"]') ||
          document.querySelector('[class*="Captcha"]')
        );
      } catch (e) { return false; }
    }
    function detectAppBanner() {
      try { return !!document.querySelector('.pdd-go-to-app-pc'); }
      catch (e) { return false; }
    }
    function extractFreightText() {
      try {
        const bt = (document.body && (document.body.innerText || document.body.textContent)) || '';
        let m = bt.match(/包邮|免运费|免邮/);
        if (!m) m = bt.match(/(?:运费|快递费|物流费|邮费)[^。；;]{0,12}/);
        return m ? m[0] : null;
      } catch (e) { return null; }
    }
    function result(patch) {
      return JSON.stringify(Object.assign({
        status: 'error', error: null, message: null, url: String(location.href || ''),
        captcha: detectCaptcha(), app_banner: detectAppBanner(),
        source: '', goods: null, freightText: extractFreightText(),
        pdduid_present: null,
      }, patch));
    }
    function collectDomImages(selectors, limit) {
      const urls = [];
      String(selectors || '').split(',').map((s) => s.trim()).filter(Boolean).forEach((selector) => {
        document.querySelectorAll(selector).forEach((node) => {
          const candidate = (node.getAttribute && (node.getAttribute('data-src') || node.getAttribute('src'))) || node.src || '';
          const abs = String(candidate || '');
          if (!abs || abs.includes('assets/') || abs.includes('grey.gif')) return;
          const full = abs.startsWith('//') ? ('https:' + abs) : (abs.startsWith('/') ? (window.location.origin + abs) : abs);
          if (full && !urls.includes(full)) urls.push(full);
        });
      });
      return urls.slice(0, limit);
    }
    try {
      if (detectLoginUrl()) {
        return result({ status: 'login', error: 'LOGIN_PAGE', message: '商品页被重定向到登录/验证页' });
      }
      const goodsId = __GOODS_ID__;
      const shapes = [
        ['rawData.store.initDataObj.goods', window.rawData && window.rawData.store && window.rawData.store.initDataObj ? window.rawData.store.initDataObj.goods : null],
        ['rawData.store.goods', window.rawData && window.rawData.store ? window.rawData.store.goods : null],
        ['rawData.goods', window.rawData ? window.rawData.goods : null],
      ];
      for (let i = 0; i < shapes.length; i += 1) {
        const [path, goods] = shapes[i];
        if (goods && typeof goods === 'object') {
          return result({ status: 'ok', source: path, goods: goods });
        }
      }
      const cookieMatch = String(document.cookie || '').match(/(?:^|;\s*)pdd_user_id=([^;]+)/);
      const pdduid = cookieMatch ? decodeURIComponent(cookieMatch[1]) : '';
      if (!pdduid) {
        return result({ status: 'no_data', error: 'pdd_user_id_missing', message: '页内无 pdd_user_id cookie（未登录）', pdduid_present: false });
      }
      const resp = await fetch(window.location.origin + '/proxy/api/api/oak/integration/render/sku?pdduid=' + encodeURIComponent(pdduid), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          page_version: 7,
          _component_version: 2,
          hostname: window.location.origin,
          front_supports: ['front_promo_price', 'sku_selector_toast', 'group_tip_end_time', 'custom_sku', 'goods_reminder', 'morgan_suffix', 'goods_reminder_click'],
          goods_id: goodsId,
          page_from: 0,
          _oak_stage: 'mall_page',
        }),
      });
      if (!resp.ok) {
        return result({ status: 'no_data', error: 'fallback_http_' + resp.status, message: 'render/sku 兜底 HTTP ' + resp.status, pdduid_present: true });
      }
      const payload = await resp.json().catch(() => null);
      if (!payload || !payload.goods) {
        return result({ status: 'no_data', error: 'fallback_empty', message: 'render/sku 兜底响应无 goods', pdduid_present: true });
      }
      const mapped = {
        goodsID: goodsId,
        goodsName: payload.goods.goods_name || payload.goods.goodsName || '',
        goodsProperty: Array.isArray(payload.goods.goods_property)
          ? payload.goods.goods_property.map((item) => ({ key: item && item.key, values: Array.isArray(item && item.values) ? item.values : [] }))
          : [],
        topGallery: collectDomImages('.PPuOGFfM img, [class*="goodsBanner"] img, [class*="goodsImg"] img', 25).map((u) => ({ url: u })),
        detailGallery: collectDomImages('.UhNRiWLO img, [class*="goodsDetails"] img, [class*="goodsDetail"] img', 40).map((u) => ({ url: u })),
        skus: (Array.isArray(payload.sku) ? payload.sku : []).map((item) => ({
          specs: (item && Array.isArray(item.specs)) ? item.specs : [],
          thumbUrl: (item && (item.thumb_url || item.thumbUrl)) || '',
          groupPrice: item ? (item.group_price != null ? item.group_price : (item.groupPrice != null ? item.groupPrice : null)) : null,
          skuId: (item && (item.sku_id || item.skuId || item.id)) || '',
          quantity: item ? item.quantity : null,
          is_onsale: item ? item.is_onsale : null,
        })),
      };
      return result({ status: 'ok', source: 'fallback_render_sku', goods: mapped, pdduid_present: true });
    } catch (e) {
      return result({ status: 'error', error: 'JS_EXCEPTION', message: String((e && e.message) || e) });
    }
})()"""


# ────────────────────────── CDP 编排 ──────────────────────────


def _safe_json(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None
    return raw


def _evaluate_extract(tab, target: PlatformTarget, timeout_seconds: int) -> dict:
    js = _PDD_EXTRACT_JS.replace("__GOODS_ID__", json.dumps(str(target.item_id)))
    raw = tab.evaluate(js, await_promise=True, timeout=timeout_seconds)
    payload = _safe_json(raw)
    if not isinstance(payload, dict):
        raise PddFetchError(
            "拼多多页内数据读取返回无法解析（非 JSON）——页面可能被风控/强制 App "
            f"跳转拦截，请在工具 Chrome 打开商品页人工确认后重试；{LOGIN_HINT}")
    return payload


def _classify_payload(payload: dict) -> tuple[str, str]:
    """evaluate 载荷 → (kind, detail)。kind ∈ ok/login/risk/error。

    优先级：有数据先返回 ok（验证码容器可能误报，数据在手最权威）；无数据时
    依次判登录页 / pdd_user_id 缺失（同属登录语义，goldminer
    ``site_login_required`` 口径）/ 验证码 / 强制 App 引导 / 兜底错误原文。
    """
    status = str(payload.get("status") or "").strip()
    error = str(payload.get("error") or "").strip()
    url = str(payload.get("url") or "")
    if status == "ok":
        return "ok", ""
    if status == "login" or any(m in url for m in PDD_LOGIN_URL_MARKERS):
        return "login", f"商品页被重定向到登录/验证页（{url or '未知 URL'}）"
    if error == "pdd_user_id_missing":
        return "login", str(payload.get("message") or "页内无 pdd_user_id cookie（未登录）")
    if payload.get("captcha"):
        return "risk", "检测到验证码/人机验证拦截"
    if payload.get("app_banner"):
        return "risk", "检测到拼多多强制 App 引导（网页端数据不可用）"
    if status == "no_data" or error:
        detail = str(payload.get("message") or error or status)
        return "error", detail
    return "error", f"未知载荷状态 status={status or '空'}"


def fetch_product(cdp_url: str, item_url: str, target: PlatformTarget | None = None,
                  *, cdp: CdpConnection | None = None, timeout_seconds: int = 45,
                  login_wait: bool = False,
                  login_wait_seconds: int | None = None) -> dict:
    """抓取拼多多商品 → 统一 ProductInfo（taobao_client.fetch_product 同形）。

    Args:
        cdp_url: 工具 Chrome CDP 地址（自建连接时用；传入 cdp 时忽略）。
        item_url: 拼多多商品 URL（goods/goods1/goods2.html）。
        target: parse_platform_url 结果（None 时内部解析）。
        cdp: 可选外部 CdpConnection（归调用方所有，本函数不关它）。
        login_wait: True 时未登录 → ``wait_for_login``（分级 UX）后重试一次。

    Raises:
        PddLoginRequired: 未登录/会话过期/pdd_user_id 缺失。
        PddRiskControlError: 风控/验证码/强制 App 引导拦截。
        PddFetchError: 其他抓取/归一失败（人话消息，点名形态，绝不半猜）。
    """
    target = target or parse_platform_url(item_url)
    if target is None or target.platform != "pdd":
        raise PddFetchError(
            f"pdd_client 只支持拼多多商品 URL，收到: {item_url!r}"
            + ("（淘宝/天猫请走 taobao_client）"
               if target and target.platform in ("taobao", "tmall") else ""))

    own_connection = cdp is None
    if own_connection:
        try:
            cdp = CdpConnection(cdp_url)
        except Exception as exc:
            raise PddFetchError(
                f"无法连接工具 Chrome CDP（{cdp_url}）：{exc}——请先运行 "
                f"`python scripts/cli.py check` 确认 Chrome 已启动") from exc

    tab = None
    created_tab = False
    try:
        matched = cdp.find_tab(f"goods_id={target.item_id}")
        if matched is not None:
            # 用户已有商品 tab：release 取消跟踪（绝不随连接 close 远程关）
            cdp.release(matched)
            tab = matched
        else:
            tab = cdp.new_tab(target.canonical_url or item_url)
            created_tab = True
            try:
                tab.wait_for_load(timeout=15)
            except Exception:
                pass

        attempts = 2 if login_wait else 1
        for attempt in range(attempts):
            payload = _evaluate_extract(tab, target, timeout_seconds)
            kind, detail = _classify_payload(payload)
            if kind == "ok":
                return _normalize_product(payload.get("goods"),
                                          str(payload.get("source") or ""),
                                          target, payload.get("freightText"))
            if kind == "login" and login_wait and attempt == 0:
                logger.warning("拼多多未登录（%s）——进入登录等待（分级 UX）", detail[:120])
                if not wait_for_login(cdp_url, cdp=cdp,
                                      timeout_seconds=login_wait_seconds):
                    raise PddLoginRequired(
                        f"拼多多登录未完成，无法继续抓取（{LOGIN_HINT}；"
                        "登录页已保留在浏览器，完成登录后重跑本命令即可）")
                # 登录成功 → 回到商品页上下文重取一次
                try:
                    tab.navigate(target.canonical_url or item_url,
                                 wait_until="domcontentloaded", timeout=30)
                    tab.wait_for_load(timeout=15)
                except Exception:
                    pass
                continue
            if kind == "login":
                raise PddLoginRequired(
                    f"拼多多未登录或会话过期（{detail}）——{LOGIN_HINT}")
            if kind == "risk":
                raise PddRiskControlError(
                    f"拼多多风控/验证码拦截（{detail}）——{LOGIN_HINT}，"
                    "或在工具 Chrome 打开该商品页完成人工验证后重试")
            raise PddFetchError(
                f"拼多多页面数据未取到（{_SHAPE_HINT} 三形态均未命中，兜底失败："
                f"{detail}）——拒绝编造，请刷新商品页后重试")
        raise PddFetchError(f"拼多多抓取重试耗尽（登录等待后仍未成功）——{LOGIN_HINT}")
    except PddFetchError:
        raise
    except Exception as exc:
        raise PddFetchError(f"拼多多商品抓取失败: {exc}") from exc
    finally:
        if created_tab and tab is not None:
            try:
                tab.close()
            except Exception:
                pass
        if own_connection:
            try:
                cdp.close()  # type: ignore[union-attr]
            except Exception:
                pass


def wait_for_login(cdp_url: str, *, cdp: CdpConnection | None = None,
                   timeout_seconds: int | None = None,
                   poll_interval: float = 3.0) -> bool:
    """等待拼多多登录完成（taobao_client.wait_for_login 分级 UX 同款）。

    超时下限分级：TTY ≥300s / 非 TTY ≥90s；超时后 TTY 询问「Enter 续等」
    （登录页保留在浏览器，绝不关），非 TTY 直接返回 False。
    登录判定：任一标记（passport/renzheng/login 路径）从 tab URL 消失。

    Returns:
        True=登录完成；False=超时/放弃。
    """
    own_connection = cdp is None
    if own_connection:
        try:
            cdp = CdpConnection(cdp_url)
        except Exception as exc:
            logger.warning("wait_for_login: 无法连接 CDP: %s", exc)
            return False
    tab = None
    created_tab = False
    try:
        try:
            interactive = bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            interactive = False
        floor = 300 if interactive else 90
        timeout_sec = max(int(timeout_seconds or 0), floor)
        start = time.time()

        tab = None
        for pattern in PDD_LOGIN_TAB_PATTERNS:
            tab = cdp.find_tab(pattern)
            if tab is not None:
                break
        if tab is not None:
            cdp.release(tab)  # 用户已开的登录页：只读复用
        else:
            tab = cdp.new_tab(PDD_LOGIN_URL)
            created_tab = True

        def _still_login(url: str) -> bool:
            return (not url) or any(m in url for m in PDD_LOGIN_TAB_PATTERNS)

        unlimited = False
        notified = False
        while True:
            if not unlimited and not notified and time.time() - start >= timeout_sec:
                notified = True
                if interactive:
                    print('\n⏳ 等待拼多多登录超时。登录页已保留在浏览器——'
                          '完成扫码登录后按 Enter 继续等待（Ctrl+C 放弃）...',
                          file=sys.stderr)
                    try:
                        input()
                    except (EOFError, KeyboardInterrupt, OSError):
                        return False
                    unlimited = True
                else:
                    print('\n⏳ 等待拼多多登录超时（无人值守模式）。'
                          '登录页已保留在浏览器，完成登录后重跑本命令即可。',
                          file=sys.stderr)
                    return False
            url = ""
            try:
                url = str(tab.url or "")
            except Exception:
                url = ""
            if not _still_login(url):
                logger.info("拼多多登录完成（当前页 %s）", url[:80])
                return True
            time.sleep(poll_interval)
    except Exception as exc:
        logger.warning("wait_for_login: %s", exc)
        return False
    finally:
        if created_tab and tab is not None:
            try:
                tab.close()
            except Exception:
                pass
        if own_connection:
            try:
                cdp.close()  # type: ignore[union-attr]
            except Exception:
                pass
