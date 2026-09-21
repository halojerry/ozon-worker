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
- ✅ v0.78 批C（fix/guard-precision-v1）：跨币种从「直接 skip」增强为「汇率换算后
  真比」——``exchange_rate``（keyword-only，CNY→RUB 方向，与 pricing 语义一致）
  > 0 时 ``anchor_cmp = anchor / exchange_rate`` 折回店铺币种再走既有 ratio 判定
  （阈值不变），evidence 记 ``anchor_converted``/``exchange_rate`` 留痕；
  ``exchange_rate <= 0`` 维持 v0.77.3 skip 止血语义（宁缺勿假）。绝不 raise。
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


def check_price_sanity(
    final_price: float,
    discovery_meta: Optional[dict],
    final_currency: str = "RUB",
    *,
    exchange_rate: float = 0.0,
) -> Tuple[str, Dict[str, Any]]:
    """校验定价终价与选品锚价的倍数关系。

    final_currency = 终价货币码（pricing_node 的 currency_code；缺省 "RUB" =
    生产主形态，旧调用方行为零变化）。
    exchange_rate = CNY→RUB 汇率（keyword-only；与 pricing 的 _get_exchange_rate
    同方向同语义）。仅跨币种（final_currency 非 RUB）时参与换算：
    ``anchor_cmp = anchor / exchange_rate`` 折回店铺币种再比。

    Returns:
        ("ok"|"warn"|"block", evidence)。
        - 无锚 / final_price 非法 ≤0 → ("ok", {})；
        - 币种不可比且 exchange_rate <= 0 → ("ok", {skipped:
          "currency_mismatch", anchor_price, ...})——锚价恒 RUB（Ozon 站内价），
          非 RUB 终价与锚不同单位，且无汇率可换算时直接比倍数是跨币种假阳性
          （实测 gate：608₽÷30¥=20× 冤杀六卡，真实可比 608×0.075≈45.6¥ vs 30¥
          仅 1.52×）。宁缺勿假 = 不比。
        - 币种不可比但 exchange_rate > 0 → 换算真比：anchor_cmp =
          anchor / exchange_rate，ratio = anchor_cmp / final_price 走既有阈值
          判定（block ≥10 / warn ≥3 不变）；evidence 额外带 anchor_converted /
          exchange_rate（audit 留痕）。608₽÷13.3≈45.7¥ vs 30¥ = 1.52× → ok。
        - 有锚可比（RUB）→ evidence = {anchor_price, anchor_source, final_price,
          ratio, block_ratio, warn_ratio}（ok 档也带，供审计留痕）。
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

    # ✅ v0.77.3：币种可比性校验——非 RUB 终价不与 RUB 锚直接比倍数（见 docstring）
    # ✅ v0.78 批C：exchange_rate > 0 时换算真比（anchor 折回店铺币种再走既有阈值）；
    #    rate 缺失/≤0 维持 skip 止血语义（宁缺勿假）。
    if str(final_currency or "RUB").strip().upper() != "RUB":
        if not (isinstance(exchange_rate, (int, float))
                and not isinstance(exchange_rate, bool)
                and exchange_rate > 0):
            return "ok", {
                "skipped": "currency_mismatch",
                "anchor_price": anchor,
                "anchor_source": anchor_source,
                "final_price": float(final_price),
                "final_currency": str(final_currency).strip().upper(),
            }
        anchor_cmp = anchor / float(exchange_rate)
        block_ratio, warn_ratio = get_ratios()
        ratio = anchor_cmp / float(final_price)
        evidence: Dict[str, Any] = {
            "anchor_price": anchor,
            "anchor_source": anchor_source,
            "anchor_converted": round(anchor_cmp, 2),
            "exchange_rate": float(exchange_rate),
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
