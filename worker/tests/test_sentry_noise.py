"""R6 (v0.62): Sentry 噪音治理 — task_rerun 条件上报 + stale 全零不报 + 语言噪音聚合。

覆盖：
- _should_report_task_rerun：STALE_RUNNING/ZOMBIE/超时 → True；正常失败重试 → False
- task_rerun 上报只在命中僵尸/超时关键词时触发（capture_task_event mock 断言）
- stale_running 全零不调用 capture（main.py 守卫）
- 语言噪音指纹聚合不回归（_is_lang_noise_event）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from utils.task_processor import _should_report_task_rerun


# ═══ 判定函数 ═══

def test_rerun_stale_running_reported():
    assert _should_report_task_rerun(1, "[STALE_RUNNING] 任务运行超30分钟未更新") is True


def test_rerun_zombie_reported():
    assert _should_report_task_rerun(2, "ZOMBIE 恢复") is True
    assert _should_report_task_rerun(2, "zombie_reset") is True


def test_rerun_timeout_reported():
    assert _should_report_task_rerun(3, "任务执行超时") is True


def test_rerun_normal_retry_not_reported():
    assert _should_report_task_rerun(1, "Ozon API 返回 400: 属性值不正确") is False
    assert _should_report_task_rerun(2, "") is False


def test_rerun_first_run_not_reported():
    assert _should_report_task_rerun(0, "[STALE_RUNNING] 任意") is False


# ═══ stale_running 全零不报（main.py 守卫）═══

def test_stale_running_zero_counts_no_capture():
    """main.py 定期清理仅在 r1/r1f/r2 非零时调用 capture_task_event。"""
    import main as main_mod

    src = open(main_mod.__file__, encoding="utf-8").read()
    # 守卫：`if r1 or r1f or r2:` 之后才 capture_task_event
    assert "if r1 or r1f or r2:" in src
    # 找到 capture 调用位置，确认在守卫块内
    guard_idx = src.index("if r1 or r1f or r2:")
    capture_idx = src.index('"stale_running_reset"')
    assert capture_idx > guard_idx, "capture_task_event 必须在非零守卫块内"


# ═══ 语言噪音聚合不回归 ═══

def test_lang_noise_aggregation_not_regressed():
    """中文字符/拉丁字母/描述含 噪音事件仍被 before_send 聚合（v0.32 不回归）。"""
    import utils.sentry_setup as ss

    assert ss._is_lang_noise_event(
        {"logger": "graphs.nodes.prepare_ozon_upload_node", "message": "item[0]描述含拉丁字母"}
    ) is True
    assert ss._is_lang_noise_event(
        {"logger": "graphs.nodes.ozon_validate_node", "logentry": {"formatted": "描述含中文字符"}}
    ) is True
    assert ss._is_lang_noise_event(
        {"logger": "graphs.nodes.prepare_ozon_upload_node", "message": "正常处理"}
    ) is False


def test_lang_noise_fingerprint_set():
    import utils.sentry_setup as ss

    event = {
        "logger": "graphs.nodes.ozon_validate_node",
        "message": "描述含拉丁字母",
    }
    ss._SENTRY_ENABLED = True
    out = ss._before_send(event, None)
    assert out["fingerprint"] == [ss._LANG_NOISE_FINGERPRINT]
    assert out["level"] == "warning"
    ss._SENTRY_ENABLED = False


# ═══ MXOU 永久错误聚合（v0.63.1）═══

def _exc_event():
    """伪造一条 exception 事件（hint 带 exc_info）。"""
    return {"level": "error", "message": "任务执行异常", "extra": {}}


def _make_exc_info(exc):
    tb = exc.__traceback__
    return (type(exc), exc, tb)


def test_mxou_permanent_aggregation_exception_type():
    """MxouOutOfQuotaError exception 事件 → 单一 fingerprint + warning（按异常类型）。"""
    import utils.sentry_setup as ss
    from utils.mxou_api import MxouContentViolationError, MxouOutOfQuotaError

    for exc in (
        MxouOutOfQuotaError("OUT_OF_QUOTA: x"),
        MxouContentViolationError("grsai 内容违规: policy"),
    ):
        ss._SENTRY_ENABLED = True
        out = ss._before_send(_exc_event(), {"exc_info": _make_exc_info(exc)})
        assert out["fingerprint"] == [ss._MXOU_PERMANENT_FINGERPRINT], exc
        assert out["level"] == "warning", exc
        assert out["extra"]["noise_group"] == "mxou_permanent", exc
    ss._SENTRY_ENABLED = False


def test_mxou_permanent_aggregation_message_signal():
    """message 事件按消息信号兜底（OUT_OF_QUOTA:/内容违规）。"""
    import utils.sentry_setup as ss

    for msg in ("OUT_OF_QUOTA: MXOU balance insufficient", "grsai 内容违规: adult"):
        ss._SENTRY_ENABLED = True
        out = ss._before_send({"level": "error", "message": msg}, None)
        assert out["fingerprint"] == [ss._MXOU_PERMANENT_FINGERPRINT], msg
    ss._SENTRY_ENABLED = False


def test_mxou_permanent_aggregation_not_regressed():
    """普通异常/瞬时故障 → 不聚合（原样返回）。"""
    import utils.sentry_setup as ss

    ss._SENTRY_ENABLED = True
    event = {"level": "error", "message": "网络超时", "logger": "utils.mxou_api"}
    out = ss._before_send(event, {"exc_info": _make_exc_info(RuntimeError("timeout"))})
    assert "fingerprint" not in out, "瞬时故障不应被永久错误聚合"
    ss._SENTRY_ENABLED = False


# ═══ 高频业务噪音聚合（v0.63.1 架构优化 S4）═══

def test_ozon_upload_error_aggregated():
    """Ozon 上传失败连打 error（Ozon API错误/完整错误响应）→ 单一 fingerprint + warning。"""
    import utils.sentry_setup as ss

    for msg in ("Ozon API错误: 400 bad", "完整错误响应: {...}"):
        ss._SENTRY_ENABLED = True
        out = ss._before_send({"level": "error", "message": msg, "logger": "graphs.nodes.ozon_upload_node"}, None)
        assert out["fingerprint"] == [ss._OZON_UPLOAD_FINGERPRINT], msg
        assert out["level"] == "warning", msg
        assert out["extra"]["noise_group"] == "ozon_upload", msg
    ss._SENTRY_ENABLED = False


def test_attr_translate_skip_aggregated():
    """属性俄语翻译失败跳过（每属性 1 条）→ attribute-translate-skip 聚合。"""
    import utils.sentry_setup as ss

    ss._SENTRY_ENABLED = True
    out = ss._before_send(
        {"level": "error", "message": "❌ 属性1234俄语翻译失败或非俄语，跳过该属性: xxx",
         "logger": "graphs.nodes.prepare_ozon_upload_node"}, None)
    assert out["fingerprint"] == [ss._ATTR_TRANSLATE_FINGERPRINT]
    assert out["level"] == "warning"
    assert out["extra"]["noise_group"] == "attribute_translate"
    ss._SENTRY_ENABLED = False


def test_validation_detail_aggregated():
    """Ozon 预检测严重错误汇总 → validation-detail 聚合。"""
    import utils.sentry_setup as ss

    ss._SENTRY_ENABLED = True
    out = ss._before_send(
        {"level": "error", "message": "Ozon预检测发现严重错误: 3个",
         "logger": "graphs.nodes.ozon_validate_node"}, None)
    assert out["fingerprint"] == [ss._VALIDATION_DETAIL_FINGERPRINT]
    assert out["level"] == "warning"
    assert out["extra"]["noise_group"] == "validation_detail"
    ss._SENTRY_ENABLED = False


def test_business_noise_wrong_logger_passthrough():
    """消息命中业务噪音关键词但 logger 不是已知源 → 原样通过。"""
    import utils.sentry_setup as ss

    ss._SENTRY_ENABLED = True
    event = {"level": "error", "message": "Ozon API错误: 400", "logger": "utils.ozon_client"}
    out = ss._before_send(event, None)
    assert out is event
    assert "fingerprint" not in out
    assert out["level"] == "error"
    ss._SENTRY_ENABLED = False
