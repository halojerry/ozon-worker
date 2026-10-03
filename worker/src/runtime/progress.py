"""任务级运行时状态：进度存储 + 当前任务 ContextVar + 优雅关闭标志。

W3b（main.py composition root 拆解）自 main.py 顶层抽出——此前本族代码住在
main 模块，低层（utils/progress_logger、orchestrator/task_processor）只能函数内
``from main import ...`` 懒导入取用，形成下层 → God module 反向依赖。
本模块是唯一权威；main.py 保留同名 re-export（测试 monkeypatch main.X 的
兼容面 + 路由裸名调用不变）。

- 进度：内存 dict（重启清空）+ PG progress 列节流落盘（2s 合并窗口）；
  v0.80 起对同一 task 单调不降（详见 update_progress 注释）。
- STAGE_ORDER 是展示序（拓扑近似），与真实拓扑的局部错位由单调钳制兜底。
- _current_task_id ContextVar 按任务协程隔离（v0.29 串号事故修复，注释保留）。

⚠️ DB 放行形态（Mimosa 共处纪律，改前必读）：本模块两条 PG 语句走
「静态 SQL + %s 占位 + 参数元组经 exec_driver_sql」（AGENTS.md 规定的唯一
放行形态，范本 utils/category_doc_gate.py）——**勿改回** ``text(':bind')``
命名绑定：老 main.py 是历史豁免，新文件会被安全插件 SQL 规则拦截。
psycopg2 客户端插值语义与原 text() 等价（jsonb 列收字符串参量照常隐式 cast）。
"""

import asyncio
import contextvars
import json
import time
from typing import Any, Dict, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# ── 进度追踪（内存存储，重启清空） ──
# 格式: {task_id: {stage, stage_index, total_stages, percent, message, updated_at}}
_task_progress: Dict[str, Dict[str, Any]] = {}
# ✅ v0.29 P0(PRD-cicd-stability): 模块级全局 → contextvars
# 原实现是模块级 global, 注释谎称 "thread-local" —— asyncio 多任务并发时
# set/get 之间被其他协程 set 覆盖 → 日志/进度/Sentry 串号(PRD 复现路径)。
# ContextVar 按任务协程隔离, 子任务自动继承。
_current_task_id: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "current_task_id", default=None
)

# ✅ v0.29(PRD-cicd-stability): 优雅关闭标志
# 收到 SIGTERM/docker stop 后: worker_loop 不再拉新任务 → drain 运行中任务
# (最多 5 分钟) → 超时才 cancel。避免 update.sh --force-recreate 强杀用户任务。
SHUTDOWN_FLAG = False


def request_shutdown() -> None:
    """请求优雅关闭(停止接收新任务)。"""
    global SHUTDOWN_FLAG
    SHUTDOWN_FLAG = True


def is_shutting_down() -> bool:
    """是否正在优雅关闭。"""
    return SHUTDOWN_FLAG


def set_current_task_id(task_id: str | None):
    """设置当前协程正在处理的 task_id（供 ProgressLogger 等模块使用）"""
    _current_task_id.set(task_id)


def get_current_task_id() -> str | None:
    """获取当前协程正在处理的 task_id"""
    return _current_task_id.get()

# 节点执行顺序（用于计算进度百分比）。
# ⚠️ v0.80 重排为拓扑序（arch-findings #2）：check_quota 实际是 auth 后第二跳
# （graph.py route_after_auth → check_quota），旧序排第 10 位导致中段阶段百分比
# 虚高、后续节点回调时进度回跳。本表是**展示序**（13 项不变，仅排序），
# 与真实拓扑仍存在局部错位（如 pricing 先于 assemble 执行但 category_match
# 展示在前）——由 update_progress 的单调不降钳制兜底。新测试
# tests/test_progress_map_v080.py 锁定集合等价 + 本顺序。
STAGE_ORDER = [
    "auth", "check_quota", "ingest", "category_match", "pricing",
    "attributes", "description", "image_generation", "prepare_ozon_upload",
    "ozon_validate", "ozon_upload", "ozon_status", "learning_record"
]

# ✅ v0.9: 合并为单一 update_progress（内存 + PG 持久化），避免重复定义

_SQL_GET_PROGRESS = (
    "SELECT progress FROM ozon_product_tasks WHERE id = %s"
)
_SQL_PERSIST_PROGRESS = (
    "UPDATE ozon_product_tasks SET progress = %s, updated_at = NOW() "
    "WHERE id = %s AND status = 'running'"
)


def get_progress(task_id: str) -> Optional[Dict[str, Any]]:
    """获取任务进度（内存优先 → PG 回退）"""
    if task_id in _task_progress:
        return _task_progress[task_id]
    # ✅ P1 修复：内存无数据时回退到 PG（重启后仍可读）
    try:
        from storage.database.db import get_engine
        with get_engine().connect() as conn:
            row = conn.exec_driver_sql(_SQL_GET_PROGRESS, (task_id,)).scalar()
        if row:
            return json.loads(row) if isinstance(row, str) else row
    except Exception:
        pass
    return None


async def _persist_progress(task_id: str, data: dict):
    """异步写入 PG progress 列"""
    try:
        from storage.database.db import get_engine
        with get_engine().begin() as conn:
            conn.exec_driver_sql(
                _SQL_PERSIST_PROGRESS,
                (json.dumps(data, ensure_ascii=False), task_id),
            )
    except Exception as e:
        logger.debug("progress persist failed for %s: %s", task_id, e)


# ⚠️ v0.14 E1: 进度写 PG 节流 — 每任务 2s 合并窗口（旧代码每节点异步写一次 PG）
_last_persist_ts: dict = {}
_PERSIST_THROTTLE = 2.0


def _purge_stale_progress():
    """清理 _task_progress 中已完成超过 1 小时的条目（防内存泄漏）"""
    now = time.time()
    stale = [tid for tid, data in list(_task_progress.items())
             if now - data.get("updated_at", 0) > 3600]
    for tid in stale:
        del _task_progress[tid]


def update_progress(task_id: str, stage: str, message: str = ""):
    """更新任务进度（内存 + 异步 PG）。

    ✅ v0.80 防倒退（arch-findings #2）：进度对同一 task 单调不降——
    - stage 不在 STAGE_ORDER（_NODE_STAGE_MAP 漏配 / 未知节点 / "error" 等
      旁路调用）→ 保留上一阶段与百分比，只刷新 message（旧行为 stage_idx=0，
      进度条从高位跳回 0%；首跳无历史时按 0 起步）。
    - 已知阶段但展示序低于当前进度（展示序与真实拓扑局部错位，如 pricing
      之后的 assemble→category_match）→ 钳到当前进度，stage 标签跟随钳后
      下标保持 stage/stage_index/stages_* 自洽，节点明细在 message 里。
    - auth（STAGE_ORDER 首位，图的唯一入口）是新一轮哨兵：重试重跑允许把
      基线重置回 0，否则上一轮残留的高位会把整轮重试钉在旧百分比上。
    终态归位（completed/failed/rejected）不走本函数，由 http_task_status
    覆盖 progress，不受影响。
    """
    if not task_id:
        return
    prev = _task_progress.get(task_id) or {}
    prev_idx = int(prev.get("stage_index") or 0)
    if stage == STAGE_ORDER[0]:
        stage_idx = 0
        cur_stage = stage
    elif stage in STAGE_ORDER:
        stage_idx = max(STAGE_ORDER.index(stage), prev_idx)
        cur_stage = STAGE_ORDER[stage_idx]
    else:
        stage_idx = prev_idx
        cur_stage = str(prev.get("stage") or stage)
    total = len(STAGE_ORDER)
    percent = int((stage_idx / total) * 100)
    data = {
        "stage": cur_stage,
        "stage_index": stage_idx,
        "total_stages": total,
        "percent": percent,
        "message": message,
        "updated_at": time.time(),
        "stages_completed": STAGE_ORDER[:stage_idx],
        "stages_remaining": STAGE_ORDER[stage_idx+1:],
    }
    _task_progress[task_id] = data
    # PRD M4: 进度事件落 task_progress_events(时间线/SSE 数据源,静默降级)
    try:
        from services.task_progress_service import emit
        emit(task_id, stage, "", "progress", message)
    except Exception:
        pass
    # ✅ P1 修复：异步持久化到 PG（重启后仍可恢复进度）
    # ⚠️ v0.14 E1: 节流 — 同一任务 2s 窗口内跳过 PG 写（内存进度始终最新，PG 低频落盘）
    try:
        now_ts = time.time()
        if now_ts - _last_persist_ts.get(task_id, 0) >= _PERSIST_THROTTLE:
            _last_persist_ts[task_id] = now_ts
            # v0.63.1: 先确认有运行中事件循环再创建协程——直接 asyncio.create_task
            # 在同步模式（pytest/CLI）会抛 RuntimeError，且协程对象已创建未 await，
            # GC 时产生 RuntimeWarning: coroutine never awaited。
            loop = asyncio.get_running_loop()
            loop.create_task(_persist_progress(task_id, data))
    except RuntimeError:
        pass  # 无 event loop 时跳过（同步模式）
