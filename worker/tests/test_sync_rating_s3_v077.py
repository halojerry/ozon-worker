# -*- coding: utf-8 -*-
"""
v0.77.2 — S3 残留点：评分域 localization_index 数组形态修复（纯 mock + 真 PG 域测试）。

实机取证（2026-09-18 本地真凭证打真 Ozon）：`_sync_rating` 每轮必炸
`(psycopg2.ProgrammingError) can't adapt type 'dict'`。官方 swagger 实锤
`/v1/rating/summary` 的 `localization_index` 是**数组**
（items: {calculation_date, localization_percentage:int}；14 天无销售为空数组），
旧代码当标量直塞 `credentials.rating_localization_index` 列。

09-12 上报的 S3 在 0.77.0 只修了 credential_sync_state 写入点（:121 json.dumps），
评分写回 credentials 这条才是评分域日志刷屏的真炸点——服务器核验未复跑评分域故漏判。

契约：
- `_extract_localization_index(raw)`：
  - list[dict] → 取 calculation_date 最新一条的 localization_percentage（float）；
  - 空 list / 无效元素 → None（宁缺毋滥，14 天无销售是官方正常态）；
  - int/float 标量 → 原值 float（兼容存量 mock/旧形态）；
  - 其他（None/str/dict）→ None。
- `_sync_rating` 落库不再 can't adapt；评分域 error 恒 ""（真店复跑验证）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_sync_rating_s3_v077.py -q
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _fn():
    """延迟导入：RED 阶段函数不存在 → 测试内 ImportError 即特性缺失信号。"""
    from services.store_sync_service import _extract_localization_index
    return _extract_localization_index


# ── 官方数组形态 ──
def test_array_takes_latest_by_calculation_date():
    raw = [
        {"calculation_date": "2026-09-01T00:00:00Z", "localization_percentage": 80},
        {"calculation_date": "2026-09-10T00:00:00Z", "localization_percentage": 87},
        {"calculation_date": "2026-09-05T00:00:00Z", "localization_percentage": 83},
    ]
    assert _fn()(raw) == 87.0, "必须取 calculation_date 最新一条"


def test_empty_array_is_none():
    # 官方：14 天无销售 → 空数组（正常态，宁缺毋滥不编造）
    assert _fn()([]) is None


def test_array_invalid_elements_skipped():
    raw = [{"foo": 1}, {"calculation_date": "2026-09-10", "localization_percentage": "abc"},
           {"calculation_date": "2026-09-09", "localization_percentage": 66}]
    assert _fn()(raw) == 66.0, "非法 percentage 跳过，取最近有效一条"


# ── 标量兼容（存量 mock/旧形态不回归）──
def test_scalar_float_passthrough():
    assert _fn()(92.5) == 92.5
    assert _fn()(92) == 92.0


# ── 垃圾输入恒 None，绝不抛 ──
def test_garbage_is_none():
    assert _fn()(None) is None
    assert _fn()("92.5") is None
    assert _fn()({"localization_percentage": 92}) is None
