"""价差守卫（v0.73 · Issue5b worker 侧防线）——终价 vs 选品锚价倍数校验唯一入口。

生产实证背景（2026-09）：¥1.5 塑料转盘被客户本地选品错配成 3418₽ 的 Ozon 竞品，
上架 approved 后卖 35₽。**该例信封 discovery_meta 为空（graph 流不带锚），本守卫
拦不到它**——真实上游防线在 skill 侧匹配（另行任务）。本守卫只保护「带锚」流
（discover / extensions.discovery_meta 在场的信封），无锚恒 ok，零误杀。

改前必读：
- 锚价取 ``discovery_meta.ozon_price``，退 ``min_competing_price``；非法/≤0 一律
  视为无锚 → ``("ok", {})``。
- ``final_price <= 0`` 是定价层自己的 bug（compute_price 自有除零兜底），本守卫
  **不越界**返回 ``("ok", {})``——在这里拦只会混淆失败归因（pricing 层问题应
  由 [PRICING_FAILED] 异常路径报，而非「货源错配」误导排查方向）。
- 判定口径 ``ratio = anchor / final_price``：终价远低于锚价是货源错配的典型形状
  （把 3418₽ 竞品的货错配成 ¥1.5 地摊货）；终价**高于**锚价不拦（贵卖不伤人，
  竞品价本就只是参考）。
- 阈值 env 可覆盖：``PRICE_GAP_BLOCK_RATIO``（默认 10）/ ``PRICE_GAP_WARN_RATIO``
  （默认 3）。每次调用时读 env（非 import 时），热加载/测试 monkeypatch 均生效。
"""
import os
from typing import Any, Dict, Optional, Tuple

BLOCK_RATIO = 10.0  # env PRICE_GAP_BLOCK_RATIO 可覆盖
WARN_RATIO = 3.0    # env PRICE_GAP_WARN_RATIO 可覆盖

_ENV_BLOCK = "PRICE_GAP_BLOCK_RATIO"
_ENV_WARN = "PRICE_GAP_WARN_RATIO"


def _ratio_from_env(name: str, default: float) -> float:
    """读 env 阈值；非法/≤0 回落默认（配置错误不能把守卫变成恒拦截/恒放行）。"""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val > 0 else default


def get_ratios() -> Tuple[float, float]:
    """(block_ratio, warn_ratio)，调用时读 env（测试可 monkeypatch.setenv）。"""
    return (
        _ratio_from_env(_ENV_BLOCK, BLOCK_RATIO),
        _ratio_from_env(_ENV_WARN, WARN_RATIO),
    )


def _positive_number(val: Any) -> bool:
    """合法正数（bool 是 int 子类，显式排除——True 当锚价是配置事故）。"""
    return isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0


def check_price_sanity(final_price: float, discovery_meta: Optional[dict]) -> Tuple[str, Dict[str, Any]]:
    """校验定价终价与选品锚价的倍数关系。

    Returns:
        ("ok"|"warn"|"block", evidence)。
        - 无锚 / final_price 非法 ≤0 → ("ok", {})；
        - 有锚 → evidence = {anchor_price, anchor_source, final_price, ratio,
          block_ratio, warn_ratio}（ok 档也带，供审计留痕）。
        - ratio >= block_ratio → "block"；ratio >= warn_ratio → "warn"；否则 "ok"。
    """
    if not _positive_number(final_price):
        # final_price<=0 是定价层自己的 bug，本守卫不越界（见模块注释）
        return "ok", {}
    meta = discovery_meta if isinstance(discovery_meta, dict) else {}

    anchor: Optional[float] = None
    anchor_source = ""
    for key in ("ozon_price", "min_competing_price"):
        if _positive_number(meta.get(key)):
            anchor = float(meta[key])
            anchor_source = key
            break
    if anchor is None:
        return "ok", {}

    block_ratio, warn_ratio = get_ratios()
    ratio = anchor / float(final_price)
    evidence: Dict[str, Any] = {
        "anchor_price": anchor,
        "anchor_source": anchor_source,
        "final_price": float(final_price),
        "ratio": round(ratio, 2),
        "block_ratio": block_ratio,
        "warn_ratio": warn_ratio,
    }
    if ratio >= block_ratio:
        return "block", evidence
    if ratio >= warn_ratio:
        return "warn", evidence
    return "ok", evidence
