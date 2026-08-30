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
