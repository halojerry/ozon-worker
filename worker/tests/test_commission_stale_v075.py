"""repo-gov 一期·佣金缓存 180d 新鲜度闸单测（BL-24，TDD）。

覆盖 design-b2b-cache-ttl-governance.md §3 Phase 1：
- resolve_commission_rate_detail：新鲜行采信 / 200d 超龄行降级 fallback:stale /
  边界（恰 180d 采信、180d+1s 降级）/ 无 updated_at 历史行保守视同超龄 /
  超龄 + segments 命中时 source 如实标 segments 但 stale=True；
- resolve_commission_rate 薄壳：新鲜行逐字等价旧行为；
- get_category_commission：返回 dict 带 updated_at epoch；
- pricing_node：stale 时 pricing_info marks 带 commission_source="stale_fallback"。

⚠️ 行为变化（有意，PR 说明必提）：缓存行超龄从「照常采信」变为「降级
segments/0.10」——保守方向（宁可利润算保守不可虚高）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_commission_stale_v075.py -q
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.commission_resolver import (  # noqa: E402
    COMMISSION_STALE_AFTER_DAYS,
    FALLBACK_RATE,
    get_category_commission,
    resolve_commission_rate,
    resolve_commission_rate_detail,
)


def _fresh_row(pct_by_band: dict | None = None) -> dict:
    row = {
        "fbs_leq_1500": 8.0,
        "fbs_leq_5000": 12.0,
        "fbs_gt_5000": 18.0,
        "source": "what_to_sell",
        "updated_at": time.time(),
    }
    row.update(pct_by_band or {})
    return row


# ═══════════ detail：新鲜行采信 ═══════════

def test_detail_fresh_row_trusted():
    """新鲜行（1d）→ 采信 cache:{band}，stale=False（设计验收探针）。"""
    d = resolve_commission_rate_detail(
        123, 800, 0, get_category_commission_fn=lambda _dc: _fresh_row(),
        now_fn=lambda: time.time(),
    )
    assert d == {"rate": 0.08, "source": "cache:leq_1500", "stale": False}


def test_detail_stale_row_falls_back():
    """超龄行（200d）→ 不采信，降级 (0.10, "fallback:stale")，stale=True（设计验收探针）。"""
    stale_row = _fresh_row()
    stale_row["updated_at"] = time.time() - 200 * 86400
    d = resolve_commission_rate_detail(
        123, 800, 0, get_category_commission_fn=lambda _dc: stale_row,
        now_fn=lambda: time.time(),
    )
    assert d == {"rate": 0.10, "source": "fallback:stale", "stale": True}
    assert d["rate"] == FALLBACK_RATE


def test_detail_freshness_boundary_exact_180d():
    """边界：行龄恰 = 180d → 新鲜（采信）；180d+1s → 超龄（降级）。"""
    now = 1_800_000_000.0
    row_exact = _fresh_row()
    row_exact["updated_at"] = now - COMMISSION_STALE_AFTER_DAYS * 86400
    d = resolve_commission_rate_detail(
        123, 800, 0, get_category_commission_fn=lambda _dc: row_exact, now_fn=lambda: now)
    assert d["stale"] is False and d["source"] == "cache:leq_1500"

    row_over = _fresh_row()
    row_over["updated_at"] = now - COMMISSION_STALE_AFTER_DAYS * 86400 - 1
    d = resolve_commission_rate_detail(
        123, 800, 0, get_category_commission_fn=lambda _dc: row_over, now_fn=lambda: now)
    assert d["stale"] is True and d["source"] == "fallback:stale"


def test_detail_missing_updated_at_treated_stale():
    """无 updated_at（历史行/mock）→ 保守视同超龄降级（设计 §3 Phase 1.4）。"""
    row = _fresh_row()
    row.pop("updated_at")
    d = resolve_commission_rate_detail(
        123, 800, 0, get_category_commission_fn=lambda _dc: row,
        now_fn=lambda: time.time(),
    )
    assert d["stale"] is True and d["source"] == "fallback:stale" and d["rate"] == 0.10


def test_detail_stale_with_segments_keeps_segment_source():
    """超龄 + extensions segments 命中 → 用 segments 值，source 如实标 segments，stale=True。"""
    stale_row = _fresh_row()
    stale_row["updated_at"] = time.time() - 200 * 86400
    segments = {"fbs": {"leq_1500": 9.0, "leq_5000": 13.0, "gt_5000": 20.0}}
    d = resolve_commission_rate_detail(
        123, 800, 0,
        extensions_commission_segments=segments,
        get_category_commission_fn=lambda _dc: stale_row,
        now_fn=lambda: time.time(),
    )
    assert d == {"rate": 0.09, "source": "segments:leq_1500", "stale": True}


def test_detail_stale_threshold_configurable():
    """stale_after_days 可调（keyword-only）：7d 阈值下 8d 行降级、6d 行采信。"""
    now = 1_800_000_000.0
    row8 = _fresh_row()
    row8["updated_at"] = now - 8 * 86400
    d = resolve_commission_rate_detail(
        123, 800, 0, get_category_commission_fn=lambda _dc: row8,
        stale_after_days=7, now_fn=lambda: now)
    assert d["stale"] is True

    row6 = _fresh_row()
    row6["updated_at"] = now - 6 * 86400
    d = resolve_commission_rate_detail(
        123, 800, 0, get_category_commission_fn=lambda _dc: row6,
        stale_after_days=7, now_fn=lambda: now)
    assert d["stale"] is False


def test_detail_polluted_row_not_marked_stale():
    """段值 0/缺失（污染行，v0.67 语义）→ 视同未命中但不标 stale（stale 专指超龄）。"""
    row = _fresh_row({"fbs_leq_1500": 0.0})
    d = resolve_commission_rate_detail(
        123, 800, 0, get_category_commission_fn=lambda _dc: row, now_fn=lambda: time.time())
    assert d == {"rate": 0.10, "source": "fallback", "stale": False}


def test_detail_explicit_wins_and_no_cache_miss_paths_unchanged():
    """explicit 最高优先；无缓存行时 segments/fallback 行为与旧版一致（stale=False）。"""
    d = resolve_commission_rate_detail(
        123, 800, 0.12, get_category_commission_fn=lambda _dc: _fresh_row())
    assert d == {"rate": 0.12, "source": "explicit", "stale": False}

    d = resolve_commission_rate_detail(123, 800, 0)  # 无 fn 无 segments
    assert d == {"rate": 0.10, "source": "fallback", "stale": False}

    segments = {"fbs": {"leq_1500": 9.0, "leq_5000": 13.0, "gt_5000": 20.0}}
    d = resolve_commission_rate_detail(123, 800, 0, extensions_commission_segments=segments)
    assert d == {"rate": 0.09, "source": "segments:leq_1500", "stale": False}


# ═══════════ 薄壳：新鲜行逐字等价旧行为 ═══════════

def test_thin_shell_fresh_row_same_as_before():
    """resolve_commission_rate 薄壳对新鲜行返回 (rate, source) 与旧行为逐字一致。"""
    rate, source = resolve_commission_rate(
        123, 800, 0, get_category_commission_fn=lambda _dc: _fresh_row())
    assert (rate, source) == (0.08, "cache:leq_1500")


def test_thin_shell_stale_row_downgraded():
    """薄壳对超龄行返回降级值（fallback:stale）——有意行为变化，调用方无感升级。"""
    stale_row = _fresh_row()
    stale_row["updated_at"] = time.time() - 200 * 86400
    rate, source = resolve_commission_rate(
        123, 800, 0, get_category_commission_fn=lambda _dc: stale_row)
    assert (rate, source) == (0.10, "fallback:stale")


# ═══════════ get_category_commission 带 updated_at ═══════════

class _FakeSession:
    def __init__(self, first_result=None):
        self._first = first_result

    def execute(self, stmt):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._first

    def commit(self):
        pass

    def close(self):
        pass


def test_get_category_commission_returns_updated_at_epoch():
    """命中行返回 dict 带 updated_at epoch（datetime 归一）；新鲜度闸数据源。"""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc)
    row_obj = SimpleNamespace(
        fbs_leq_1500=8.0, fbs_leq_5000=12.0, fbs_gt_5000=18.0,
        fbo_leq_1500=7.0, fbo_leq_5000=11.0, fbo_gt_5000=16.0,
        source="what_to_sell", updated_at=ts,
    )
    row = get_category_commission(123, session=_FakeSession(row_obj))
    assert row is not None
    assert isinstance(row["updated_at"], float)
    assert abs(row["updated_at"] - ts.timestamp()) < 1


def test_get_category_commission_updated_at_none_safe():
    """updated_at 为 None（异常行）→ 返回 None（消费侧保守视同超龄），不抛。"""
    row_obj = SimpleNamespace(
        fbs_leq_1500=8.0, fbs_leq_5000=12.0, fbs_gt_5000=18.0,
        fbo_leq_1500=7.0, fbo_leq_5000=11.0, fbo_gt_5000=16.0,
        source="what_to_sell", updated_at=None,
    )
    row = get_category_commission(123, session=_FakeSession(row_obj))
    assert row["updated_at"] is None


# ═══════════ BL-24 Phase 3-7：upsert 随用续期 ═══════════

class _CaptureSession:
    """捕获 execute 收到的语句（不触网）；commit/close 静默。"""

    def __init__(self):
        self.stmt = None

    def execute(self, stmt):
        self.stmt = stmt

    def commit(self):
        pass

    def close(self):
        pass


def test_upsert_refreshes_updated_at():
    """upsert DO UPDATE 分支必须刷新 updated_at（func.now()）——超龄行随使用自然续期。

    语句级断言（mock session，恒跑不触网）：values() 不设 updated_at（INSERT 列
    清单无它），编译产物出现 updated_at 只能来自 ON CONFLICT DO UPDATE SET 刷新。
    """
    from sqlalchemy.dialects import postgresql

    from utils import commission_resolver as cr

    session = _CaptureSession()
    cr.upsert_category_commission(123, "what_to_sell", session=session, fbs_leq_1500=8.0)
    assert session.stmt is not None, "upsert 必须向 session 提交 insert 语句"
    compiled = str(session.stmt.compile(dialect=postgresql.dialect())).lower()
    assert "on conflict" in compiled and "do update set" in compiled
    assert "updated_at" in compiled, "DO UPDATE SET 必须刷新 updated_at（超龄行随用续期）"
    assert "now()" in compiled, "updated_at 刷新须为 DB 侧 now()（与 server_default 写入风格一致）"


def test_upsert_refreshes_stale_row_live_pg():
    """真实 PG：200d 超龄行经 what_to_sell 分段 upsert 续期 → get 返回当前 epoch。

    新鲜度闸（180d 视同未命中）随 upsert 自然解除。直连探测 skip 守卫
    （不读 env 判存——PGDATABASE_URL 可能被注入容器风格 URL，直连 5433 才作数）。
    """
    import sqlalchemy
    from sqlalchemy import text

    url = os.environ.get(
        "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
    )
    try:
        probe = sqlalchemy.create_engine(url)
        with probe.connect():
            pass
        probe.dispose()
    except Exception:
        import pytest

        pytest.skip("本地 PG 不可达")

    from sqlalchemy.orm import Session

    from utils.commission_resolver import get_category_commission, upsert_category_commission

    dc = 990075001  # 测试专用类目 ID，避免撞真实缓存数据
    engine = sqlalchemy.create_engine(url)
    # 直连探测作 skip 守卫（同一 engine 建连失败 → 本地 PG 不可达）
    try:
        with engine.connect():
            pass
    except Exception:
        engine.dispose()
        import pytest

        pytest.skip("本地 PG 不可达")
    # 显式注入 session：upsert/get 内部的惰性 get_session() 读 PGDATABASE_URL，
    # 纯测试批（env 未导出）会 ValueError——测试全程不依赖进程 env。
    try:
        with Session(engine) as session:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM category_commission WHERE description_category_id = :dc"),
                    {"dc": dc},
                )
                conn.execute(
                    text(
                        "INSERT INTO category_commission "
                        "(description_category_id, fbs_leq_1500, source, updated_at) "
                        "VALUES (:dc, 8.0, 'what_to_sell', now() - interval '200 days')"
                    ),
                    {"dc": dc},
                )
            # 种好的行先确认确实超龄（updated_at ≈ 200d 前）
            row_before = get_category_commission(dc, session=session)
            assert row_before is not None and row_before["updated_at"] is not None
            assert time.time() - row_before["updated_at"] > 180 * 86400, "前置：种入行应为超龄行"

            # 随用续期：upsert 刷新 updated_at
            upsert_category_commission(dc, "what_to_sell", session=session, fbs_leq_1500=8.0)
            row = get_category_commission(dc, session=session)
            assert row is not None and row["updated_at"] is not None
            assert time.time() - row["updated_at"] < 60, (
                f"upsert 后 updated_at 应为当前时间（实际 epoch {row['updated_at']}）"
            )
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM category_commission WHERE description_category_id = :dc"),
                {"dc": dc},
            )
        engine.dispose()


# ═══════════ pricing_node：stale marks ═══════════

def _make_state(extensions=None, dc_id="17028830"):
    return SimpleNamespace(
        draft={
            "cost_cny": 5.5, "purchase_cost": 5.5, "weight": 227,
            "dimensions": {"length": 120, "width": 80, "height": 60},
        },
        extensions=extensions or {},
        supabase_url="http://supabase.local", supabase_key="k",
        currency_code="RUB", ozon_client_id="1", ozon_api_key="k",
        description_category_id=dc_id, task_id="", tenant_id="", token="sk-test",
    )


class _DummyRuntime:
    class _DummyContext:
        pass
    context = _DummyContext()


def _call_pricing(monkeypatch, state, cat_commission):
    from utils import logistics_quote
    import graphs.nodes.pricing_node as pn

    monkeypatch.setattr(logistics_quote, "query_logistics_cost",
                        lambda *a, **k: (10.0, "mock_channel", {}))
    monkeypatch.setattr(logistics_quote, "get_store_logistics_config",
                        lambda *a, **k: ("RETS", "Standard"))
    monkeypatch.setattr(pn, "_get_exchange_rate", lambda *a, **k: 12.0)
    monkeypatch.setattr(pn, "get_category_commission", lambda *a, **k: cat_commission,
                        raising=False)
    return pn.pricing_node(state, None, _DummyRuntime())


def test_pricing_node_stale_mark_recorded(monkeypatch):
    """超龄缓存行 → 佣金降级 0.10 且 pricing_info marks 带 commission_source="stale_fallback"。"""
    stale_row = {
        "fbs_leq_1500": 18.0, "fbs_leq_5000": 25.0, "fbs_gt_5000": 30.0,
        "source": "what_to_sell", "updated_at": time.time() - 200 * 86400,
    }
    out = _call_pricing(monkeypatch, _make_state(extensions={"margin_rate": 0.25}), stale_row)
    assert out.error_message == "", f"不应失败: {out.error_message}"
    assert out.pricing_info["commission_rate"] == 0.10, "超龄行必须降级 fallback 0.10"
    assert out.pricing_info["commission_source"] == "stale_fallback"


def test_pricing_node_fresh_no_mark(monkeypatch):
    """新鲜缓存行 → 照常采信，pricing_info 不带 commission_source 键（零行为噪音）。"""
    fresh_row = {
        "fbs_leq_1500": 18.0, "fbs_leq_5000": 25.0, "fbs_gt_5000": 30.0,
        "source": "what_to_sell", "updated_at": time.time(),
    }
    out = _call_pricing(monkeypatch, _make_state(extensions={"margin_rate": 0.25}), fresh_row)
    assert out.pricing_info["commission_rate"] == 0.18
    assert "commission_source" not in out.pricing_info


def test_pricing_node_missing_updated_at_row_marked_stale(monkeypatch):
    """无 updated_at 的缓存行（mock/历史行）→ 同样降级并留 stale_fallback mark。"""
    row = {"fbs_leq_1500": 18.0, "fbs_leq_5000": 25.0, "fbs_gt_5000": 30.0,
           "source": "what_to_sell"}
    out = _call_pricing(monkeypatch, _make_state(extensions={"margin_rate": 0.25}), row)
    assert out.pricing_info["commission_rate"] == 0.10
    assert out.pricing_info["commission_source"] == "stale_fallback"
