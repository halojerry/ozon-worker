#!/usr/bin/env python3
"""race-L3（Task 28）：template set_default 单事务化——并发切默认不再 500。

取证（docs/PLAN-security-remediation-v1.md race-L3）：set_default 原实现三段
独立事务（get_template 读 → _clear_default 清旧 → begin 置新），两段之间
存在窗口：并发切换时后提交的 UPDATE is_default=true 撞部分唯一索引
uq_listing_templates_default → IntegrityError（UniqueViolation）原样冒泡
→ 客户端拿到 500。审计探针实证并发 20 次 10 失败（5 种异常形态）。

修复契约（本文件锁定）：
  1. 清旧 + 置新合并进单个 get_engine().begin() 事务；
  2. 残余并发窗口（双 set 同事务竞态）两类 DB 伪异常同转 HTTPException 409
     （服务层既有错误形态，与 create/update 同文案）：IntegrityError
     （UniqueViolation，冲突方已提交）与 OperationalError 40P01
     DeadlockDetected（并发唯一索引插入等待环，探针实证）；
  3. 成功形态/函数签名不变（消费方零感知，错误路径 500→409）。

断言（复用审计探针形态分类）：
  - 并发 20 次 set_default（4 模板 id 交替）：结果形态只允许
    「成功 dict」或「HTTPException 409」，其余任何异常形态 = 500 缺陷；
  - 终态恒恰 1 个 is_default=true；
  - 单线程切换语义回归：旧默认被清、新默认生效。

运行（需本地 PG 5433，不可达自动 skip）：
    cd worker && PGDATABASE_URL=... PYTHONPATH=src ../skill/.venv314/bin/python \
        -m pytest tests/test_template_default_atomic_v076.py -q
"""
from __future__ import annotations

import os
import sys
import threading
from collections import Counter
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import template_service

DB_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)

# 探针数据：probe_ 前缀租户，fixture 双向清理
TENANT = "probe_t28_default_race"
N_THREADS = 20
N_TEMPLATES = 4


def _pg_available() -> bool:
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="本地 PG 不可达")


def _wipe() -> None:
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM listing_templates WHERE tenant_id = :t"), {"t": TENANT}
        )
    eng.dispose()


@pytest.fixture(autouse=True)
def _probe_cleanup():
    _wipe()
    yield
    _wipe()


def _mk_templates() -> list[str]:
    """建 N_TEMPLATES 个非默认模板，返回 id 列表。"""
    ids: list[str] = []
    for i in range(N_TEMPLATES):
        tpl = template_service.create_template(
            TENANT, {"name": f"probe-t28-tpl-{i}", "config": {"margin_rate": 0.3}}
        )
        ids.append(tpl["id"])
    return ids


def _default_id() -> str | None:
    got = template_service.get_default_template(TENANT)
    return got["id"] if got else None


def _count_defaults() -> int:
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        n = conn.execute(
            text(
                "SELECT COUNT(*) FROM listing_templates "
                "WHERE tenant_id = :t AND is_default"
            ),
            {"t": TENANT},
        ).scalar()
    eng.dispose()
    return int(n)


# ============================================================
# 1. 单线程回归：切换语义（旧默认清、新默认生效）
# ============================================================

def test_set_default_switch_semantics():
    ids = _mk_templates()
    assert _default_id() is None
    template_service.set_default(TENANT, ids[0])
    assert _default_id() == ids[0]
    # 切换：旧默认被清、新默认生效，且返回值真实反映新默认
    switched = template_service.set_default(TENANT, ids[1])
    assert switched["id"] == ids[1]
    assert switched["is_default"] is True
    assert _default_id() == ids[1]
    assert template_service.get_template(TENANT, ids[0])["is_default"] is False
    assert _count_defaults() == 1


# ============================================================
# 2. 并发 20 次 set_default：恒 1 个 default、零 500 形态
# ============================================================

def test_set_default_concurrent_20_only_ok_or_409_exactly_one_default():
    """审计探针形态分类复刻：20 并发交替切 4 个模板 id。

    形态只允许两种：ok（成功 dict）/ 409（并发残余窗口被服务层转译）。
    其余任何形态（IntegrityError/DBAPIError/OperationalError…）即 500 缺陷。
    """
    ids = _mk_templates()
    template_service.set_default(TENANT, ids[0])  # 预置一个默认（真实切换场景）
    barrier = threading.Barrier(N_THREADS)
    outcomes: list[tuple[str, object]] = [None] * N_THREADS  # type: ignore[list-item]

    def _worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            res = template_service.set_default(TENANT, ids[i % N_TEMPLATES])
            outcomes[i] = ("ok", res)
        except HTTPException as exc:
            outcomes[i] = (f"http_{exc.status_code}", exc.detail)
        except Exception as exc:  # 500 形态：任何非 HTTPException 冒泡都是缺陷
            outcomes[i] = (f"unexpected_{type(exc).__name__}", repr(exc))

    threads = [
        threading.Thread(target=_worker, args=(i,), name=f"probe-t28-{i}")
        for i in range(N_THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "并发探针线程超时未结束"

    shapes = Counter(shape for shape, _ in outcomes)
    print(f"[probe-t28] 20 并发 set_default 形态分布: {dict(shapes)}")
    # 取证：500 形态的原始异常全文打出（pgcode 级定位，CI 红时日志自解释）
    for shape, detail in outcomes:
        if shape not in ("ok", "http_409"):
            print(f"[probe-t28] 500 形态明细 {shape}: {detail}")

    # 500 形态必须为零——IntegrityError 必须被转成约定的 409 形态
    unexpected = {s: v for s, v in shapes.items() if s not in ("ok", "http_409")}
    assert not unexpected, f"出现 500 形态异常（race-L3 未修复）: {unexpected}"
    # 409 之外的 HTTP 状态码也不允许（服务层约定只有 409）
    bad_http = {s: v for s, v in shapes.items() if s.startswith("http_") and s != "http_409"}
    assert not bad_http, f"出现约定外的 HTTP 状态: {bad_http}"
    # 至少一路成功（探针非全拒场景）
    assert shapes.get("ok", 0) >= 1, f"20 并发全部失败不合理: {dict(shapes)}"

    # 终态不变式：恒恰 1 个默认
    assert _count_defaults() == 1, "并发切换后默认模板数必须恰为 1"
    final = template_service.get_default_template(TENANT)
    assert final is not None and final["id"] in ids
