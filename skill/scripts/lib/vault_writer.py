"""vault 落盘器 —— 把 skill 的配置/结果/进度落盘成 Markdown 知识库（vault）。

目标：把 skill 的「黑盒」变透明，让 dsh 的 agent 通过读 vault 自动获取状态：
- 配置类（店铺/凭证/类目）→ 命令执行后**自动落盘**（状态必须同步）
- 结果类（采集/选品/上架）→ **商品卡片**落盘（图片 URL + 采购价/运费/利润）
- 进度 → `Active-Context.md` 落盘（长任务实时可见）

平台无关：目录按能力域组织（stores/sourcing/selection/listing），Ozon 专属集中 `ozon/`。
未来加 Amazon → `amazon/`，通用能力零改动。

与 references/ 的分工：references/ = 静态说明书（只读、随版本）；vault/ = 动态工作台（可写、随状态）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

# vault 目录：环境变量 VAULT_DIR，默认项目根 vault/（skill 同级的 ../vault）
_DEFAULT_VAULT = Path(__file__).resolve().parent.parent.parent.parent / "vault"
VAULT_DIR = Path(os.environ.get("VAULT_DIR", str(_DEFAULT_VAULT)))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def write_markdown(rel_path: str, content: str) -> Path:
    """写 Markdown 到 vault 的 rel_path（相对 vault 根）。幂等覆盖，自动建目录。"""
    target = VAULT_DIR / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


# ── 店铺（平台无关）───────────────────────────────────────────────

def render_stores(stores: dict, default: str = "") -> str:
    """店铺列表 → Markdown（已脱敏的摘要，不含明文 key）。"""
    lines = [
        "# 店铺配置",
        "",
        f"> 自动落盘 · 更新于 {_now()} · 凭证已脱敏（明文 key 不落盘）。",
        "",
    ]
    if default:
        lines.append(f"**默认店铺**：`{default}`")
        lines.append("")
    if not stores:
        lines.append("（未配置任何店铺。用 set_store 命令配置。）")
        return "\n".join(lines) + "\n"
    lines.append("| 店铺名 | client_id | API Key | 币种 |")
    lines.append("|---|---|---|---|")
    for name, cfg in stores.items():
        if not isinstance(cfg, dict):
            continue
        cid = str(cfg.get("client_id", "") or "")
        cid_masked = cid[:8] + "***" if cid else "-"
        has_key = bool(cfg.get("api_key"))
        cur = cfg.get("currency", "") or "RUB"
        lines.append(f"| {name} | {cid_masked} | {'✅' if has_key else '❌'} | {cur} |")
    return "\n".join(lines) + "\n"


# ── 商品卡片（平台无关，结果卡片化）───────────────────────────────

def render_product_card(product: dict) -> str:
    """单个商品 → 商品卡片 Markdown（对齐原型图的卡片：图片 + 采购价/运费/利润）。

    期望字段（skill 的 product_summary[] / draft）：
    - title / images[0] / purchase_url / purchase_cost / logistics_cost / price / profit_rate
    """
    title = product.get("title") or product.get("name") or "（无标题）"
    images = product.get("images") or []
    image = images[0] if images else ""
    url = product.get("purchase_url") or product.get("url") or ""
    cost = product.get("purchase_cost")
    logistics = product.get("logistics_cost")
    price = product.get("price")
    profit_rate = product.get("profit_rate")

    lines = [f"## 商品卡片：{title}", ""]
    if image:
        lines.append(f"![商品图]({image})")
        lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("|---|---|")
    if url:
        lines.append(f"| 货源链接 | {url} |")
    if cost is not None:
        lines.append(f"| 采购价 | ¥{cost} |")
    if logistics is not None:
        lines.append(f"| 运费预估 | ¥{logistics} |")
    if price is not None:
        suffix = f"（利润率 {profit_rate:.0%}）" if isinstance(profit_rate, (int, float)) else ""
        lines.append(f"| 售价 | ₽{price}{suffix} |")
    return "\n".join(lines) + "\n"


def render_product_cards(products: list[dict], heading: str = "采集结果") -> str:
    """多个商品 → 一个 Markdown 文件（多张商品卡片）。"""
    lines = [f"# {heading}", "", f"> 落盘于 {_now()}", ""]
    for p in products:
        lines.append(render_product_card(p))
        lines.append("")
    return "\n".join(lines)


# ── 进度（黑盒透明化）─────────────────────────────────────────────

def write_progress(stage: str, detail: str) -> Path:
    """写当前进度到 Active-Context.md（长任务实时可见）。

    例：write_progress("采集", "正在抓取 1688 第 3/10 页")
    """
    content = (
        "# Active Context\n\n"
        f"> 实时进度 · 更新于 {_now()}\n\n"
        f"## 当前任务\n\n- **{stage}**：{detail}\n"
    )
    return write_markdown("00-System/Active-Context.md", content)
