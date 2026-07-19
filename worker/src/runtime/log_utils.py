"""Minimal replacements for local and trace utilities."""

import os
import logging
import traceback

LOG_FILE = os.environ.get("LOG_FILE", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


def setup_logging(
    log_file: str = "",
    max_bytes: int = 100 * 1024 * 1024,
    backup_count: int = 5,
    log_level: str = "INFO",
    use_json_format: bool = False,
    console_output: bool = True,
) -> None:
    """Setup Python logging with optional file and console output."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Console handler
    if console_output:
        ch = logging.StreamHandler()
        ch.setLevel(root.level)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        ch.setFormatter(formatter)
        root.addHandler(ch)

    # File handler
    if log_file:
        fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
        fh.setLevel(root.level)
        fh.setFormatter(formatter)
        root.addHandler(fh)


class request_context:
    """Context manager stub for request-level logging context."""

    _ctx = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @classmethod
    def set(cls, ctx) -> None:
        cls._ctx = ctx

    @classmethod
    def get(cls):
        return cls._ctx


def extract_core_stack() -> str:
    """Extract the most relevant part of the current traceback."""
    return traceback.format_exc()


class LangGraphParser:
    """Stub for LangGraph log parser."""

    @staticmethod
    def parse(log_line: str) -> dict:
        return {"raw": log_line}
