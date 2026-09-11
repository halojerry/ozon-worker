"""生产库 marker 闸（2026-09-11 I/O 雪崩事故防线，docs/audit/2026-09-11-io-avalanche.md）。

背景：v0.74 全量测试套件曾在生产服务器上直连生产 PG 跑了 18 小时（生产 compose
把 PG 发布在 127.0.0.1:5433，与本地开发惯例端口 localhost:5433 撞车，conftest
默认 URL 在服务器上等价直连生产库），是磁盘 I/O 雪崩 9 小时不可用的直接触发源。
AGENTS.md「禁止打生产」是纪律，无技术强制力——本模块提供 URL 无关的硬闸：

- deploy.sh / cos-update.sh 部署成功后幂等写入 ``prod_marker`` 表（单行哨兵）。
  marker 是普通表，随 pg_dump 备份走、恢复后仍在（恢复到全新集群需重写，见
  docs/RESTORE-RUNBOOK.md）。
- worker/tests/conftest.py 在 collection 前调用 :func:`enforce_not_production`：
  探测到 marker → 醒目报错 + SystemExit(2)，整个测试会话拒绝启动。

探测语义（保守方向）：
- 表不存在 / 表为空 / 查询失败 / 连接失败 → 不是生产库（False）。
  本地/CI 库天然无 marker；连接失败时后续用例自然报错，无需本闸兜底。

CLI：``python scripts/prod_db_guard.py`` —— 部署脚本/人工排障可独立执行。
"""
from __future__ import annotations

import os
import sys

MARKER_TABLE = "prod_marker"

_DEFAULT_URL = "postgresql://postgres:localdev123@localhost:5433/ozon"

MARKER_DDL = (
    f"CREATE TABLE IF NOT EXISTS {MARKER_TABLE} ("
    "id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1), "
    "marked_at timestamptz NOT NULL DEFAULT now()); "
    f"INSERT INTO {MARKER_TABLE} (id) VALUES (1) ON CONFLICT (id) DO NOTHING;"
)

_BANNER = f"""
════════════════════════════════════════════════════════════════════
⛔ 拒绝在【生产库】上运行测试（prod_marker 哨兵命中）

  目标库带 prod_marker 表 = deploy.sh/cos-update.sh 标记的生产环境。
  在生产库跑 pytest 会反复读写大缓存表并与真实流量竞争——2026-09-10
  测试套件直连生产库 18 小时，直接触发磁盘 I/O 雪崩、服务 9 小时不可用
  （docs/audit/2026-09-11-io-avalanche.md）。

  正确做法（AGENTS.md）：本地 Docker PG（localhost:5433）或 CI 隔离库，
  服务器直连口是 15433 不是 5433。确需在生产库执行 SQL 时用 psql，
  不要跑 pytest。
════════════════════════════════════════════════════════════════════
"""


def is_production_db(url: str | None = None) -> bool:
    """探测目标库是否带 prod_marker 哨兵（缺表/连接失败一律 False）。"""
    url = url or os.environ.get("PGDATABASE_URL", _DEFAULT_URL)
    try:
        from sqlalchemy import create_engine, text

        eng = create_engine(url, connect_args={"connect_timeout": 3})
        try:
            with eng.connect() as conn:
                exists = conn.execute(
                    text(f"SELECT to_regclass('public.{MARKER_TABLE}') IS NOT NULL")
                ).scalar()
                if not exists:
                    return False
                rows = conn.execute(
                    text(f"SELECT COUNT(*) FROM {MARKER_TABLE}")
                ).scalar() or 0
                return rows > 0
        finally:
            eng.dispose()
    except Exception:
        return False
    return False


def enforce_not_production(url: str | None = None) -> None:
    """命中生产 marker 时终止进程（exit 2）。conftest 在 collection 前调用。"""
    if is_production_db(url):
        sys.stderr.write(_BANNER)
        raise SystemExit(2)


if __name__ == "__main__":
    enforce_not_production()
    print(f"prod_db_guard: 目标库无 {MARKER_TABLE} 哨兵，放行")
