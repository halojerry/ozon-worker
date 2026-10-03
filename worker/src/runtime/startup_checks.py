"""启动校验与恢复：关键配置在位校验 / 多 worker 探测告警 / 僵尸任务恢复（W3c 自 main.py 迁出）。

此前本族代码住在 main 模块顶层（`lifespan` 调用）。本模块是唯一权威；
main.py 不再保留同名 re-export（消费方仅 runtime.lifespan——存量测试
patch 靶点已随迁，见 tests/test_pipeline_latency_v0773.py /
tests/test_repo_gov_b4.py / tests/test_revive_default_v076.py）。

- `_recover_zombie_tasks` 是 lifespan 中僵尸恢复块的函数化搬家，调用点
  与原 main.lifespan 内位置逐字对应（graph 构建后、周期任务启动前）。
  语义：SKIP_ZOMBIE_RECOVERY=1 跳过全部；T19 起 failed 默认不复活，
  仅 SKIP_FAILED_REVIVE=0 显式恢复旧行为（见 `_revive_failed_enabled`）。
- 校验只报不拦（repair = 修 bind/重新部署，热路径不受影响）。
"""

import os

from storage.database.db import get_session
from utils.logger import get_logger

logger = get_logger(__name__)

# ✅ v0.77.3：启动关键配置校验清单——缺失即大声报（ERROR+Sentry）。
# 必需 = 节点硬依赖（缺了任务确定性失败）；可选 = 有内置降级（仅 WARNING）。
_CRITICAL_CONFIG_FILES = (
    "scene_generation_llm_cfg.json",
    "visual_vars_llm_cfg.json",
    "category_match_v2_cfg.json",
    "attributes_llm_cfg.json",
    "translate_russian_cfg.json",
    "error_repair_llm_cfg.json",
    "imagegen.json",
    "image_prompts.json",
)
_OPTIONAL_CONFIG_FILES = (
    "restricted_keywords.json",   # 缺失回退内置默认词表（受限闸降级）
    "attr_synonyms.json",         # 同义词组缺失=匹配质量降级，非失败
    "category_synonyms.json",
)


def _assert_critical_configs(base_dir: str | None = None) -> None:
    """启动即校验 /app/config 关键文件在位（bind mount 挂空秒级暴露）。

    实测事故（2026-09-19 gate 取证）：compose 栈从已删除 worktree 启动 →
    config bind 源路径不存在 → Docker 静默创建空目录 → 镜像内配置被空目录
    遮蔽 → 任务在 scene 节点 FileNotFoundError 被重试 4 轮白烧 40s+。
    校验只报不拦（repair = 修 bind/重新部署，热路径不受影响）。
    base_dir 参数供测试注入（缺省 /app/config）。
    """
    base = base_dir or os.path.join(os.sep, "app", "config")
    if not os.path.isdir(base):
        logger.warning("⚠️ 配置目录 %s 不存在（源码态运行可忽略）", base)
        return
    missing = [f for f in _CRITICAL_CONFIG_FILES if not os.path.isfile(os.path.join(base, f))]
    if missing:
        logger.error(
            "🚨 关键配置文件缺失 %d 个（bind mount 挂空/镜像残缺？任务将确定性失败）: %s",
            len(missing), ", ".join(missing),
        )
        try:
            from utils.sentry_setup import capture_task_event
            capture_task_event(
                "critical_configs_missing",
                f"关键配置缺失: {', '.join(missing)}",
                level="error",
            )
        except Exception:
            pass
    for f in _OPTIONAL_CONFIG_FILES:
        if not os.path.isfile(os.path.join(base, f)):
            logger.warning("⚠️ 可选配置缺失（走内置降级）: %s", f)


def _warn_if_multi_worker() -> None:
    """B4 BL-31（2026-09-11 仓库治理）：多 worker 部署探测告警（只告警不改行为）。

    本进程存在多处**内存态组件**：MXOU 余额缓存（_check_balance_cached，token
    指纹绑定）、任务进度缓存（内存优先 + PG 2s 节流回写）、限流器计数、远程 MCP
    session manager（FastMCP）——均进程局部，无任何开关能使其多副本安全，当前
    部署假设 uvicorn workers=1（start_http_server 硬编码；横向并发靠
    MAX_CONCURRENT 单进程内消化）。uvicorn/gunicorn 均会读 WEB_CONCURRENCY
    （gunicorn 亦读 WORKERS）——env >1 即告警，提醒运维改回单 worker 部署。
    """
    for _var in ("WEB_CONCURRENCY", "WORKERS"):
        _raw = os.getenv(_var, "").strip()
        if _raw.isdigit() and int(_raw) > 1:
            logger.warning(
                "⚠️ 检测到 %s=%s：内存态组件（余额缓存/租户缓存/MCP session/限流计数）"
                "非多副本安全，当前部署假设 workers=1——多 worker 会出现余额误判/进度"
                "丢失/MCP 会话错乱，请改用单 worker + MAX_CONCURRENT 扩并发",
                _var, _raw,
            )


def _revive_failed_enabled() -> bool:
    """T19(race-M4): 部署重启默认不复活 failed 任务（retry_count 归零会烧用户额度重复上架）。
    应急恢复旧行为：SKIP_FAILED_REVIVE=0。"""
    return os.environ.get("SKIP_FAILED_REVIVE", "").strip() == "0"


def _recover_zombie_tasks() -> None:
    """启动时僵尸任务恢复：重置重启前的 running 任务和可重试的 failed 任务。

    ⚠️ v0.30.0:
      SKIP_ZOMBIE_RECOVERY=1 — 跳过全部恢复（本地/测试环境必开，防止旧 failed 任务复活真实上架）
    ⚠️ T19(race-M4) 语义翻转：failed 复活默认**关闭**——部署重启不再把 retry_count<max 的
      failed 重置回 pending（retry_count 归零=对用户无人同意的重新上架，烧生图/上传额度）。
      应急恢复旧行为：SKIP_FAILED_REVIVE=0 — failed→pending 复活（running 恢复不受影响）。
    """
    _skip_all = os.getenv("SKIP_ZOMBIE_RECOVERY", "0") == "1"
    _skip_failed = not _revive_failed_enabled()
    if _skip_all:
        logger.info("🧹 跳过全部僵尸任务恢复（SKIP_ZOMBIE_RECOVERY=1）")
        return
    try:
        from sqlalchemy import text
        sess = get_session()
        try:
            # ⚠️ v0.26 FIX: 僵尸重置同样有界化——running 且 retry_count < max_retries → pending+递增；
            # retry_count >= max_retries → failed（避免重启后无限重跑烧生图额度）
            # 1. 重置 running 任务（Worker 重启导致中断）— 永远保留（任务卡 running 会永久阻塞）
            zombie_running = sess.execute(
            text("UPDATE ozon_product_tasks SET status='pending', retry_count=retry_count+1, "
                 "error_message='[ZOMBIE_RESET] 重启前 running 任务重置重试(有界)', "
                 "started_at=NULL, updated_at=NOW() "
                 "WHERE status='running' AND retry_count < max_retries")
            ).rowcount
            zombie_running_failed = sess.execute(
            text("UPDATE ozon_product_tasks SET status='failed', "
                 "error_message='[ZOMBIE_RESET] 重试次数耗尽，终止（不再重跑）', "
                 "completed_at=NOW(), updated_at=NOW() "
                 "WHERE status='running' AND retry_count >= max_retries")
            ).rowcount
            # 2. 重置可重试的 failed 任务（T19 默认跳过——防部署重启复活旧任务重复上架；
            #    仅 SKIP_FAILED_REVIVE=0 显式恢复旧行为）
            zombie_failed = 0
            if not _skip_failed:
                zombie_failed = sess.execute(
                text("UPDATE ozon_product_tasks SET status='pending', retry_count=0, error_message=NULL, updated_at=NOW() WHERE status='failed' AND retry_count < max_retries")
                ).rowcount
            sess.commit()
            if zombie_running or zombie_running_failed or zombie_failed:
                logger.info(f"🧹 启动清理: {zombie_running} 个僵尸 running → pending, {zombie_running_failed} 个 running → failed(耗尽), {zombie_failed} 个 failed → pending{'（failed 默认不复活；SKIP_FAILED_REVIVE=0 恢复旧行为）' if _skip_failed else ''}")
                # v0.29.2 监控: 启动时任务重跑/恢复上报 Sentry(带数量)
                try:
                    from utils.sentry_setup import capture_task_event
                    capture_task_event(
                        "zombie_reset",
                        f"启动僵尸恢复: running→pending {zombie_running}, "
                        f"running→failed(耗尽) {zombie_running_failed}, failed→pending {zombie_failed}",
                        level="warning",
                        zombie_running=zombie_running,
                        zombie_running_failed=zombie_running_failed,
                        zombie_failed=zombie_failed,
                    )
                except Exception:
                    pass
        finally:
            sess.close()
    except Exception as _cleanup_e:
        logger.warning(f"⚠️ 启动清理失败（非致命）: {_cleanup_e}")
