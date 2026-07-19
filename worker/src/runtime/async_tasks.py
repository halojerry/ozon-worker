"""Stub replacements for coze_coding_utils.async_tasks."""


class AsyncTaskRuntime:
    """Stub async task runtime."""

    def __init__(self, *args, **kwargs):
        pass

    async def submit(self, *args, **kwargs):
        raise NotImplementedError("AsyncTaskRuntime.submit not implemented")

    async def get_result(self, *args, **kwargs):
        raise NotImplementedError("AsyncTaskRuntime.get_result not implemented")


class AsyncTaskStorageError(Exception):
    """Stub storage error."""
    pass


def extract_biz_context(payload: dict) -> dict:
    """Extract business context from payload."""
    return payload.get("biz_context", payload)


def parse_deadline_sec(headers: dict) -> int:
    """Parse deadline from headers."""
    return 900  # Default 15 minutes


# Config stub
class _AsyncConfig:
    pass


config = _AsyncConfig()

HEADER_X_RUN_ID = "X-Run-Id"
