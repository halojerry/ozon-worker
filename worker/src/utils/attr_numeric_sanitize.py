"""数值属性清洗纯函数（v0.69 T1.1）。

三店健康扫描实证：数值属性错误 42 例（VALUE_MUST_BE_DECIMAL 11 /
VALUE_MUST_BE_INTEGER 10 / VALUE_MAX_LIMIT 6 / VALUE_MIN_LIMIT 3 /
error_attribute_values_empty 12），典型属性 8962 «Единиц в одном товаре»
VALUE_MAX_LIMIT。

根因：1688「件数/数量」类属性按名映射到数值 schema 属性（如 8962）后，
prepare 主转换循环对**非空值原样保留不校验**——可能带单位（"30包"）、
RU locale 逗号小数（"1,5"）或超限数值（20000）。

本模块是数值清洗的**唯一入口**（prepare 主循环 / 8962 兜底段 /
validation_retry_loop.repair_prepare_node 三处共用）——禁止各处内联正则
（同 compute_price / commission_resolver 共享层纪律）。

⚠️ type 字符串取证（2026-09-07）：
- v0.26 P1-2 生产修复（commit b8e6917f）实证 Ozon /v1/description-category/attribute
  返回的 type 为 "Integer"/"Decimal"（该修复据 VALUE_MUST_BE_INTEGER/DECIMAL
  真实错误落地且生效）；
- 本函数按**大小写不敏感**匹配 integer/decimal/number（含 int/float/double 变体），
  兼容历史代码只见过的两种枚举 + 文档提示的 Number 变体，防再出漏网通道。
"""
import re
from typing import Any, Dict, Optional, Tuple

# 数值型 schema type 名单（大小写不敏感比较）
_NUMERIC_TYPE_NAMES = frozenset({"integer", "int", "decimal", "number", "float", "double"})

# 已知数值属性合法区间（attr_id → (min, max)）。8962 «Единиц в одном товаре»
# 生产 VALUE_MAX_LIMIT 实证超限被拒；Ozon 类目默认上限 10000，下限 1。
# 新增属性按 Ozon 报错实测补表即可。
NUMERIC_ATTR_BOUNDS: Dict[int, Tuple[int, int]] = {
    8962: (1, 10000),
}

# 提取数字（允许负号与小数点；调用前先把 RU 逗号小数归一为点）
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def is_numeric_attr_type(attr_type: Any) -> bool:
    """schema type 是否数值型（大小写不敏感）。非字符串/空 → False。"""
    if not isinstance(attr_type, str):
        return False
    return attr_type.strip().lower() in _NUMERIC_TYPE_NAMES


def sanitize_numeric_attr_value(
    attr_id: Any, raw_value: Any, attr_type: Any
) -> Tuple[Optional[str], Optional[str]]:
    """清洗数值型属性值。返回 (清洗后值, 调整说明)。

    - 非数值型 attr_type → 原样透传 (str(raw).strip(), None)（调用方不必预判类型）。
    - 空值 → (None, reason)（调用方剔除/兜底）。
    - 剥货币/单位/空格等非数值字符（保留数字、小数点、负号）；
      RU 逗号小数 "1,5" → "1.5"；Integer 取整、Decimal/Number 浮点
      （整数值不带 .0 尾巴）；多个数字取首个（"10x20" → 10）。
    - 越界（NUMERIC_ATTR_BOUNDS 有该属性时）夹取到边界并在调整说明记录。
    - 剥完无数字 → (None, reason)（调用方剔除该属性或走兜底）。
    - 无调整时 reason=None。
    """
    if not is_numeric_attr_type(attr_type):
        # 非数值型属性不归本函数管——原样透传，绝不误伤文本属性
        return str(raw_value).strip() if raw_value is not None else None, None

    raw = str(raw_value).strip() if raw_value is not None else ""
    if not raw:
        return None, "数值属性值为空"

    # RU locale 逗号小数归一 → 提取首个数字（保留负号/小数点，剥其余一切）
    normalized = raw.replace(",", ".")
    match = _NUM_RE.search(normalized)
    if not match:
        return None, f"无法解析为数值: '{raw[:40]}'（已剔除，防 VALUE_MUST_BE_* 整单被拒）"

    num_str = match.group()
    try:
        num_f = float(num_str)
    except ValueError:
        return None, f"无法解析为数值: '{raw[:40]}'"

    is_integer_type = attr_type.strip().lower() in ("integer", "int")
    if is_integer_type:
        cleaned = str(int(num_f))
    elif num_f == int(num_f):
        # Decimal/Number 整数值不带 .0 尾巴（"2,0" → "2"）
        cleaned = str(int(num_f))
    else:
        cleaned = repr(num_f) if "e" not in repr(num_f) else str(num_f)

    # 越界夹取（仅白名单属性；说明里写明原值与区间）
    bounds = NUMERIC_ATTR_BOUNDS.get(int(attr_id) if str(attr_id).isdigit() else 0)
    if bounds:
        lo, hi = bounds
        if num_f < lo:
            return str(lo), f"越界下夹取 {cleaned}→{lo}（attr {attr_id} 合法区间 {lo}..{hi}）"
        if num_f > hi:
            return str(hi), f"越界上夹取 {cleaned}→{hi}（attr {attr_id} 合法区间 {lo}..{hi}）"

    if cleaned != raw:
        return cleaned, f"数值清洗: '{raw[:40]}' → '{cleaned}'（剥单位/格式归一）"
    return cleaned, None
