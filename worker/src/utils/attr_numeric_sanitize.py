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

⚠️ v0.75 C4 拒单学习闭环（audit A4 F-P1-1）：Ozon 平台不下发数值 bounds，
NUMERIC_ATTR_BOUNDS 白名单只能靠人工逐条补。本模块新增 decline 原文回流学习：
- parse_numeric_bounds_from_decline：俄语拒单文案 → (attr_id, lo|None, hi|None)，
  置信门槛——单条消息须同时定位 attr_id 与至少一界，否则宁可不学；
- learn_bounds_from_decline：parse → PG attr_bounds_learned upsert（收紧并集，
  min 取 max / max 取 min——只收紧不放松）；DB 异常静默 0，绝不 raise 进重试主链；
- get_learned_bounds：读学习表（进程内 60s TTL 缓存，容量 1000 超容清空；
  表不存在/PG 不可用 → None）。
读侧合成 _resolve_effective_bounds：静态 NUMERIC_ATTR_BOUNDS（人工）恒赢 >
学习表 > None（现状不夹取）。表见 storage.database.shared.model.AttrBoundLearned。
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

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

# ── v0.75 C4: decline 原文解析正则（俄语，大小写不敏感；«» 引号容错）──
# attr_id 两种形态：«характеристика/характеристики/характеристик <id>»（主形态）
# 与 «id[: ]<id>»（兜底形态；\b 防单词内部 id 误配如 valid: 42）
_DECLINE_ATTR_RE = re.compile(r"характеристик[аиы]?\s*«?(\d+)»?", re.IGNORECASE)
_DECLINE_ATTR_ALT_RE = re.compile(r"\bid\s*[: ]\s*«?(\d+)»?", re.IGNORECASE)
# 上界形态：не должно / не должна / не должны превышать <N>
_DECLINE_MAX_RE = re.compile(r"не\s+должн[оаы]?\s+превышать\s*«?(\d+)»?", re.IGNORECASE)
# 下界形态：не менее / не меньше / минимум <N>
_DECLINE_MIN_RE = re.compile(r"(?:не\s+менее|не\s+меньше|минимум)\s*«?(\d+)»?", re.IGNORECASE)

# 学习表读缓存：attr_id → (过期 epoch, bounds 或 None)；TTL 60s，容量 1000 超容清空
_BOUNDS_TTL_SECONDS = 60
_BOUNDS_CACHE_CAP = 1000
_BOUNDS_CACHE: Dict[int, Tuple[float, Optional[Tuple[Optional[float], Optional[float]]]]] = {}


def parse_numeric_bounds_from_decline(
    text: str, fallback_attr_id: Any = None,
) -> List[Tuple[int, Optional[float], Optional[float]]]:
    """从 VALUE_MAX/MIN_LIMIT 类 decline 俄语原文解析数值 bounds。

    按「句」（.; 换行分隔）逐段解析，段内须**同时**定位到唯一 attr_id 与至少
    一个界才产出 (attr_id, lo|None, hi|None)；定位不 confident 的段整段跳过
    （宁可不学，绝不把别属性的界张冠李戴）。多条 confident 产出全部返回；
    同 attr 重复产出去重保序。无可解析内容 → []。

    fallback_attr_id（终审 review Important#2）：Ozon /v1/product/import/info 的
    errors[] 携带**结构化** attribute_id(int64)——原文若只写属性名不内嵌
    «характеристика <id>» 形态（纯文本正则抽不到 id 时），用同一 error dict 的
    结构化 id 补全（同条 error 的 attr 与界归属天然正确，宁可不学门槛不降）。

    «символ»（字符数限制）形态排除：长度类报错（如 «не должно превышать 100
    символов»）不是数值界——段内出现 символ/символов 时该段的界一律不学。
    """
    results: List[Tuple[int, Optional[float], Optional[float]]] = []
    seen: set = set()
    try:
        fb_aid: Optional[int] = int(fallback_attr_id) if fallback_attr_id is not None else None
    except (TypeError, ValueError):
        fb_aid = None
    if not text or not isinstance(text, str):
        return results
    for segment in re.split(r"[.;\n]", text):
        if re.search(r"символ", segment, re.IGNORECASE):
            continue  # 字符数限制 ≠ 数值界（长度类报误学会污染夹取）
        attr_ids = {
            int(m.group(1)) for m in _DECLINE_ATTR_RE.finditer(segment)
        } | {
            int(m.group(1)) for m in _DECLINE_ATTR_ALT_RE.finditer(segment)
        }
        if len(attr_ids) > 1:
            continue  # ≥2 个 = 界值无法归属 → 都不学
        max_m = _DECLINE_MAX_RE.search(segment)
        min_m = _DECLINE_MIN_RE.search(segment)
        if not max_m and not min_m:
            continue  # 有 attr 无界 → 不学
        if attr_ids:
            attr_id = attr_ids.pop()
        elif fb_aid is not None:
            attr_id = fb_aid  # 文本无 id，用结构化 attribute_id（终审 Important#2）
        else:
            continue  # 无主 → 不学
        lo = float(min_m.group(1)) if min_m else None
        hi = float(max_m.group(1)) if max_m else None
        key = (attr_id, lo, hi)
        if key not in seen:
            seen.add(key)
            results.append(key)
    return results


def get_learned_bounds(attr_id: Any) -> Optional[Tuple[Optional[float], Optional[float]]]:
    """读拒单学习表 attr_bounds_learned 的已知 bounds。

    进程内 60s TTL 缓存（容量 1000，超容整体清空）；表不存在（未迁移库）/
    PG 不可用 → None（视同未学习，绝不抛）。无行或两界全空 → None；
    单边缺失以 None 占位（调用方只夹已知一侧）。
    """
    try:
        aid = int(attr_id)
    except (TypeError, ValueError):
        return None
    now = time.time()
    hit = _BOUNDS_CACHE.get(aid)
    if hit is not None and hit[0] > now:
        return hit[1]
    value: Optional[Tuple[Optional[float], Optional[float]]] = None
    try:
        from storage.database.db import get_engine

        with get_engine().connect() as conn:
            row = conn.execute(
                sql_text("SELECT min_value, max_value FROM attr_bounds_learned WHERE attr_id = :aid"),
                {"aid": aid},
            ).fetchone()
        if row is not None and (row[0] is not None or row[1] is not None):
            value = (row[0], row[1])
    except Exception:
        value = None
    if len(_BOUNDS_CACHE) >= _BOUNDS_CACHE_CAP:
        _BOUNDS_CACHE.clear()
    _BOUNDS_CACHE[aid] = (now + _BOUNDS_TTL_SECONDS, value)
    return value


def _tighten_bounds(
    old: Tuple[Optional[float], Optional[float]],
    new: Tuple[Optional[float], Optional[float]],
) -> Tuple[Optional[float], Optional[float]]:
    """区间收紧并集：min 取 max(min)，max 取 min(max)——学习只收紧不放松；
    任一侧缺失（None）由另一侧补位，双侧全缺 → (None, None)。"""
    los = [v for v in (old[0], new[0]) if v is not None]
    his = [v for v in (old[1], new[1]) if v is not None]
    return (max(los) if los else None, min(his) if his else None)


def learn_bounds_from_decline(text: str, fallback_attr_id: Any = None) -> int:
    """decline 原文 → parse → upsert 学习表，返回学习条数。

    fallback_attr_id 透传 parse（Ozon errors[] 结构化 attribute_id 兜底，
    见 parse_numeric_bounds_from_decline docstring）。
    每条先读旧行再 Python 侧收紧合并后写回（INSERT..ON CONFLICT DO UPDATE，
    与 _tighten_bounds 同一语义）。任何异常（含 parse/DB）静默返回 0——
    学习是 best-effort，绝不影响 validation_retry_loop 重试主链。
    """
    try:
        parsed = parse_numeric_bounds_from_decline(text, fallback_attr_id=fallback_attr_id)
        if not parsed:
            return 0
        sample = str(text or "")[:200]
        now = int(time.time())
        learned = 0
        from storage.database.db import get_engine

        with get_engine().connect() as conn:
            for attr_id, lo, hi in parsed:
                row = conn.execute(
                    sql_text("SELECT min_value, max_value FROM attr_bounds_learned WHERE attr_id = :aid"),
                    {"aid": attr_id},
                ).fetchone()
                m_lo, m_hi = _tighten_bounds(
                    (row[0], row[1]) if row else (None, None), (lo, hi)
                )
                conn.execute(
                    sql_text(
                        "INSERT INTO attr_bounds_learned "
                        "  (attr_id, min_value, max_value, source, sample, updated_at) "
                        "VALUES (:aid, :lo, :hi, 'decline_learned', :sample, :now) "
                        "ON CONFLICT (attr_id) DO UPDATE SET "
                        "  min_value = :lo, max_value = :hi, "
                        "  source = 'decline_learned', sample = :sample, updated_at = :now"
                    ),
                    {"aid": attr_id, "lo": m_lo, "hi": m_hi, "sample": sample, "now": now},
                )
                _BOUNDS_CACHE.pop(attr_id, None)  # 失效缓存，下次读立即见新界
                learned += 1
            conn.commit()
        return learned
    except Exception as exc:
        logger.warning("attr bounds 学习失败（静默，不影响重试主链）: %s", str(exc)[:200])
        return 0


def _resolve_effective_bounds(attr_id: Any) -> Optional[Tuple[Optional[float], Optional[float]]]:
    """读侧合成：静态 NUMERIC_ATTR_BOUNDS（人工维护）恒赢 > 拒单学习表 > None。"""
    static = NUMERIC_ATTR_BOUNDS.get(int(attr_id) if str(attr_id).isdigit() else 0)
    if static:
        return (static[0], static[1])
    return get_learned_bounds(attr_id)


def _fmt_num(v: Any) -> str:
    """界值/夹取目标格式化：整值浮点不带 .0 尾巴（5000.0 → "5000"，防
    Integer 属性夹取后产出 "5000.0" 再吃 VALUE_MUST_BE_INTEGER 拒单）。"""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def _fmt_bounds(lo: Optional[float], hi: Optional[float]) -> str:
    if lo is not None and hi is not None:
        return f"{_fmt_num(lo)}..{_fmt_num(hi)}"
    if lo is not None:
        return f">={_fmt_num(lo)}"
    return f"<={_fmt_num(hi)}"


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
    - 越界（静态白名单或拒单学习表有该属性 bounds 时）夹取到边界并在调整说明记录。
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

    # 越界夹取（v0.75 C4：静态白名单恒赢，未命中查拒单学习表，都无则不夹取；
    # 单边学习缺失只夹已知一侧；说明里写明原值与区间）
    bounds = _resolve_effective_bounds(attr_id)
    if bounds:
        lo, hi = bounds
        if lo is not None and num_f < lo:
            _lo = _fmt_num(lo)
            return _fmt_num(lo), f"越界下夹取 {cleaned}→{_lo}（attr {attr_id} 合法区间 {_fmt_bounds(lo, hi)}）"
        if hi is not None and num_f > hi:
            _hi = _fmt_num(hi)
            return _fmt_num(hi), f"越界上夹取 {cleaned}→{_hi}（attr {attr_id} 合法区间 {_fmt_bounds(lo, hi)}）"

    if cleaned != raw:
        return cleaned, f"数值清洗: '{raw[:40]}' → '{cleaned}'（剥单位/格式归一）"
    return cleaned, None
