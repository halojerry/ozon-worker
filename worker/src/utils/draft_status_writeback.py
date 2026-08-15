"""draft_submissions 状态写回工具（WebUI 运营工作台 M0.2）。

`draft_submissions.status` 是草稿提交记录的状态列，与 worker 任务（ozon_product_tasks）
通过 `submitted_task_id` 关联。本模块在任务终态时把 worker 终态映射回 submission 状态并写库，
供 M0.3 在 task_processor 终态点调用。

非致命契约：写回失败（DB 不可用 / SQL 错误）只记录 warning，绝不 raise——
写回失败不能破坏任务终态落库。
"""
from sqlalchemy import text

from storage.database.db import get_engine
from utils.logger import get_logger

logger = get_logger("draft.status_writeback")

# worker 任务终态 → submission 状态
_WORKER_TO_SUBMISSION = {
    "completed": "published",
    "failed": "failed",
    "rejected": "rejected",
    "pending_moderation": "uploading",
}


def map_worker_status(status: str) -> str:
    """worker 任务终态 → draft_submissions.status；未知状态原样透传。"""
    return _WORKER_TO_SUBMISSION.get(status, status)


def writeback_submission_status(task_id: str, status: str, error_message: str | None = None) -> None:
    """按 submitted_task_id 回写 submission 状态。

    error_message 用 COALESCE 合并：传入 None 时保留库里已有值（失败原因不被清空）。
    整个函数非致命：任何异常 → logger.warning → 返回 None，绝不 raise。
    """
    try:
        with get_engine().connect() as conn:
            conn.execute(
                text(
                    "UPDATE draft_submissions SET status=:s, "
                    "error_message=COALESCE(:e, error_message), updated_at=NOW() "
                    "WHERE submitted_task_id=:task_id"
                ),
                {"s": status, "e": error_message, "task_id": task_id},
            )
            conn.commit()
    except Exception:
        logger.warning("draft_submissions 状态写回失败 task=%s status=%s", task_id, status, exc_info=True)
