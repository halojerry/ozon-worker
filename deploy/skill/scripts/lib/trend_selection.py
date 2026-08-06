"""趋势驱动选品（v0.25 S3，参考 ozon-product-selection）：
市场信息（--market-info 文件 / SEARXNG）→ AI 提炼细分关键词（严格 JSON）
→ AK 关键词搜索（并发≤3，满 3 即停）→ 模板渲染。
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

import requests

logger = logging.getLogger(__name__)

KEYWORD_PROMPT = """根据以下关于"{category}"在Ozon平台的市场信息，提取5-8个最有潜力的细分市场关键词。
要求：
1. 关键词要具体到可直接搜索1688的程度（如"儿童益智积木"而不是"玩具"）
2. 优先选择：竞争较少、需求增长、利润空间大的细分方向
3. 每个关键词附带一句话说明为什么有潜力
4. 输出中文关键词（用于1688搜索）
输出格式（严格JSON）：
[
  {{"keyword": "关键词", "reason": "潜力原因"}},
  ...
]"""


def collect_market_info(category: str, market_info_file: str = "", searxng_url: str = "") -> str:
    if market_info_file and os.path.exists(market_info_file):
        with open(market_info_file, "r", encoding="utf-8") as f:
            return f.read()
    if searxng_url:
        try:
            resp = requests.get(
                f"{searxng_url.rstrip('/')}/search",
                params={"q": f"{category} Ozon 热门趋势 蓝海 细分品类 2025", "format": "json"},
                timeout=20,
            )
            if resp.status_code == 200:
                results = (resp.json().get("results") or [])[:5]
                return "\n".join(
                    f"{r.get('title','')}\n{r.get('content','')}" for r in results
                )
        except Exception as e:
            logger.warning("SearXNG 搜索失败: %s", e)
    return f"（未提供市场信息：请用 --market-info <文件> 传入 web_search 结果，或配置 SEARXNG_URL）品类：{category}"


def parse_keywords_json(raw: str) -> list[dict]:
    text = str(raw or "").strip()
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI 关键词输出不是合法 JSON: {e}") from e
    if not isinstance(data, list):
        raise ValueError("AI 关键词输出应为 JSON 数组")
    return [{"keyword": str(d.get("keyword", "")).strip(), "reason": str(d.get("reason", "")).strip()}
            for d in data if isinstance(d, dict) and d.get("keyword")]


def summarize_keywords(token: str, market_info: str, category: str) -> list[dict]:
    from scripts.lib.mxou_chat import call_chat
    raw = call_chat(
        token,
        "你是跨境电商选品专家。只输出严格 JSON，不要任何额外文字。",
        KEYWORD_PROMPT.format(category=category) + "\n\n市场信息：\n" + market_info[:6000],
    )
    if not raw:
        raise ValueError("AI 关键词总结失败（mxou chat 无返回）")
    return parse_keywords_json(raw)


def _search_one(kw: dict, filters: dict) -> Any | None:
    from scripts.lib import ak_1688_client as ak
    items = ak.search_products(kw["keyword"], **filters)
    return items[0] if items else None


def search_by_keywords(keywords: list[dict], max_results: int = 3, **filters) -> list[dict]:
    """并发搜索（≤3 在飞），无结果继续下一个关键词，满 max_results 即停。"""
    results: list[dict] = []
    idx = 0

    def _submit(i):
        if i < len(keywords):
            pending[pool.submit(_search_one, keywords[i], filters)] = keywords[i]

    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {}
        for _i in range(min(3, len(keywords))):
            _submit(_i)
        idx = 3
        while pending and len(results) < max_results:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            none_cnt = 0
            for fut in done:
                kw = pending.pop(fut)
                try:
                    item = fut.result()
                except Exception as e:
                    logger.warning("关键词 %s 搜索失败: %s", kw["keyword"], e)
                    item = None
                if item:
                    results.append({"keyword": kw["keyword"], "reason": kw.get("reason", ""), "item": item})
                    if len(results) >= max_results:
                        break
                else:
                    none_cnt += 1  # 无结果 → 补位下一个关键词（并发≤3）
            if len(results) >= max_results:
                break
            for _ in range(none_cnt):
                if idx < len(keywords):
                    _submit(idx)
                    idx += 1
    return results[:max_results]


def render_results(results: list[dict]) -> str:
    blocks = []
    for r in results:
        it = r["item"]
        skus = it.get("skus") or []
        sku_rows = "".join(
            f"| {s.get('name','')} | {s.get('price','')} | {s.get('suggestedPrice','')} | {s.get('stock','')} |\n"
            for s in skus
        ) if skus else "| （未拉取 SKU，用 --with-skus 获取） | | | |\n"
        blocks.append(
            f"### 🔥 细分市场：{r['keyword']}\n"
            f"> 潜力原因：{r.get('reason','')}\n\n"
            f"![商品图片]({it.get('image_url') or ''})\n\n"
            f"- **商品名称**：{it.get('title','')}\n"
            f"- **价格**：¥{it.get('price','')}\n"
            f"- **起批量**：{it.get('moq','')}\n"
            f"- **发货地**：{it.get('location','')}\n"
            f"- **48H揽收率**：{it.get('ship_rate_48h','')}\n"
            f"- **近一年销量**：{it.get('sales','')}\n"
            f"- **🔗 [查看商品]({it.get('detail_url','')})**\n\n"
            f"供应商：{it.get('supplier','')}（{'、'.join(it.get('supplier_tags') or [])}）\n\n"
            f"**SKU明细及建议售价（3倍定价）：**\n\n"
            f"| SKU名称 | 拿货价(¥) | 建议售价(¥) | 库存 |\n"
            f"|---------|----------|------------|------|\n"
            f"{sku_rows}"
        )
    summary_rows = "\n".join(
        f"| {r['keyword']} | {r['item'].get('title','')} | ¥{r['item'].get('price','')} "
        f"| {r['item'].get('sales','')} | {r['item'].get('supplier','')} | [查看]({r['item'].get('detail_url','')}) |"
        for r in results
    )
    blocks.append(
        "## 汇总表\n\n"
        "| 细分市场 | 商品 | 价格 | 销量 | 供应商 | 链接 |\n"
        "|---------|------|------|------|--------|------|\n"
        f"{summary_rows}"
    )
    return "\n\n".join(blocks)
