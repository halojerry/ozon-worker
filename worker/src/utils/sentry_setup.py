"""Sentry 错误监测（v0.23）— worker 侧统一初始化 + 任务异常捕获。

配置（deploy/.env）:
    SENTRY_DSN=...                 # 为空则完全禁用（no-op）
    SENTRY_ENV=production          # 环境标签（默认 production）
    SENTRY_TRACES_SAMPLE_RATE=0.1  # 性能采样率（默认 0.1）

用法:
    from utils.sentry_setup import init_sentry, capture_task_error
    init_sentry()                                   # main.py 模块加载时调用一次
    capture_task_error(e, task_id=..., tenant_id=...)  # task_processor 异常分支

噪音指纹登记表（已修复问题——后续 review 勿重复报告；新增噪音聚合必须在此登记 + 测试锁定）：
| fingerprint | 版本 | 命中特征 | 聚合行为 |
|---|---|---|---|
| language-noise-validation | v0.32 | 语言检查 logger.error（中文/拉丁特征）| 单一 fingerprint + level warning + trace_id |
| mxou-permanent-error | v0.63.1 | MxouOutOfQuotaError/MxouContentViolationError 或消息含 OUT_OF_QUOTA:/内容违规 | 单一 fingerprint + level warning（保留 token 指纹 user tag）|
| ozon-upload-error | v0.63.1 | ozon_upload_node logger.error（"Ozon API错误"/"完整错误响应"）| 单一 fingerprint + level warning |
| attribute-translate-skip | v0.63.1 | prepare_ozon_upload_node 属性翻译失败跳过（每属性 1 条）| 单一 fingerprint + level warning |
| validation-detail | v0.63.1 | ozon_validate_node 预检测严重错误汇总 | 单一 fingerprint + level warning |

发版修复 Sentry 问题后，按本表在 Sentry 后台 resolve（已修）/archive（已知聚合噪音），
流程见 docs/LOGGING.md「Sentry 错误监测」节。
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

# ============================================================
# v0.63.1: before_send — MXOU 永久错误（OutOfQuota/内容违规）聚合
# 余额/鉴权耗尽与内容违规是「用户状态」问题，不是代码 bug——每任务 1 个独立
# issue 会在大面积余额不足时刷屏。按异常类型（hint.exc_info）+ 消息信号兜底
# 聚合为单一 fingerprint + level 降 warning；保留 capture_task_error 已设的
# token 指纹 user tag（仍能按用户筛「哪个账号余额不足」）。
# ============================================================

_MXOU_PERMANENT_FINGERPRINT = "mxou-permanent-error"
_MXOU_PERMANENT_MSG_KEYWORDS = ("OUT_OF_QUOTA:", "内容违规")


# ============================================================
# v0.63.1 架构优化: before_send — 高频业务噪音聚合（每任务必现，非代码 bug）
# 深查发现生产事件大头来自三类业务失败：Ozon 上传失败（连打 2 条 error 且
# 第二条含全量 body）、属性俄语翻译失败跳过（每属性 1 条）、Ozon 预检测汇总。
# 命中 → 单一 fingerprint + level warning，防止 Sentry 后台持续生成独立 issue。
# ============================================================

_OZON_UPLOAD_FINGERPRINT = "ozon-upload-error"
_OZON_UPLOAD_MSG_KEYWORDS = ("Ozon API错误", "完整错误响应")

_ATTR_TRANSLATE_FINGERPRINT = "attribute-translate-skip"
_ATTR_TRANSLATE_MSG_KEYWORDS = ("俄语翻译失败或非俄语，跳过该属性", "翻译失败", "跳过该属性")

_VALIDATION_DETAIL_FINGERPRINT = "validation-detail"
_VALIDATION_DETAIL_MSG_KEYWORDS = ("Ozon预检测发现严重错误",)


def _is_ozon_upload_error_event(event: dict) -> bool:
    logger_name = (event.get("logger") or "").lower()
    if "ozon_upload_node" not in logger_name:
        return False
    msg = _event_message(event)
    return any(kw in msg for kw in _OZON_UPLOAD_MSG_KEYWORDS)


def _is_attr_translate_skip_event(event: dict) -> bool:
    logger_name = (event.get("logger") or "").lower()
    if "prepare_ozon_upload_node" not in logger_name:
        return False
    msg = _event_message(event)
    return any(kw in msg for kw in _ATTR_TRANSLATE_MSG_KEYWORDS)


def _is_validation_detail_event(event: dict) -> bool:
    logger_name = (event.get("logger") or "").lower()
    if "ozon_validate_node" not in logger_name:
        return False
    msg = _event_message(event)
    return any(kw in msg for kw in _VALIDATION_DETAIL_MSG_KEYWORDS)


def _is_mxou_permanent_event(event: dict, hint: Optional[dict]) -> bool:
    """永久错误判定：hint 带异常类型优先；message 事件按消息信号兜底。"""
    if hint and isinstance(hint.get("exc_info"), tuple) and hint["exc_info"][0]:
        exc = hint["exc_info"][0]
        return exc.__name__ in ("MxouOutOfQuotaError", "MxouContentViolationError")
    return any(kw in _event_message(event) for kw in _MXOU_PERMANENT_MSG_KEYWORDS)


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


def _has_python_traceback(event: dict) -> bool:
    """v0.64 P2: 事件含 Python 异常栈帧 → 跳过噪音聚合（可能是真实代码 bug）。"""
    exc = event.get("exception") or {}
    values = exc.get("values") or []
    for v in values:
        if isinstance(v, dict) and (v.get("stacktrace") or {}).get("frames"):
            return True
    return False


def _before_send(event: dict, hint: Optional[dict] = None) -> dict:
    """噪音指纹聚合：语言检查（v0.32）+ MXOU 永久错误（v0.63.1）+ 高频业务噪音（v0.63.1）。未启用短路零开销。

    v0.64 P2: 含 Python 异常栈帧的事件跳过全部噪音聚合——可能是真实代码 bug，
    不应被降级为 warning 或折叠进单一 fingerprint。
    """
    if not _SENTRY_ENABLED or not isinstance(event, dict):
        return event
    # 有 Python traceback 的事件不走噪音聚合（保留原始 level + 独立 fingerprint）
    if _has_python_traceback(event):
        return event
    if _is_lang_noise_event(event):
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
    if _is_mxou_permanent_event(event, hint):
        event["fingerprint"] = [_MXOU_PERMANENT_FINGERPRINT]
        event["level"] = "warning"
        extra = event.setdefault("extra", {})
        if not isinstance(extra, dict):
            extra = {}
            event["extra"] = extra
        extra["noise_group"] = "mxou_permanent"
        return event
    if _is_ozon_upload_error_event(event):
        event["fingerprint"] = [_OZON_UPLOAD_FINGERPRINT]
        event["level"] = "warning"
        extra = event.setdefault("extra", {})
        if not isinstance(extra, dict):
            extra = {}
            event["extra"] = extra
        extra["noise_group"] = "ozon_upload"
        return event
    if _is_attr_translate_skip_event(event):
        event["fingerprint"] = [_ATTR_TRANSLATE_FINGERPRINT]
        event["level"] = "warning"
        extra = event.setdefault("extra", {})
        if not isinstance(extra, dict):
            extra = {}
            event["extra"] = extra
        extra["noise_group"] = "attribute_translate"
        return event
    if _is_validation_detail_event(event):
        event["fingerprint"] = [_VALIDATION_DETAIL_FINGERPRINT]
        event["level"] = "warning"
        extra = event.setdefault("extra", {})
        if not isinstance(extra, dict):
            extra = {}
            event["extra"] = extra
        extra["noise_group"] = "validation_detail"
        return event
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
    未启用时 no-op。SDK 后台异步批量发送（v0.63.1 起不再同步 flush）。
    """
    if not _SENTRY_ENABLED:
        return
    try:
        import sentry_sdk  # type: ignore

        # v0.63.1 架构优化: configure_scope 在 sentry-sdk 2.x 操作共享 isolation
        # scope 且不还原 → task_id/tenant tag 永久残留、跨任务串号。new_scope 为
        # 事件级 scope（fork 当前、退出还原），tag/extra 只附着到本条事件。
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("task_event", event_type)
            if task_id:
                scope.set_tag("task_id", task_id)
            if tenant_id:
                scope.set_tag("tenant_id", tenant_id)
            for k, v in extras.items():
                scope.set_extra(k, v)
            sentry_sdk.capture_message(message, level=level, scope=scope)
        # v0.63.1: 不再同步 flush——SDK 默认后台异步批量发送（worker 长驻进程，
        # 事件会在退出前送达）；同步 flush 在 async 上下文最多阻塞 1s/次，
        # 并发任务同时失败时会串行卡顿事件循环。
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
    """任务级异常上报：带 task_id/tenant_id/token 指纹上下文。未启用时 no-op。

    v0.63.1: 不再同步 flush（防 async 上下文阻塞事件循环），SDK 后台异步送达。
    """
    if not _SENTRY_ENABLED:
        return
    try:
        import sentry_sdk  # type: ignore

        # v0.63.1 架构优化: configure_scope 在 2.x 操作共享 isolation scope 且不
        # 还原 → token 指纹/task_id/tenant 永久残留、跨任务串号（并发 50 worker
        # 下 Sentry 归因失真）。new_scope 为事件级 scope，tag/user 只附着本条事件。
        with sentry_sdk.new_scope() as scope:
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
                sentry_sdk.capture_exception(exc, scope=scope)
            if message:
                sentry_sdk.capture_message(message, scope=scope)
        # v0.63.1: 不再同步 flush——任务失败路径在 async 上下文，flush(timeout=2)
        # 同步阻塞事件循环最多 2s/任务；50 并发任务同时失败（MXOU 大面积故障/
        # 余额不足）会串行卡顿。SDK 默认后台异步批量发送，worker 长驻进程，
        # 事件会在退出前送达。
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
