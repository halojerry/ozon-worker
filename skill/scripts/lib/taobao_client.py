"""淘宝/天猫商品抓取适配器 — 跨平台货源 v1 批2（批3 拼多多同模式另建）。

一次 ``fetch_product``：CDP 打开/复用商品页 → 页上下文内 fetch mtop
``mtop.taobao.pcdetail.data.get/1.0/``（appKey=12574478，签名 token 取页内
``_m_h5_tk`` cookie，**免手签**——签名 md5 在页内 JS 完成）→ 归一为与 1688
链路 ``enrich_product_with_cdp().data`` 同形的统一 ProductInfo（title/price/
images/sku_details/option_groups/attributes/shipping/weight_grams），供
cloud_probe 信封组装复用（``_collapse_variants_to_single`` /
``_validate_and_fix_product_data`` 直接消费，见批4 接线）。

参考实现（字段权威）：goldminer-collector-extension v2.43 ``background.js``
``buildTbTmallApiRequest`` / ``validateTbTmallDetailResult`` /
``extractTbTmallAttributes``（:13972/:14034/:14141）；
``docs/competitor/maozier-plugin-full.md`` §2.5（淘宝/天猫同 mtop 协议，
仅 h5api 域不同：taobao→h5api.m.taobao.com，tmall→h5api.m.tmall.com）。

纪律（红线）：
- **失败出声、绝不编造字段**——标题/图/价格任一缺失、mtop ret 非 SUCCESS、
  风控页/滑块、未登录 → 抛人话异常（TaobaoFetchError 家族），绝不返回半猜
  数据；运费未知 → shipping 缺 freightCny 键（不是 0）；重量缺失 →
  ``weight_grams=None``（不是 0）。
- **登录态用用户自己 Chrome 会话（CDP）**，本模块不搬 cookie、不落任何
  凭证；``wait_for_login`` 复用 skill-login-wait 分级 UX 先例（TTY ≥300s /
  非 TTY ≥90s + Enter 续等，登录页保留在浏览器）——对齐
  service.py ``_wait_for_login_session`` v0.63.3。
- tab 复用纪律对齐 ``ozon_widget._tab_for_variant_truth``：find_tab 命中
  用户 tab → release()（绝不随连接 close 远程关用户页）；自建 tab 用后关闭。
- 页内嵌 JSON（React VO）兜底**有意不实现**：无已验证的字段权威（ozonAI
  参考未在案），按「绝不半猜」纪律宁缺毋滥——mtop 失败即大声报错。

测试：``tests/test_taobao_client_normalize.py``（全 mock CDP，手工构造 mtop
夹具，零真实 cookie/凭证/截图入库）。
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

logger = logging.getLogger(__name__)

TAOBAO_LOGIN_URL = "https://login.taobao.com/member/login.jhtml"
MTOP_APP_KEY = "12574478"  # 必须与下方 _MTOP_FETCH_JS 内 appKey 一致
MTOP_HOSTS = {"taobao": "h5api.m.taobao.com", "tmall": "h5api.m.tmall.com"}

# 风控/滑块选择器：service.py:804-810 先例 + 淘宝系 baxia 风控容器
_RISK_CONTROL_MARKERS = ("验证", "滑块", "风控", "安全")


class TaobaoFetchError(RuntimeError):
    """淘宝/天猫抓取失败（人话消息，含 mtop ret 原文——失败出声）。"""


class TaobaoLoginRequired(TaobaoFetchError):
    """未登录/会话过期 → 请在工具 Chrome 登录淘宝后重试。"""


class TaobaoRiskControlError(TaobaoFetchError):
    """风控/滑块/验证码拦截 → 请在工具 Chrome 完成人工验证后重试。"""


# ────────────────────────── 纯函数（可独立测试）──────────────────────────


_FREE_SHIPPING_RE = re.compile(r"包邮|免运费|免邮")
_FREIGHT_KW_RE = re.compile(r"运费|快递|物流|配送|邮费")
_FREIGHT_CURRENCY_RE = re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)")
_FREIGHT_YUAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")


def parse_freight_cny(text: str | None) -> float | None:
    """运费文案 → CNY 数额。

    包邮/免运费/免邮 → 0.0；「运费¥8.00」「快递费 10元」→ 数额；
    关键词缺席或解析不出 → None（**未知≠0**，调用方据此省略 freightCny）。
    口径对齐 goldminer ``parse1688FreightAmount``（:13225）。
    """
    normalized = str(text or "").strip()
    if not normalized:
        return None
    if _FREE_SHIPPING_RE.search(normalized):
        return 0.0
    if not _FREIGHT_KW_RE.search(normalized):
        return None
    m = _FREIGHT_CURRENCY_RE.search(normalized)
    if m:
        return float(m.group(1))
    m = _FREIGHT_YUAN_RE.search(normalized)
    if m:
        return float(m.group(1))
    return None


def classify_mtop_failure(ret_values: list | None) -> str | None:
    """mtop ret 数组分类（镜像 goldminer ``validateTbTmallDetailResult``）。

    "login"=TOKEN/SESSION 过期（需登录）；"risk"=USER_VALIDATE（人机验证）；
    "error"=其他 FAIL/ILLEGAL_ACCESS；None=无失败（SUCCESS）。
    """
    for value in (ret_values or []):
        v = str(value or "").strip()
        if not v:
            continue
        if "USER_VALIDATE" in v.upper():
            return "risk"
        if re.search(r"TOKEN|SESSION_EXPIRED", v, re.I):
            return "login"
        if re.search(r"(?:^|::)FAIL|ILLEGAL_ACCESS", v, re.I):
            return "error"
    return None


def _https_url(value) -> str:
    """mtop 协议相对 URL（//img.alicdn.com/...）→ https 绝对化。"""
    u = str(value or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    return u


def _weight_grams_from_attributes(attributes: list[dict]) -> int | None:
    """属性表 重量/净重/毛重 → 克；缺失 → None（绝不 0）。"""
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|千克|公斤|g|克)", re.I)
    for a in attributes or []:
        name = str(a.get("name", ""))
        if not any(k in name for k in ("重量", "净重", "毛重", "weight")):
            continue
        m = pattern.search(str(a.get("value", "")))
        if not m:
            continue
        num = float(m.group(1))
        unit = m.group(2).lower()
        if unit in ("kg", "千克", "公斤"):
            return int(round(num * 1000))
        return int(round(num))
    return None


def _extract_attributes(data: dict, platform: str) -> list[dict]:
    """商品属性（name/value 列表）——权威路径同 goldminer
    ``extractTbTmallAttributes``（:14141）：taobao 走 plusViewVO.industryParamVO.
    basicParamList；tmall 走 componentsVO.extensionInfoVO.infos BASE_PROPS。"""
    out: list[dict] = []
    if platform == "tmall":
        infos = (((data.get("componentsVO") or {}).get("extensionInfoVO") or {})
                 .get("infos")) or []
        for block in infos:
            if not isinstance(block, dict) or block.get("type") != "BASE_PROPS":
                continue
            for it in block.get("items") or []:
                name = str(it.get("title") or it.get("name") or "").strip()
                raw = it.get("text")
                if isinstance(raw, list):
                    value = ";".join(str(x).strip() for x in raw if str(x).strip())
                else:
                    value = str(raw if raw not in (None, "") else
                                it.get("value") or "").strip()
                if name and value:
                    out.append({"name": name, "value": value})
    else:
        basics = (((data.get("plusViewVO") or {}).get("industryParamVO") or {})
                  .get("basicParamList")) or []
        for it in basics:
            name = str(it.get("propertyName") or it.get("name") or "").strip()
            value = str(it.get("valueName") or it.get("value") or "").strip()
            if name and value:
                out.append({"name": name, "value": value})
    return out


def _normalize_skus(data: dict) -> tuple[list[dict], list[dict]]:
    """skuBase.props/skus + skuCore.sku2info → (sku_details, option_groups)。

    - option_groups（≈1688 optionGroups）：props → {name, values:[{name,image}]}
    - sku_details：skus[].propPath（pid:vid;…）→ 值名拼名 + sku2info 价格 +
      首个带图值的 SK U 图（同 goldminer :20585-:20660 口径）
    """
    sku_base = data.get("skuBase") or {}
    sku_core = data.get("skuCore") or {}
    sku2info = sku_core.get("sku2info") or {}
    props = sku_base.get("props") or []

    lookup: dict[str, dict[str, dict]] = {}
    option_groups: list[dict] = []
    for prop in props:
        if not isinstance(prop, dict):
            continue
        pid = str(prop.get("pid") or "")
        values: list[dict] = []
        for v in prop.get("values") or []:
            vid = str(v.get("vid") or "")
            entry = {
                "name": str(v.get("name") or "").strip(),
                "image": _https_url(v.get("image")),
            }
            if pid and vid and entry["name"]:
                lookup.setdefault(pid, {})[vid] = entry
            if entry["name"]:
                # 无图值不携带 image 键（与 1688 optionGroups 值形态一致——缺省=无）
                values.append({"name": entry["name"], **(
                    {"image": entry["image"]} if entry["image"] else {})})
        if values:
            option_groups.append({
                "name": str(prop.get("name") or "规格").strip(),
                "values": values,
            })

    sku_details: list[dict] = []
    for sku in sku_base.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        sku_id = str(sku.get("skuId") or "").strip()
        if not sku_id:
            continue
        names: list[str] = []
        image = ""
        for token in str(sku.get("propPath") or "").split(";"):
            if not token:
                continue
            pid, _, vid = token.partition(":")
            meta = (lookup.get(pid) or {}).get(vid) or {}
            if meta.get("name"):
                names.append(meta["name"])
            if not image and meta.get("image"):
                image = meta["image"]
        price = _price_number(((sku2info.get(sku_id) or {}).get("price") or {}))
        sku_details.append({
            "sku_id": sku_id,
            "name": " ".join(names) if names else sku_id,
            "price": price,
            "image": image,
        })
    return sku_details, option_groups


def _price_number(price_info: dict) -> float | None:
    """skuCore 价格块 → float；解析不出 → None。"""
    for key in ("priceText", "price", "text"):
        raw = price_info.get(key)
        if raw in (None, ""):
            continue
        m = re.search(r"\d+(?:\.\d+)?", str(raw))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                continue
    return None


def _normalize_product(mtop_body: dict, target: PlatformTarget,
                       page_freight_text: str | None) -> dict:
    """mtop pcdetail 响应体 → 统一 ProductInfo（1688 enrich data 同形）。

    纯函数。标题/图/价格缺失即抛 TaobaoFetchError——失败出声，绝不编造。
    """
    data = mtop_body.get("data") or {}
    ret = [str(x) for x in (mtop_body.get("ret") or [])]
    ret_hint = ";".join(ret) if ret else "ret 缺失"
    item = data.get("item") or {}
    if not isinstance(item, dict):
        item = {}
    title = str(item.get("title") or "").strip()
    images = [_https_url(u) for u in (item.get("images") or []) if u]
    if not title:
        raise TaobaoFetchError(
            f"淘宝详情响应缺商品标题（ret={ret_hint}）——拒绝编造，请重试或检查商品有效性")
    if not images:
        raise TaobaoFetchError(
            f"淘宝详情响应无可用商品图（ret={ret_hint}）——拒绝编造，请重试或检查商品有效性")

    sku_details, option_groups = _normalize_skus(data)
    prices = [float(s["price"]) for s in sku_details
              if s.get("price") and float(s["price"]) > 0]
    price_block_text = str(
        (((data.get("price") or {}).get("price") or {}).get("priceText")) or "")
    if prices:
        lo, hi = min(prices), max(prices)
    else:
        # sku2info 空 → price.priceText 区间块兜底（"12.90-25.00"）
        block_numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", price_block_text)]
        if not block_numbers:
            raise TaobaoFetchError(
                f"淘宝详情响应无可用价格（skuCore 空，price 块={price_block_text!r}，"
                f"ret={ret_hint}）——拒绝编造")
        lo, hi = min(block_numbers), max(block_numbers)
    price_str = f"{lo:.2f}"

    attributes = _extract_attributes(data, target.platform)
    brand = ""
    for a in attributes:
        if a["name"] == "品牌":
            brand = a["value"]
            break
    seller = ""
    seller_raw = data.get("seller")
    if isinstance(seller_raw, dict):
        seller = str(seller_raw.get("shopName") or "").strip()

    shipping: dict[str, Any] = {}
    freight = parse_freight_cny(page_freight_text)
    if freight is not None:
        shipping["freightCny"] = float(freight)
        shipping["displayText"] = str(page_freight_text or "").strip()
        if freight == 0.0:
            shipping["freeShipping"] = True

    return {
        "platform": target.platform,
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
        "weight_grams": _weight_grams_from_attributes(attributes),
        "seller": seller,
        "brand": brand,
    }


# ────────────────────────── 页内 mtop fetch JS ──────────────────────────
# 单次 evaluate（await_promise）：页上下文自带 _m_h5_tk cookie 与登录态，
# 签名 md5 在页内完成（免 Python 手签）。返回 JSON 字符串：
#   {status, error, message, captcha, url, ret[], body, freightText}
_MTOP_FETCH_JS = r"""(async () => {
    function add32(a, b) { return (a + b) & 0xffffffff; }
    function cmn(q, a, b, x, s, t) { a = add32(add32(a, q), add32(x, t)); return add32((a << s) | (a >>> (32 - s)), b); }
    function ff(a, b, c, d, x, s, t) { return cmn((b & c) | (~b & d), a, b, x, s, t); }
    function gg(a, b, c, d, x, s, t) { return cmn((b & d) | (c & ~d), a, b, x, s, t); }
    function hh(a, b, c, d, x, s, t) { return cmn(b ^ c ^ d, a, b, x, s, t); }
    function ii(a, b, c, d, x, s, t) { return cmn(c ^ (b | ~d), a, b, x, s, t); }
    function md5cycle(x, k) {
      let a = x[0], b = x[1], c = x[2], d = x[3];
      a = ff(a, b, c, d, k[0], 7, -680876936); d = ff(d, a, b, c, k[1], 12, -389564586);
      c = ff(c, d, a, b, k[2], 17, 606105819); b = ff(b, c, d, a, k[3], 22, -1044525330);
      a = ff(a, b, c, d, k[4], 7, -176418897); d = ff(d, a, b, c, k[5], 12, 1200080426);
      c = ff(c, d, a, b, k[6], 17, -1473231341); b = ff(b, c, d, a, k[7], 22, -45705983);
      a = ff(a, b, c, d, k[8], 7, 1770035416); d = ff(d, a, b, c, k[9], 12, -1958414417);
      c = ff(c, d, a, b, k[10], 17, -42063); b = ff(b, c, d, a, k[11], 22, -1990404162);
      a = ff(a, b, c, d, k[12], 7, 1804603682); d = ff(d, a, b, c, k[13], 12, -40341101);
      c = ff(c, d, a, b, k[14], 17, -1502002290); b = ff(b, c, d, a, k[15], 22, 1236535329);
      a = gg(a, b, c, d, k[1], 5, -165796510); d = gg(d, a, b, c, k[6], 9, -1069501632);
      c = gg(c, d, a, b, k[11], 14, 643717713); b = gg(b, c, d, a, k[0], 20, -373897302);
      a = gg(a, b, c, d, k[5], 5, -701558691); d = gg(d, a, b, c, k[10], 9, 38016083);
      c = gg(c, d, a, b, k[15], 14, -660478335); b = gg(b, c, d, a, k[4], 20, -405537848);
      a = gg(a, b, c, d, k[9], 5, 568446438); d = gg(d, a, b, c, k[14], 9, -1019803690);
      c = gg(c, d, a, b, k[3], 14, -187363961); b = gg(b, c, d, a, k[8], 20, 1163531501);
      a = gg(a, b, c, d, k[13], 5, -1444681467); d = gg(d, a, b, c, k[2], 9, -51403784);
      c = gg(c, d, a, b, k[7], 14, 1735328473); b = gg(b, c, d, a, k[12], 20, -1926607734);
      a = hh(a, b, c, d, k[5], 4, -378558); d = hh(d, a, b, c, k[8], 11, -2022574463);
      c = hh(c, d, a, b, k[11], 16, 1839030562); b = hh(b, c, d, a, k[14], 23, -35309556);
      a = hh(a, b, c, d, k[1], 4, -1530992060); d = hh(d, a, b, c, k[4], 11, 1272893353);
      c = hh(c, d, a, b, k[7], 16, -155497632); b = hh(b, c, d, a, k[10], 23, -1094730640);
      a = hh(a, b, c, d, k[13], 4, 681279174); d = hh(d, a, b, c, k[0], 11, -358537222);
      c = hh(c, d, a, b, k[3], 16, -722521979); b = hh(b, c, d, a, k[6], 23, 76029189);
      a = hh(a, b, c, d, k[9], 4, -640364487); d = hh(d, a, b, c, k[12], 11, -421815835);
      c = hh(c, d, a, b, k[15], 16, 530742520); b = hh(b, c, d, a, k[2], 23, -995338651);
      a = ii(a, b, c, d, k[0], 6, -198630844); d = ii(d, a, b, c, k[7], 10, 1126891415);
      c = ii(c, d, a, b, k[14], 15, -1416354905); b = ii(b, c, d, a, k[5], 21, -57434055);
      a = ii(a, b, c, d, k[12], 6, 1700485571); d = ii(d, a, b, c, k[3], 10, -1894986606);
      c = ii(c, d, a, b, k[10], 15, -1051523); b = ii(b, c, d, a, k[1], 21, -2054922799);
      a = ii(a, b, c, d, k[8], 6, 1873313359); d = ii(d, a, b, c, k[15], 10, -30611744);
      c = ii(c, d, a, b, k[6], 15, -1560198380); b = ii(b, c, d, a, k[13], 21, 1309151649);
      a = ii(a, b, c, d, k[4], 6, -145523070); d = ii(d, a, b, c, k[11], 10, -1120210379);
      c = ii(c, d, a, b, k[2], 15, 718787259); b = ii(b, c, d, a, k[9], 21, -343485551);
      x[0] = add32(a, x[0]); x[1] = add32(b, x[1]); x[2] = add32(c, x[2]); x[3] = add32(d, x[3]);
    }
    function md5blk(s) {
      const blks = [];
      for (let i = 0; i < 64; i += 4) {
        blks[i >> 2] = s.charCodeAt(i) + (s.charCodeAt(i + 1) << 8) + (s.charCodeAt(i + 2) << 16) + (s.charCodeAt(i + 3) << 24);
      }
      return blks;
    }
    function md51(s) {
      const n = s.length;
      const state = [1732584193, -271733879, -1732584194, 271733878];
      let i;
      for (i = 64; i <= n; i += 64) md5cycle(state, md5blk(s.substring(i - 64, i)));
      s = s.substring(i - 64);
      const tail = new Array(16).fill(0);
      for (i = 0; i < s.length; i += 1) tail[i >> 2] |= s.charCodeAt(i) << ((i % 4) << 3);
      tail[i >> 2] |= 0x80 << ((i % 4) << 3);
      if (i > 55) { md5cycle(state, tail); for (i = 0; i < 16; i += 1) tail[i] = 0; }
      tail[14] = n * 8;
      md5cycle(state, tail);
      return state;
    }
    function rhex(n) {
      const chr = '0123456789abcdef';
      let s = '';
      for (let j = 0; j < 4; j += 1) s += chr.charAt((n >> (j * 8 + 4)) & 0x0f) + chr.charAt((n >> (j * 8)) & 0x0f);
      return s;
    }
    function md5(input) { return md51(String(input)).map(rhex).join(''); }

    function detectCaptcha() {
      try {
        return !!(
          document.querySelector('#baxia-dialog-content') ||
          document.querySelector('.nc-container') ||
          document.querySelector('#nc_1_wrapper') ||
          document.querySelector('.slide-verify') ||
          document.querySelector('.captcha') ||
          document.querySelector('#J_Checkcode') ||
          (document.querySelector('.J_MIDDLEWARE_FRAMEWRAP') && document.title.indexOf('验证') !== -1)
        );
      } catch (e) { return false; }
    }
    function extractFreightText() {
      try {
        const bt = (document.body && (document.body.innerText || document.body.textContent)) || '';
        let m = bt.match(/包邮|免运费|免邮/);
        if (!m) m = bt.match(/(?:运费|快递费|物流费|邮费)[^。；;]{0,12}/);
        return m ? m[0] : null;
      } catch (e) { return null; }
    }
    try {
      const cookieMatch = document.cookie.match(/(?:^|;\s*)_m_h5_tk=([^;]+)/);
      const rawToken = cookieMatch ? decodeURIComponent(cookieMatch[1]) : '';
      const token = rawToken.includes('_') ? rawToken.split('_')[0] : rawToken;
      if (!token) {
        return JSON.stringify({ status: 0, error: 'NO_MTOP_TOKEN', message: '页内无 _m_h5_tk cookie（未登录或 cookie 未就绪）', captcha: detectCaptcha(), url: location.href, ret: [], body: null, freightText: extractFreightText() });
      }
      const itemId = __ITEM_ID__;
      const host = __MTOP_HOST__;
      const appKey = '12574478';
      const timestamp = Date.now();
      const params = new URLSearchParams(window.location.search || '');
      const exParams = JSON.stringify({
        abbucket: params.get('abbucket') || '',
        id: itemId,
        ns: params.get('ns') || params.get('rn') || '',
        spm: params.get('spm') || '',
        scm: params.get('scm') || '',
        queryParams: (window.location.search || '').replace(/^\?/, ''),
        domain: window.location.origin,
        path_name: window.location.pathname || '/item.htm',
      });
      const payload = JSON.stringify({ id: itemId, detail_v: '3.3.2', exParams: exParams });
      const sign = md5(token + '&' + timestamp + '&' + appKey + '&' + payload);
      const url = 'https://' + host + '/h5/mtop.taobao.pcdetail.data.get/1.0/?jsv=2.6.1&appKey=' + appKey +
        '&t=' + timestamp + '&sign=' + sign + '&api=mtop.taobao.pcdetail.data.get&v=1.0' +
        '&ttid=2022%40taobao_litepc_9.17.0&isSec=0&ecode=0&timeout=10000&preventFallback=true' +
        '&AntiFlood=true&AntiCreep=true&H5Request=true&type=json&dataType=json&data=' + escape(payload);
      const resp = await fetch(url, { credentials: 'include' });
      const text = await resp.text();
      let body;
      try { body = JSON.parse(text); }
      catch (e) {
        return JSON.stringify({ status: resp.status, error: 'MTOP_NON_JSON', message: 'mtop 网关返回非 JSON（HTTP ' + resp.status + '）', captcha: detectCaptcha(), url: location.href, ret: [], body: null, freightText: extractFreightText() });
      }
      return JSON.stringify({ status: resp.status, error: null, message: null, captcha: detectCaptcha(), url: location.href, ret: Array.isArray(body && body.ret) ? body.ret : [], body: body, freightText: extractFreightText() });
    } catch (e) {
      return JSON.stringify({ status: 0, error: 'JS_EXCEPTION', message: String((e && e.message) || e), captcha: detectCaptcha(), url: location.href, ret: [], body: null, freightText: null });
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


def _evaluate_fetch(tab, target: PlatformTarget, timeout_seconds: int) -> dict:
    host = MTOP_HOSTS.get(target.platform, MTOP_HOSTS["taobao"])
    js = (_MTOP_FETCH_JS
          .replace("__ITEM_ID__", json.dumps(str(target.item_id)))
          .replace("__MTOP_HOST__", json.dumps(host)))
    raw = tab.evaluate(js, await_promise=True, timeout=timeout_seconds)
    payload = _safe_json(raw)
    if not isinstance(payload, dict):
        raise TaobaoFetchError(
            "淘宝详情页内 mtop fetch 返回无法解析（非 JSON）——页面可能被风控拦截，"
            "请在工具 Chrome 打开商品页人工确认后重试")
    return payload


def _classify_payload(payload: dict) -> tuple[str, str]:
    """evaluate 结果 → (kind, detail)。kind ∈ ok/login/risk/error。"""
    error = str(payload.get("error") or "").strip()
    url = str(payload.get("url") or "")
    if error == "NO_MTOP_TOKEN" or error == "MTOP_NON_JSON" or error == "JS_EXCEPTION":
        detail = str(payload.get("message") or error)
        if error == "NO_MTOP_TOKEN":
            return "login", detail
        return "error", detail
    if "login.taobao.com" in url or "login.tmall.com" in url:
        return "login", f"商品页被重定向到登录页（{url}）"
    if payload.get("captcha"):
        return "risk", "检测到风控/滑块/验证码拦截（baxia/nc 容器）"
    kind = classify_mtop_failure(payload.get("ret"))
    if kind:
        ret_text = ";".join(str(x) for x in (payload.get("ret") or []))
        return kind, ret_text
    return "ok", ""


def fetch_product(cdp_url: str, item_url: str, target: PlatformTarget | None = None,
                  *, cdp: CdpConnection | None = None, timeout_seconds: int = 45,
                  login_wait: bool = False,
                  login_wait_seconds: int | None = None) -> dict:
    """抓取淘宝/天猫商品 → 统一 ProductInfo（见模块 docstring）。

    Args:
        cdp_url: 工具 Chrome CDP 地址（自建连接时用；传入 cdp 时忽略）。
        item_url: 淘宝/天猫商品 URL。
        target: parse_platform_url 结果（None 时内部解析）。
        cdp: 可选外部 CdpConnection（归调用方所有，本函数不关它）。
        login_wait: True 时未登录 → ``wait_for_login``（分级 UX）后重试一次。

    Raises:
        TaobaoLoginRequired: 未登录/会话过期。
        TaobaoRiskControlError: 风控/滑块拦截。
        TaobaoFetchError: 其他抓取/归一失败（人话消息，绝不半猜数据）。
    """
    target = target or parse_platform_url(item_url)
    if target is None or target.platform not in ("taobao", "tmall"):
        raise TaobaoFetchError(
            f"taobao_client 只支持淘宝/天猫商品 URL，收到: {item_url!r}"
            + ("（拼多多适配器在批3 提供）" if target and target.platform == "pdd" else ""))

    own_connection = cdp is None
    if own_connection:
        try:
            cdp = CdpConnection(cdp_url)
        except Exception as exc:
            raise TaobaoFetchError(
                f"无法连接工具 Chrome CDP（{cdp_url}）：{exc}——请先运行 "
                f"`python scripts/cli.py check` 确认 Chrome 已启动") from exc

    tab = None
    created_tab = False
    try:
        matched = cdp.find_tab(f"id={target.item_id}")
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
            payload = _evaluate_fetch(tab, target, timeout_seconds)
            kind, detail = _classify_payload(payload)
            if kind == "ok":
                return _normalize_product(payload.get("body") or {}, target,
                                          payload.get("freightText"))
            if kind == "login" and login_wait and attempt == 0:
                logger.warning("淘宝未登录（%s）——进入登录等待（分级 UX）", detail[:120])
                if not wait_for_login(cdp_url, cdp=cdp,
                                      timeout_seconds=login_wait_seconds):
                    raise TaobaoLoginRequired(
                        "淘宝登录未完成，无法继续抓取（登录页已保留在浏览器，"
                        "完成登录后重跑本命令即可）")
                # 登录成功 → 回到商品页上下文重取一次
                try:
                    tab.navigate(target.canonical_url or item_url,
                                 wait_until="domcontentloaded", timeout=30)
                    tab.wait_for_load(timeout=15)
                except Exception:
                    pass
                continue
            if kind == "login":
                raise TaobaoLoginRequired(
                    f"淘宝/天猫未登录或会话过期（{detail}）——请在工具 Chrome 登录后重试")
            if kind == "risk":
                raise TaobaoRiskControlError(
                    f"淘宝/天猫风控拦截（{detail}）——请在工具 Chrome 打开该商品页完成"
                    f"滑块/验证码人工验证后重试")
            raise TaobaoFetchError(
                f"淘宝详情接口返回失败（ret={detail}）——失败出声，拒绝编造数据")
        raise TaobaoFetchError("淘宝详情抓取重试耗尽（登录等待后仍未成功）")
    except TaobaoFetchError:
        raise
    except Exception as exc:
        raise TaobaoFetchError(f"淘宝/天猫商品抓取失败: {exc}") from exc
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
    """等待淘宝/天猫登录完成（skill-login-wait 分级 UX 先例复用）。

    超时下限分级（对齐 service._wait_for_login_session v0.63.3）：TTY ≥300s
    （人工在跑）/ 非 TTY ≥90s（浏览器在本机可见，给现场用户留窗口）；超时后
    TTY 询问「Enter 续等」（登录页保留在浏览器，绝不关），非 TTY 直接返回
    False（登录页保留，重跑即可复用登录态）。

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

        tab = cdp.find_tab("login.taobao.com") or cdp.find_tab("login.tmall.com")
        if tab is not None:
            cdp.release(tab)  # 用户已开的登录页：只读复用
        else:
            tab = cdp.new_tab(TAOBAO_LOGIN_URL)
            created_tab = True

        unlimited = False
        notified = False
        while True:
            if not unlimited and not notified and time.time() - start >= timeout_sec:
                notified = True
                if interactive:
                    print('\n⏳ 等待淘宝登录超时。登录页已保留在浏览器——'
                          '完成扫码登录后按 Enter 继续等待（Ctrl+C 放弃）...',
                          file=sys.stderr)
                    try:
                        input()
                    except (EOFError, KeyboardInterrupt, OSError):
                        return False
                    unlimited = True
                else:
                    print('\n⏳ 等待淘宝登录超时（无人值守模式）。'
                          '登录页已保留在浏览器，完成登录后重跑本命令即可。',
                          file=sys.stderr)
                    return False
            url = ""
            try:
                url = str(tab.url or "")
            except Exception:
                url = ""
            if url and "login.taobao.com" not in url and "login.tmall.com" not in url:
                logger.info("淘宝登录完成（当前页 %s）", url[:80])
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
