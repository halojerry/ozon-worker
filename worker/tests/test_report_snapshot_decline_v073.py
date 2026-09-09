"""v0.73 Task9: 错误通知文案如实化 + 报告快照附带末次拒绝原文。

生产实证（2026-09-09）：失败任务 error_message 显示「属性值含中文字符被拒绝:已净化
处理」——ERROR_NOTICE_MAP 的修复动作说明被当终态展示，用户困惑「说净化了怎么还
失败」。锁定两件事：
1) ERROR_NOTICE_MAP 自动修复过但仍失败的码，文案必须含「仍未通过/需人工检查」，
   禁止「已净化处理」式已完成错觉（真话行如值数闸不动）；
2) _task_snapshots 附带留存表 listing_result_log 末次拒绝原文——last_decline 截
   前 2 条（message 截 300）+ error_code/error_hint 回落；留存查询失败非致命，
   快照仍返回基础字段。

纯 mock：monkeypatch services.error_report_service.get_engine 假 conn，不连 PG。
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import services.error_report_service as ers  # noqa: E402


# ── 假 engine/conn：按 SQL 里出现 listing_result_log 分流两查 ──────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, task_rows=(), log_rows=(), log_raises=None):
        self.task_rows = list(task_rows)
        self.log_rows = list(log_rows)
        self.log_raises = log_raises

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "listing_result_log" in sql:
            if self.log_raises is not None:
                raise self.log_raises
            return _FakeResult(self.log_rows)
        return _FakeResult(self.task_rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn


def _task_row(task_id, error_message=""):
    now = datetime.datetime(2026, 9, 9, 12, 0, 0)
    return {
        "id": task_id, "status": "failed", "error_message": error_message,
        "created_at": now, "completed_at": now, "product_id": "987654321",
    }


# ── ERROR_NOTICE_MAP 文案如实化 ────────────────────────────────────────────

def test_notice_map_chinese_lines_truthful():
    """生产实证三行：含「仍未通过」且不再有「已净化处理」错觉。"""
    from graphs.validation_retry_loop import ERROR_NOTICE_MAP
    for code in ("DESCRIPTION_DECLINE", "BR_chinese_hieroglyphs",
                 "BR_chinese_hieroglyphs_in_attribute"):
        txt = ERROR_NOTICE_MAP[code]
        assert "仍未通过" in txt, f"{code} 文案缺「仍未通过」: {txt}"
        assert "需人工检查" in txt, f"{code} 文案缺「需人工检查」: {txt}"
        assert "已净化处理" not in txt, f"{code} 仍是已完成错觉: {txt}"


def test_notice_map_repair_tone_codes_truthful():
    """「已尝试…」式纯动作口吻的六行同样如实化。"""
    from graphs.validation_retry_loop import ERROR_NOTICE_MAP
    for code in ("INVALID_ATTRIBUTE_VALUE", "MISSING_REQUIRED_ATTRIBUTE",
                 "VALUE_MUST_BE_INTEGER", "VALUE_MUST_BE_DECIMAL",
                 "CONDITIONAL_ATTRIBUTE_ERROR", "INCORRECT_DENSITY"):
        txt = ERROR_NOTICE_MAP[code]
        assert "仍未通过" in txt, f"{code} 文案缺「仍未通过」: {txt}"
        assert "已尝试" not in txt, f"{code} 仍是动作说明口吻: {txt}"


def test_notice_map_truthful_lines_untouched():
    """本就如实的行不动：值数闸（v0.72 真话）/危险品/社媒/标签/重复上架。"""
    from graphs.validation_retry_loop import ERROR_NOTICE_MAP
    assert "已删多值保留首个" in ERROR_NOTICE_MAP["ATTRIBUTE_VALUE_COUNT_EXCEEDED"]
    assert "无法自动修复" in ERROR_NOTICE_MAP["BR_hazard_class1"]
    assert "极端组织" in ERROR_NOTICE_MAP["FB_INSTA"]
    assert "已移除违规标签" in ERROR_NOTICE_MAP["BR_hashtag_brand"]
    assert "无法重复上架" in ERROR_NOTICE_MAP["SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT"]
    assert len(ERROR_NOTICE_MAP) == 18, "码级说明条数不应增减"


# ── _task_snapshots 末次拒绝原文附带 ───────────────────────────────────────

def test_snapshot_merges_last_decline(monkeypatch):
    """留存行 error_code 带出 + moderation_texts 截前 2 条/message 截 300。"""
    task = _task_row("tid-1", "E" * 500)
    log = {
        "task_db_id": "tid-1",
        "error_code": "BR_chinese_hieroglyphs_in_attribute",
        "moderation_texts": [
            {"code": "BR_chinese_hieroglyphs_in_attribute",
             "texts": {"message": "莫" * 500}},
            {"code": "DESCRIPTION_DECLINE", "texts": {"message": "short"}},
            {"code": "pics_http_error", "texts": {"message": "第三条必须被截掉"}},
        ],
    }
    eng = _FakeEngine(_FakeConn(task_rows=[task], log_rows=[log]))
    monkeypatch.setattr(ers, "get_engine", lambda: eng)

    snaps = ers._task_snapshots("t1", ["tid-1"])
    assert len(snaps) == 1
    s = snaps[0]
    assert s["task_id"] == "tid-1" and s["product_id"] == "987654321"
    assert s["error_code"] == "BR_chinese_hieroglyphs_in_attribute"
    assert "error_hint" not in s, "留存 error_code 在场时不得回落 error_hint"
    ld = s["last_decline"]
    assert len(ld) == 2, f"应截前 2 条，实际 {len(ld)}"
    assert ld[0]["texts"]["message"] == "莫" * 300, "message 应截 300 字"
    assert ld[1]["texts"]["message"] == "short"


def test_snapshot_error_hint_fallback(monkeypatch):
    """留存行 error_code 为空 → 回落 error_hint=任务 error_message 前 100 字。"""
    task = _task_row("tid-2", "网" * 250)
    log = {"task_db_id": "tid-2", "error_code": "", "moderation_texts": None}
    eng = _FakeEngine(_FakeConn(task_rows=[task], log_rows=[log]))
    monkeypatch.setattr(ers, "get_engine", lambda: eng)

    s = ers._task_snapshots("t1", ["tid-2"])[0]
    assert "error_code" not in s
    assert s["error_hint"] == "网" * 100, "error_hint 应为任务 error_message 前 100 字"
    assert "last_decline" not in s, "moderation_texts 为空 → 不带 last_decline"


def test_snapshot_log_failure_non_fatal(monkeypatch):
    """留存查询抛异常 → warning 后快照仍返回基础字段。"""
    task = _task_row("tid-3", "boom")
    eng = _FakeEngine(_FakeConn(task_rows=[task], log_raises=RuntimeError("pg boom")))
    monkeypatch.setattr(ers, "get_engine", lambda: eng)

    snaps = ers._task_snapshots("t1", ["tid-3"])
    assert len(snaps) == 1 and snaps[0]["task_id"] == "tid-3"
    s = snaps[0]
    assert s["error_message"] == "boom" and s["product_id"] == "987654321"
    assert "last_decline" not in s and "error_code" not in s and "error_hint" not in s


def test_snapshot_no_listing_row_base_only(monkeypatch):
    """无留存行（旧任务/表空）→ 快照保持基础字段，不空挂新键。"""
    task = _task_row("tid-4", "legacy")
    eng = _FakeEngine(_FakeConn(task_rows=[task], log_rows=[]))
    monkeypatch.setattr(ers, "get_engine", lambda: eng)

    s = ers._task_snapshots("t1", ["tid-4"])[0]
    assert s["task_id"] == "tid-4"
    assert "last_decline" not in s and "error_code" not in s and "error_hint" not in s


def test_clip_decline_accepts_json_string_and_garbage():
    """moderation_texts str 形态兜底（JSONB 意外走 text 通道时仍可解析）。"""
    raw = json.dumps([{"code": "X", "texts": {"message": "m" * 400}}], ensure_ascii=False)
    out = ers._clip_decline_texts(raw)
    assert out[0]["texts"]["message"] == "m" * 300
    assert ers._clip_decline_texts("not-json") == []
    assert ers._clip_decline_texts(None) == []
    assert ers._clip_decline_texts({"code": "X"}) == []  # 非 list 形态防御
