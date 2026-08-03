#!/usr/bin/env python3
"""v0.19 任务统计字段映射单测（仅标准库）。

运行：cd worker && PYTHONPATH=src python3 tests/test_task_statistics.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.task_statistics import statistics_payload  # noqa: E402


def test_payload_maps_to_response_fields():
    p = statistics_payload(total=10, completed=7, failed=2, running=1,
                           pending=0, cancelled=1, avg_duration_seconds=123.456)
    assert p == {
        "total": 10, "pending": 0, "running": 1,
        "completed": 7, "failed": 2, "cancelled": 1,
        "avg_duration_seconds": 123.46,
    }


def test_payload_empty_table():
    p = statistics_payload(total=0, completed=0, failed=0, running=0, pending=0)
    assert p["total"] == 0 and p["avg_duration_seconds"] is None


def test_payload_none_coerced_to_zero():
    p = statistics_payload(total=None, completed=None, failed=None,
                           running=None, pending=None)
    assert p["total"] == 0 and p["completed"] == 0 and p["pending"] == 0


def _main() -> int:
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"✅ {name}")
            except AssertionError as exc:
                failed += 1
                print(f"❌ {name}: {exc}")
            except Exception as exc:
                failed += 1
                print(f"❌ {name}: {type(exc).__name__}: {exc}")
    print(f"\n{sum(1 for n in globals() if n.startswith('test_') and callable(globals()[n])) - failed}/"
          f"{sum(1 for n in globals() if n.startswith('test_') and callable(globals()[n]))} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
