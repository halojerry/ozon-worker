"""Sentry 错误监测（v0.23）— worker 侧统一初始化 + 任务异常捕获。

配置（deploy/.env）:
    SENTRY_DSN=...                 # 为空则完全禁用（no-op）
    SENTRY_ENV=production          # 环境标签（默认 production）
    SENTRY_TRACES_SAMPLE_RATE=0.1  # 性能采样率（默认 0.1）

用法:
    from utils.sentry_setup import init_sentry, capture_task_error
    init_sentry()                                   # main.py 模块加载时调用一次
    capture_task_error(e, task_id=..., tenant_id=...)  # task_processor 异常分支
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SENTRY_INITIALIZED = False
_SENTRY_ENABLED = False

# ============================================================
# v0.32: before_send — 语言检查噪音指纹聚合（T5）
# 62 个语言检查噪音 issue（中文/拉丁字符验证错误）走 sentry_sdk 默认
# LoggingIntegration（节点内 logger.error 自动上报，extra 无 task_id），
# 不经 capture_task_event → 只能在 before_send 按消息特征+logger 名拦截。
# 命中 → 聚合为单一 fingerprint + level 降 warning + 确定性 trace_id 注入。
# ============================================================

_LANG_NOISE_FINGERPRINT = "language-noise-validation"
_LANG_NOISE_KEYWORDS = ("中文字符", "拉丁字母", "含中文", "描述含")
_LANG_NOISE_LOGGER_SUFFIXES = (
    "ozon_validate_node",
    "prepare_ozon_upload_node",
    "validation_retry_loop",
    "assemble_ozon_product_node",
    "fetch_back_node",
)


def _event_message(event: dict) -> str:
    msg = event.get("message") or ""
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        msg = msg or logentry.get("formatted") or logentry.get("message") or ""
    return msg


def _is_lang_noise_event(event: dict) -> bool:
    logger_name = (event.get("logger") or "").lower()
    if not any(logger_name.endswith(s) for s in _LANG_NOISE_LOGGER_SUFFIXES):
        return False
    msg = _event_message(event)
    return any(kw in msg for kw in _LANG_NOISE_KEYWORDS)


def _lang_noise_trace_id(message: str) -> str:
    digest = hashlib.sha1(message.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"lang-noise-{digest}"


def _token_fingerprint(token: str) -> str:
    """mxou token 脱敏指纹——不泄露明文, 仅用于 Sentry 按用户区分。

    sk-开头取前 8 位字符 + sha1 前 6, 形如 'sk-abc12345-a1b2c3'。
    """
    if not token:
        return "no-token"
    raw = token[3:] if token.startswith("sk-") else token
    prefix = raw[:8]
    suffix = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:6]
    return f"{prefix}-{suffix}"


def _before_send(event: dict, hint: Optional[dict] = None) -> dict:
    """语言检查噪音指纹聚合。未启用短路零开销；噪音 → fingerprint/level/trace_id；其余原样返回。"""
    if not _SENTRY_ENABLED or not isinstance(event, dict):
        return event
    if not _is_lang_noise_event(event):
        return event
    msg = _event_message(event)
    event["fingerprint"] = [_LANG_NOISE_FINGERPRINT]
    event["level"] = "warning"
    extra = event.setdefault("extra", {})
    if not isinstance(extra, dict):
        extra = {}
        event["extra"] = extra
    extra["noise_group"] = "language_validation"
    extra["trace_id"] = _lang_noise_trace_id(msg)
    return event


def _is_test_process() -> bool:
    """测试进程（test_*.py / pytest / PYTEST_CURRENT_TEST）跳过上报，避免测试噪音污染生产监测。"""
    script = os.path.basename(sys.argv[0] or "") if sys.argv else ""
    return (
        script.startswith("test_")
        or script in ("pytest", "py.test")
        or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    )


def init_sentry(dsn: Optional[str] = None) -> bool:
    """初始化 Sentry SDK。DSN 为空或 SDK 未安装时安全 no-op。返回是否启用。"""
    global _SENTRY_INITIALIZED, _SENTRY_ENABLED
    if _SENTRY_INITIALIZED:
        return _SENTRY_ENABLED
    _SENTRY_INITIALIZED = True

    dsn = (dsn or os.environ.get("SENTRY_DSN", "") or "").strip()
    if not dsn:
        logger.info("SENTRY_DSN 未配置，Sentry 监测禁用")
        return False
    if _is_test_process():
        logger.info("测试进程，跳过 Sentry 上报（避免测试噪音）")
        return False
    try:
        import sentry_sdk  # type: ignore

        env = (os.environ.get("SENTRY_ENV", "") or "production").strip() or "production"
        release = (os.environ.get("APP_VERSION", "") or "").strip() or None
        try:
            traces_sample_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
        except ValueError:
            traces_sample_rate = 0.1
        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            release=release,
            traces_sample_rate=traces_sample_rate,
            before_send=_before_send,
        )
        _SENTRY_ENABLED = True
        logger.info("✅ Sentry 监测已启用 (env=%s, release=%s)", env, release or "dev")
    except Exception as e:
        logger.warning("Sentry 初始化失败（不影响服务运行）: %s", e)
        return False
    return True


def capture_task_event(
    event_type: str,
    message: str,
    *,
    task_id: str = "",
    tenant_id: str = "",
    level: str = "warning",
    **extras,
) -> None:
    """通用任务事件上报(v0.29.2 监控): 重跑/僵尸恢复/超时重置等。

    - event_type: 事件分类(如 task_rerun / zombie_reset / stale_running)
    - message: 人类可读说明
    - level: info/warning/error
    - extras: 额外字段(retry_count 等)进 event extras
    未启用时 no-op。同步 flush(最多 1s)确保事件送达。
    """
    if not _SENTRY_ENABLED:
        return
    try:
        import sentry_sdk  # type: ignore

        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("task_event", event_type)
            if task_id:
                scope.set_tag("task_id", task_id)
            if tenant_id:
                scope.set_tag("tenant_id", tenant_id)
            for k, v in extras.items():
                scope.set_extra(k, v)
        sentry_sdk.capture_message(message, level=level)
        sentry_sdk.flush(timeout=1)
    except Exception as e:
        logger.warning("Sentry 事件上报失败: %s", e)


def capture_task_error(
    exc: Optional[BaseException] = None,
    *,
    task_id: str = "",
    tenant_id: str = "",
    token: str = "",
    message: str = "",
) -> None:
    """任务级异常上报：带 task_id/tenant_id/token 指纹上下文。未启用时 no-op。"""
    if not _SENTRY_ENABLED:
        return
    try:
        import sentry_sdk  # type: ignore

        with sentry_sdk.configure_scope() as scope:
            if task_id:
                scope.set_tag("task_id", task_id)
                scope.set_extra("task_id", task_id)
            if tenant_id:
                scope.set_tag("tenant_id", tenant_id)
                scope.set_extra("tenant_id", tenant_id)
            if token:
                # v0.34: token 脱敏指纹 → Sentry user（能按用户/店铺筛选错误）
                _fp = _token_fingerprint(token)
                scope.set_user({"id": tenant_id or _fp, "username": _fp})
                scope.set_tag("mxou_token_fp", _fp)
        if exc is not None:
            sentry_sdk.capture_exception(exc)
        if message:
            sentry_sdk.capture_message(message)
        # 任务失败路径是异常场景，同步 flush 确保事件送达（最多阻塞 2s）
        sentry_sdk.flush(timeout=2)
    except Exception as e:
        logger.warning("Sentry 上报失败: %s", e)


# ============================================================
# v0.26: 全局监控 — 任务级 transaction + 节点/生图 span
# 目的：Sentry 不再只看到「Ozon 返回错误」，还能看到 worker 内部
# 每个任务/节点/生图调用的耗时与结果（卡在哪个节点、ozon_status 重跑几次、
# 生图调了几次，tracing 视图直接可见）。未启用时全部 no-op。
# ============================================================

def start_task_transaction(task_id: str = "", tenant_id: str = ""):
    """启动任务级 transaction（性能 trace）。未启用返回 None。"""
    if not _SENTRY_ENABLED:
        return None
    try:
        import sentry_sdk  # type: ignore

        tx = sentry_sdk.start_transaction(
            name=f"task_{task_id or 'unknown'}",
            op="task",
        )
        if tx is not None:
            if task_id:
                tx.set_tag("task_id", task_id)
            if tenant_id:
                tx.set_tag("tenant_id", tenant_id)
        return tx
    except Exception as e:
        logger.warning("Sentry 任务 transaction 启动失败: %s", e)
        return None


def start_node_span(transaction, node_name: str = ""):
    """在任务 transaction 下启动节点 span。未启用返回 None。"""
    if not _SENTRY_ENABLED or transaction is None:
        return None
    try:
        import sentry_sdk  # type: ignore

        return transaction.start_child(
            op="node",
            description=str(node_name or "unknown_node"),
        )
    except Exception:
        return None


def finish_span(span, status: str = "ok", **tags) -> None:
    """结束 span，可带状态与标签。未启用 no-op。"""
    if span is None:
        return
    try:
        for k, v in tags.items():
            if v is not None:
                span.set_tag(str(k), str(v))
        span.set_status(status)
        span.finish()
    except Exception:
        pass


def finish_transaction(transaction, status: str = "ok") -> None:
    """结束任务 transaction。未启用 no-op。"""
    if transaction is None:
        return
    try:
        transaction.set_status(status)
        transaction.finish()
    except Exception:
        pass
