#!/usr/bin/env python3
"""Error codes and exception classes for pounding-ozon-probe.

v0.80: ERR_CLOUD_* 云端 webhook 错误码已随前代 n8n 云端退役删除
（docs/PLAN-n8n-legacy-purge-v1.md）。
"""

# Config error codes
ERR_MISSING_CONFIG = "MISSING_CONFIG"


class SkillError(Exception):
    def __init__(self, message: str, code: str = "SKILL_ERROR"):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ConfigError(SkillError):
    def __init__(self, message: str):
        super().__init__(message, "CONFIG_ERROR")


class ValidationError(SkillError):
    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR")
