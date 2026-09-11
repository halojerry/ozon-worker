"""字典值缓存三桶策略（v0.72，字典缓存撑爆 40G 盘事故根治）。

事故与定案（2026-09-09，记忆 dict-cache-disk-explosion-incident /
ozon-dict-api-semantics）：`dictionary_value_cache` 按 (attr,dc,tp) 键把全局
字典按类目整份复制——品牌 85 字典 5.18MB × 每节点一份，17% 预热即 3.83GB、
全量外推 20GB+。真实 API 实测语义：
- `category_dependent=false`（原产国/保证类）→ 跨类目逐字节一致，可全局一份；
- `category_dependent=true`（类型 8229/颜色/HS 编码）→ 按类目隔离但小字典
  KB 级，保持 (attr,dc,tp)；
- 巨型字典（品牌 85，翻 10 页 20000 值仍 has_next）→ **不物化**，运行时走
  `/values/search`（utils/ozon_dict_values.py，已实证可用）。

三桶判定（本模块唯一入口）：
- global：cat_dep=False
- scoped：cat_dep=True（缺字段/未知 → 保守按 scoped，行为与旧版一致）
- ephemeral：取值数超 `DICT_MATERIALIZE_MAX_VALUES`（即首页 limit 就
  has_next）→ 不物化不落库；体积判定天然覆盖品牌类，无需按 attr_id 硬编码。

**全局桶 = 哨兵键 (dc=0, tp=0)，零 DDL**：现有表和唯一键不动，dc=0 不会与
真实类目冲突，ON CONFLICT 正常命中（并绕开 NULL type_id 使 ON CONFLICT
永不命中的既有雷）。读侧 scoped→global 回退由
`OzonCategoryQuery.get_dictionary_values` 内置。

防击穿接线登记（v0.75 BL-25 Phase 2）：routed_get 返回 None 即 miss，回源
Ozon fetch 必须经 :func:`run_exclusive` 包裹（同 key 并发只回源一次），或
直接迁移到 :func:`get_or_fetch` 一站式读穿（miss 判定出口已接线）。现回源
动作在调用方手里（assemble/retry 的 ``_cache_dict_values`` 链、
category_schema_service、warm 脚本）——迁移时只包「回源 fetch」，
**绝不串行化 PG set/get 读路径**。
"""
from __future__ import annotations

import logging
import random
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

# 全局桶哨兵键（写入 dictionary_value_cache 的 dc/tp 位）
GLOBAL_DC = 0
GLOBAL_TP = 0

# Ozon /values 契约上限（实测 limit=5000 被静默钳到 2000——违约调用）
DICT_PAGE_LIMIT = 2000
# 物化上限：首页即 has_next → ephemeral（不物化）。≈ 一页 2000 值。
DICT_MATERIALIZE_MAX_VALUES = DICT_PAGE_LIMIT

# 统一 TTL：30 天（warm/init_data/local_db_manager 三处一致口径）
DICT_CACHE_TTL_SECONDS = 30 * 86400

# ── BL-25/S9-04 缓存三防 一期（docs/audit/2026-09-11-repo-gov/design-b2b-perf-hardening.md §2.1）──
# 防雪崩：写侧 TTL ±10% 均匀抖动——同次 warm 的行按写入时刻定 expires_at，
# 不抖则同日集中过期（全量重预热后第 30 天同时雪崩回源）。routed_set 是
# 全部写入点（assemble/retry/schema_service 三处调用方）的唯一入口，helper
# 在此统一应用，调用方零改动。
JITTER_RATIO = 0.10
# 防穿透：负缓存 TTL（秒）——回源 Ozon 确认「该键无值」后短窗内不再回源。
# 防击穿：单飞锁（singleflight）已落地（v0.75 Task C1，见下方 _SF_* 块与
# get_or_fetch 读穿接线点）。
NEGATIVE_TTL_SECONDS = 60

BUCKET_GLOBAL = "global"
BUCKET_SCOPED = "scoped"
BUCKET_EPHEMERAL = "skipped_ephemeral"


def _jitter_ttl(ttl: int) -> int:
    """TTL ±10% 均匀抖动（防雪崩，纯函数可单测）。

    返回 int(ttl×uniform(0.9,1.1))；下限保护 1s；非正 TTL 原样返回
    （调用方自定义瞬时语义，不抖）。
    """
    if ttl <= 0:
        return ttl
    lo = max(1, int(ttl * (1 - JITTER_RATIO)))
    hi = max(lo, int(ttl * (1 + JITTER_RATIO)))
    return random.randint(lo, hi)


# 进程内负缓存 {key: exp_epoch}（workers=1 是现部署契约，进程内即够——
# 设计 §2.1A；跨副本正确性待多副本批次再议）。key=(attr_id, dc, tp, language)。
_NEG_CACHE: dict = {}
_NEG_LOCK = threading.Lock()


def _neg_key(attr_id: int, dc: int, tp: int, language: str) -> tuple:
    return (attr_id, dc, tp, language)


def _negative_hit(key: tuple) -> bool:
    """进程内负缓存命中检查（惰性过期清除）。"""
    now = time.time()
    with _NEG_LOCK:
        exp = _NEG_CACHE.get(key)
        if exp is None:
            return False
        if now >= exp:
            _NEG_CACHE.pop(key, None)
            return False
        return True


def record_negative(attr_id: int, dc: int, tp: int,
                    language: str = "ZH_HANS",
                    *, ttl: int = NEGATIVE_TTL_SECONDS) -> None:
    """记录负缓存：调用方回源 Ozon 确认该键无值后调用（60s 内不再回源）。

    双层写入：
    - 进程内 {key: exp}（routed_get 短路，省一次 PG 往返）；
    - PG sentinel 行（values_data=[] 短 TTL，routed_set 同款 upsert——跨重启
      仍有效；读侧 get_dictionary_value_cache 的 expires_at 条件保证自动过期）。
    写失败仅 warning（与 routed_set 非致命语义一致）。
    """
    key = _neg_key(attr_id, dc, tp, language)
    with _NEG_LOCK:
        _NEG_CACHE[key] = time.time() + ttl
    try:
        from utils.local_db_manager import LocalDBManager
        LocalDBManager().set_dictionary_value_cache(
            attr_id, dc, tp, [], language=language, expires_in=ttl)
    except Exception as exc:
        logger.warning("负缓存落库失败(%s, 非致命): %s", key, exc)


# ── BL-25/S9-04 缓存三防 二期：防击穿 singleflight（v0.75 Task C1，
#    docs/audit/2026-09-11-repo-gov/design-b2b-perf-hardening.md §2.1）──
# 同 key 并发 miss 只放一个线程回源 Ozon，其余阻塞等待后共享结果（leader
# 执行 / follower 共享）。workers=1 是现部署契约 → 进程内锁即够；跨副本
# 待多副本批次再议（与负缓存同口径）。⚠️ 只用于包「缓存 miss 后回源
# fetch」，绝不串行化 PG set/get 读路径（共享读，锁了就是自造瓶颈）。
_SF_LOCK = threading.Lock()
_SF_INFLIGHT: set = set()
_SF_COND = threading.Condition(_SF_LOCK)
_SF_STATS = {"inflight_hits": 0, "acquired": 0}
# 结果槽：leader 把 fn 的（结果, 异常）发布于此，同飞行 follower 取用不重跑。
# 带上限防长驻内存（溢出整体清空，缺槽的 follower 退化为自执行 fn——少一次
# 去重，正确性不受影响）。
_SF_SLOT: dict = {}
_SF_SLOT_CAP = 1024
_SF_WAIT_TIMEOUT = 30.0


class _SingleflightLease:
    """acquire_singleflight 的租约句柄：waited=True 表示进锁前经历过等待
    （与自己并发存在先行者），run_exclusive 据此判 leader/follower。"""

    __slots__ = ("waited",)

    def __init__(self) -> None:
        self.waited = False


def singleflight_stats() -> dict:
    """单飞锁计数器（探针面）：{"inflight_hits": int, "acquired": int}。

    acquired = 成功进入临界区次数；inflight_hits = 发现 key 已被占而等待的
    次数。返回副本，外部改写不影响内部计数。
    """
    with _SF_COND:
        return dict(_SF_STATS)


@contextmanager
def acquire_singleflight(key: tuple):
    """per-key 互斥上下文（yield :class:`_SingleflightLease`）。

    同 key 并发只有一个线程持锁，其余阻塞等待后进入（lease.waited=True 标记
    等过）。等待用 Condition.wait(_SF_WAIT_TIMEOUT) 循环重查（超时后重查仍占
    则继续等，不 raise——宁可慢不可死锁）；退出（含异常路径）discard +
    notify_all 唤醒同 key 等待者。不同 key 互不影响（并行度不退化）。
    """
    lease = _SingleflightLease()
    with _SF_COND:
        while key in _SF_INFLIGHT:
            lease.waited = True
            _SF_STATS["inflight_hits"] += 1
            _SF_COND.wait(_SF_WAIT_TIMEOUT)
        _SF_INFLIGHT.add(key)
        _SF_STATS["acquired"] += 1
    try:
        yield lease
    finally:
        with _SF_COND:
            _SF_INFLIGHT.discard(key)
            _SF_COND.notify_all()


def run_exclusive(key: tuple, fn: Callable[[], Any]) -> Any:
    """同 key 并发去重执行：与本次并发的调用方共享首个结果，fn 只跑 1 次。

    leader（进锁无需等待 = 本飞行第一个到达）执行 fn 并把（结果, 异常）发布
    进结果槽；follower（进锁前等过 = 与 leader 并发）直接取槽中结果不重跑
    fn（Go singleflight 语义，follower 同收 leader 的异常）。飞行结束后才
    到达的新调用（没等过）正常开启新飞行，不吃陈旧槽。fn 抛异常时锁照常
    释放（acquire_singleflight finally 兜底），后续调用可重进。
    """
    with acquire_singleflight(key) as lease:
        if not lease.waited:
            try:
                result = fn()
            except BaseException as exc:
                with _SF_COND:
                    _SF_SLOT[key] = (None, exc)
                raise
            with _SF_COND:
                if len(_SF_SLOT) >= _SF_SLOT_CAP:
                    _SF_SLOT.clear()
                _SF_SLOT[key] = (result, None)
            return result
        with _SF_COND:
            entry = _SF_SLOT.get(key)
        if entry is not None:
            result, err = entry
            if err is not None:
                raise err
            return result
        # 槽缺失（槽被上限清空时可能）→ 退化为自执行 fn
        return fn()


def is_category_dependent(attr_row: Any) -> bool:
    """schema 行的按类目隔离开关。字段缺失/无法判定 → True（保守按 scoped，
    与旧版行为一致——老缓存行与旧测试夹具均无该字段）。"""
    if not isinstance(attr_row, dict):
        return True
    return bool(attr_row.get("category_dependent", True))


def classify_bucket(attr_row: Any, truncated: bool = False,
                    value_count: int = 0) -> str:
    """三桶分类（纯函数）。truncated=首页即 has_next（dicts >2000 值）。"""
    if truncated or value_count > DICT_MATERIALIZE_MAX_VALUES:
        return BUCKET_EPHEMERAL
    return BUCKET_SCOPED if is_category_dependent(attr_row) else BUCKET_GLOBAL


def routed_get(attr_id: int, dc: int, tp: int,
               language: str = "ZH_HANS") -> Optional[List[dict]]:
    """读缓存：scoped (attr,dc,tp) 优先，未命中回退 global (attr,0,0)。

    返回值三态（BL-25/S9-04 防穿透一期）：
    - ``[...]`` 非空列表 = 正数据命中；
    - ``[]`` 空列表 = **负缓存确认空**（进程内或 PG sentinel 行未过期）——
      调用方按空结果处理，**不要回源 Ozon**；
    - ``None`` = 缓存无信息，调用方自行回源；回源确认空后请调
      :func:`record_negative` 防重复穿透。

    ⚠️ 不再透传 ``OzonCategoryQuery.get_dictionary_values``：该函数把
    values_data=[] 视为 falsy 吞掉（无法表达负缓存行），故本函数直接读
    LocalDBManager（行为对齐：scoped 未命中/空行 → global 哨兵回退；global
    正数据优先于 scoped 负标记）。异常吞掉返回 None（调用方自行回源 API）。

    防击穿接线（v0.75 BL-25 Phase 2）：本函数返回 None 即 miss，回源 Ozon
    fetch 必须经 :func:`run_exclusive` 包裹（同 key 并发只回源一次），或直接
    迁移到 :func:`get_or_fetch` 一站式读穿。迁移时只包「回源 fetch」，
    **绝不串行化 PG set/get 读路径**。
    """
    key = _neg_key(attr_id, dc, tp, language)
    if _negative_hit(key):
        return []
    try:
        from utils.local_db_manager import LocalDBManager
        ldb = LocalDBManager()
        row = ldb.get_dictionary_value_cache(attr_id, dc, tp, language)
        values = (row or {}).get("values_data")
        if values:
            return values
        # scoped 未命中或空 sentinel 行 → global 桶回退（对齐 get_dictionary_values 现行为：
        # global 正数据优先于 scoped 负标记）
        if (dc, tp) != (GLOBAL_DC, GLOBAL_TP):
            grow = ldb.get_dictionary_value_cache(attr_id, GLOBAL_DC, GLOBAL_TP, language)
            gvalues = (grow or {}).get("values_data")
            if gvalues:
                return gvalues
        if row is not None and values == []:
            # 负 sentinel 行（PG 层 expires_at > now 条件保证「未过期」）→ 确认空。
            # 回填进程内负缓存，后续查询免一次 PG 往返。
            with _NEG_LOCK:
                _NEG_CACHE.setdefault(key, time.time() + NEGATIVE_TTL_SECONDS)
            return []
        return None
    except Exception:
        return None


def routed_set(attr_id: int, values: List[dict], dc: int, tp: int, *,
               attr_row: Any = None, category_dependent: Optional[bool] = None,
               truncated: bool = False, language: str = "ZH_HANS",
               expires_in: int = DICT_CACHE_TTL_SECONDS) -> str:
    """按桶写缓存，返回落桶结果（global/scoped/skipped_ephemeral）。

    - truncated=True（首页即 has_next，或调用方已知超上限）→ ephemeral，
      **不写库**——调用方继续用内存中的值，运行时 value→id 走 /values/search。
    - 桶由 category_dependent 决定（显式传参优先，否则从 attr_row 读，
      缺省保守按 scoped）。
    - BL-25 防雪崩：expires_in 统一过 :func:`_jitter_ttl` ±10% 抖动后才落库
      （三处写入点 assemble/retry/schema_service 全走本函数，自动生效）。
    写失败仅 warning 不抛（与各调用方的非致命语义一致）。
    """
    if truncated or (values and len(values) > DICT_MATERIALIZE_MAX_VALUES):
        logger.info("⏭️ 字典 %s 值数超物化上限（%s），ephemeral 不落库",
                    attr_id, len(values or []))
        return BUCKET_EPHEMERAL
    if category_dependent is None:
        category_dependent = is_category_dependent(attr_row)
    if category_dependent:
        bucket_dc, bucket_tp, bucket = dc, tp, BUCKET_SCOPED
    else:
        bucket_dc, bucket_tp, bucket = GLOBAL_DC, GLOBAL_TP, BUCKET_GLOBAL
    _jittered = _jitter_ttl(expires_in)
    try:
        from utils.local_db_manager import LocalDBManager
        LocalDBManager().set_dictionary_value_cache(
            attr_id, bucket_dc, bucket_tp, values,
            language=language, expires_in=_jittered)
        if bucket == BUCKET_GLOBAL:
            logger.info("✅ 字典 %s 落全局桶 (attr,0,0,%s)，%s 值", attr_id, language, len(values))
    except Exception as exc:
        logger.warning("字典 %s 落桶失败(%s, 非致命): %s", attr_id, bucket, exc)
        return BUCKET_EPHEMERAL
    return bucket


def get_or_fetch(attr_id: int, dc: int, tp: int,
                 language: str = "ZH_HANS",
                 fetch_fn: Optional[Callable[[], List[dict]]] = None,
                 *, attr_row: Any = None, truncated: bool = False,
                 expires_in: int = DICT_CACHE_TTL_SECONDS) -> List[dict]:
    """读穿一站式入口（v0.75 防击穿接线点）：缓存命中直接回，miss 才回源。

    流程：进程内负缓存短路 → :func:`routed_get` → miss（None）时
    :func:`run_exclusive`（同 key 并发只一个线程真正回源）→ 锁内双检
    （等待期间 leader 可能已落库）→ ``fetch_fn()`` → 非空走 :func:`routed_set`
    落桶（三桶+TTL 抖动自动生效）/ 空 :func:`record_negative` 负缓存。

    调用方迁移指引：现回源动作在 assemble/retry（``_cache_dict_values`` 链）、
    category_schema_service、warm 脚本各自手里——把「miss 后 fetch+落库」
    的代码块换成本函数（或至少用 run_exclusive 包住 fetch），**绝不把
    PG set/get 读路径包进锁**。fetch_fn 异常向本飞行全部等待者透传。
    """
    if fetch_fn is None:
        raise TypeError("fetch_fn is required")
    key = _neg_key(attr_id, dc, tp, language)
    if _negative_hit(key):
        return []
    hit = routed_get(attr_id, dc, tp, language)
    if hit is not None:
        return hit

    def _miss_fetch() -> List[dict]:
        # 双检：等锁期间 leader 可能已回源落库 → 直接吃缓存，不再回源
        again = routed_get(attr_id, dc, tp, language)
        if again is not None:
            return again
        values = fetch_fn() or []
        if values:
            routed_set(attr_id, values, dc, tp, attr_row=attr_row,
                       truncated=truncated, language=language,
                       expires_in=expires_in)
            return values
        record_negative(attr_id, dc, tp, language)
        return []

    return run_exclusive(key, _miss_fetch)
