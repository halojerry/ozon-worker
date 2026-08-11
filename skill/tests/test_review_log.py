#!/usr/bin/env python3
"""D3 L2: review_log.jsonl 追加式决策审计落盘（TDD RED→GREEN）。

- 单条/多条记录追加写（JSONL 每行一条，含标准 shape 字段）
- 损坏输入（None / 非 dict / 不可序列化值）→ 静默 no-op，绝不 raise
- 默认路径位于 SKILL_ROOT/data/ 下（与 _save_discovery_log 约定一致）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_review_log.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import review_log  # noqa: E402

_ORIGINAL_PATH = review_log.REVIEW_LOG_PATH


@pytest.fixture(autouse=True)
def _restore_review_log_path():
    yield
    review_log.REVIEW_LOG_PATH = _ORIGINAL_PATH


def _use_tmp_path(tmp_path: Path) -> Path:
    """把写入目标指向临时文件，避免污染真实 data/。"""
    p = tmp_path / "review_log.jsonl"
    review_log.REVIEW_LOG_PATH = p
    return p


def _sample_record(**overrides) -> dict:
    rec = {
        "task_id": "t1",
        "product_id": "p1",
        "ozon_title": "Автопоилка для кошек",
        "match_title": "宠物自动饮水器",
        "match_url": "https://detail.1688.com/offer/1001.html",
        "confidence": 0.7,
        "badge_eff": 0.667,
        "score": 65.0,
        "reject_reason": "",
        "decision": "auto_pass",
        "image_urls": ["https://img/1688/1.jpg"],
    }
    rec.update(overrides)
    return rec


def test_append_single_and_multiple_records(tmp_path):
    """追加写：两条记录 = 两行 JSONL，ts 自动填充，标准字段齐全。"""
    path = _use_tmp_path(tmp_path)
    review_log.write_review_record(_sample_record())
    review_log.write_review_record(_sample_record(
        product_id="p2", decision="block", reject_reason="all_filtered",
        confidence=0.0, badge_eff=0.0, score=0.0, image_urls=[]))

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, f"每条记录一行，实际 {len(lines)} 行"

    rec = json.loads(lines[0])
    assert rec["decision"] == "auto_pass"
    assert rec["product_id"] == "p1"
    assert rec["confidence"] == 0.7
    assert rec["match_url"] == "https://detail.1688.com/offer/1001.html"
    assert rec["image_urls"] == ["https://img/1688/1.jpg"]
    for key in ("ts", "task_id", "product_id", "ozon_title", "match_title",
                "match_url", "confidence", "badge_eff", "score",
                "reject_reason", "decision", "image_urls"):
        assert key in rec, f"标准 shape 缺字段 {key}"

    rec2 = json.loads(lines[1])
    assert rec2["decision"] == "block"
    assert rec2["reject_reason"] == "all_filtered"


def test_corrupt_input_never_raises(tmp_path):
    """损坏输入（None / 非 dict / 不可序列化值）→ fail-open，绝不 raise。"""
    _use_tmp_path(tmp_path)
    review_log.write_review_record(None)
    review_log.write_review_record("not a dict")
    review_log.write_review_record([1, 2, 3])
    review_log.write_review_record({"decision": "block", "bad": object()})
    # 到达这里即通过（任何上述调用都不允许抛异常）


def test_path_under_data_dir():
    """默认路径必须位于 SKILL_ROOT/data/ 下（与 _save_discovery_log 约定一致）。"""
    p = review_log.REVIEW_LOG_PATH
    assert p.name == "review_log.jsonl"
    assert p.parent.name == "data", f"review_log 应落在 data/ 下, got {p}"
    # 用默认路径跑一次真实写（data/ 是运行时目录，测试可写）
    try:
        review_log.write_review_record(_sample_record(decision="probe"))
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert lines and json.loads(lines[-1])["decision"] == "probe"
    finally:
        if p.exists():
            p.unlink(missing_ok=True)


def test_concurrent_writes_are_thread_safe(tmp_path):
    """match_selected P2 并行路径：多线程并发写 → 每行都是完整合法 JSON。"""
    path = _use_tmp_path(tmp_path)
    import threading

    errors: list[BaseException] = []

    def _writer(i: int) -> None:
        try:
            for _ in range(20):
                review_log.write_review_record(_sample_record(product_id=f"p{i}"))
        except BaseException as exc:  # noqa: BLE001 - 测试收集异常
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发写入不得 raise: {errors}"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 80, f"4×20 条记录, 实际 {len(lines)}"
    for line in lines:
        assert json.loads(line)["decision"] == "auto_pass", f"损坏行: {line[:80]}"


if __name__ == "__main__":
    import inspect
    import tempfile
    import traceback

    failed = total = 0
    with tempfile.TemporaryDirectory() as td:
        _use_tmp_path(Path(td) / "x.jsonl")
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                total += 1
                try:
                    if "tmp_path" in inspect.signature(fn).parameters:
                        fn(Path(td))
                    else:
                        fn()
                    print(f"PASS {name}")
                except Exception:
                    failed += 1
                    print(f"FAIL {name}")
                    traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
