"""BL-10 (repo-gov): MXOU 调用台账对账 CLI（纯只读）。

用法（worker 环境，PYTHONPATH=src）：
    python scripts/reconcile_mxou.py                       # 本地台账汇总
    python scripts/reconcile_mxou.py --days 14             # 近 14 天按日汇总
    python scripts/reconcile_mxou.py --token sk-xxxx       # 附平台余额快照对照

三件事：
1. 汇总本地 mxou_call_ledger（按 token_fp / 按日，均带 LIMIT 保护）；
2. 可选 --token <mxou key>：调 utils/mxou_api.get_mxou_balance 拿平台余额快照，
   与本地调用计数对照打印；
3. 差异口径如实标注：MXOU 平台侧无对账/用量查询 API，只能做余额差分
   （两次运行之间：本地新增调用次数 vs 平台余额下降量，粗对非精对）。

纯只读：不写任何表、不调任何写接口、不改任何状态。
表/服务由并行改动提供（services.mxou_ledger_service + create_all）——本脚本
对表不存在、时间列缺失均优雅降级（打印说明，非致命）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(
    os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
))

LEDGER_TABLE = "mxou_call_ledger"

# 台账时间列候选（按优先级；列名/类型运行时经 information_schema 内省确认，
# 不硬编码假设 epoch 还是 timestamp）
_TS_CANDIDATES = ("created_at", "finished_at", "recorded_at", "called_at", "ts")
_EPOCH_TYPES = {"integer", "bigint", "smallint", "double precision", "real", "numeric"}

# 汇总 SQL（模块级常量，便于测试断言；LIMIT 恒在，防大表全量吐出）
SQL_BY_TOKEN = (
    f"SELECT token_fp, COUNT(*) AS calls FROM {LEDGER_TABLE} "
    "GROUP BY token_fp ORDER BY calls DESC LIMIT :limit"
)


def resolve_ts_column(conn):
    """内省台账表的时间列。

    返回 (column_name, is_epoch)；表不存在或找不到时间列返回 None。
    """
    from sqlalchemy import text

    try:
        rows = conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = :t"
        ), {"t": LEDGER_TABLE}).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    info = {str(r[0]): str(r[1]).lower() for r in rows}
    for cand in _TS_CANDIDATES:
        if cand in info:
            return cand, info[cand] in _EPOCH_TYPES
    return None


def _ts_expr(col: str, is_epoch: bool) -> str:
    # 列名来自本模块白名单候选，再校验一层标识符字符，防注入式拼接
    if not col or not col.replace("_", "").isalnum():
        raise ValueError(f"非法列名: {col!r}")
    return f"to_timestamp({col})" if is_epoch else col


def summarize_by_token(conn, limit: int = 20):
    """按 token_fp 汇总调用次数（TOP N）。"""
    from sqlalchemy import text

    return conn.execute(text(SQL_BY_TOKEN), {"limit": limit}).fetchall()


def summarize_by_day(conn, days: int = 7, limit: int = 31):
    """按日汇总调用次数（近 N 天）。

    时间列类型自适应：epoch 数值列用 to_timestamp，timestamp 列直用。
    返回行列表；找不到时间列返回 None（调用方如实打印）。
    """
    from sqlalchemy import text

    resolved = resolve_ts_column(conn)
    if resolved is None:
        return None
    col, is_epoch = resolved
    expr = _ts_expr(col, is_epoch)
    sql = (
        f"SELECT CAST(({expr}) AS date) AS day, COUNT(*) AS calls FROM {LEDGER_TABLE} "
        f"WHERE {expr} >= now() - (:days * interval '1 day') "
        "GROUP BY day ORDER BY day DESC LIMIT :limit"
    )
    return conn.execute(text(sql), {"days": days, "limit": limit}).fetchall()


def count_by_fingerprint(conn, token_fp: str) -> int:
    """指定 token 指纹的本地累计调用次数。"""
    from sqlalchemy import text

    rows = conn.execute(text(
        f"SELECT COUNT(*) FROM {LEDGER_TABLE} WHERE token_fp = :fp"
    ), {"fp": token_fp}).fetchall()
    return int(rows[0][0]) if rows else 0


def token_fingerprint(token: str) -> str:
    """与 mxou_api._token_fingerprint 同源；utils 不可用时 sha256[:8] 兜底。"""
    try:
        from utils.mxou_api import _token_fingerprint
        return _token_fingerprint(token)
    except Exception:
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:8]


def platform_balance(token: str) -> float | None:
    """平台余额快照（只读查询）；查询失败返回 None 并由调用方如实标注。"""
    try:
        from utils.mxou_api import get_mxou_balance
        return get_mxou_balance(token)
    except Exception:
        return None


def format_token_rows(rows) -> str:
    out = ["按 token_fp（TOP）:", "  token_fp       次数"]
    if not rows:
        out.append("  （空）")
        return "\n".join(out)
    for r in rows:
        out.append(f"  {str(r[0])[:16]:<16} {r[1]}")
    return "\n".join(out)


def format_day_rows(rows) -> str:
    out = ["按日（近 N 天）:", "  日期           次数"]
    if rows is None:
        out.append("  （表存在但找不到时间列，跳过按日汇总）")
        return "\n".join(out)
    if not rows:
        out.append("  （空）")
        return "\n".join(out)
    for r in rows:
        out.append(f"  {r[0]}  {r[1]}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MXOU 调用台账对账（纯只读）：本地 mxou_call_ledger 汇总 + 平台余额差分对照",
    )
    parser.add_argument("--limit", type=int, default=20,
                        help="按 token_fp 汇总的 TOP 行数上限（默认 20）")
    parser.add_argument("--days", type=int, default=7,
                        help="按日汇总回看天数（默认 7）")
    parser.add_argument("--token", default=None,
                        help="mxou API key（可选）：附平台余额快照与本地计数对照；不落盘不打印原文")
    args = parser.parse_args()

    print("=== mxou_call_ledger 本地对账汇总 ===")
    try:
        from storage.database.db import get_engine  # type: ignore
    except Exception as e:
        print(f"⚠️ 依赖加载失败（需在 worker 环境运行，PYTHONPATH=src）: {e}")
        return 1
    try:
        engine = get_engine()
    except Exception as e:
        print(f"⚠️ PG 连接失败: {e}")
        print("本地 Docker: cd deploy && docker compose up -d postgres （端口 5433）")
        return 1

    from sqlalchemy import text

    with engine.connect() as conn:
        exists = conn.execute(text(
            "SELECT to_regclass(:t) IS NOT NULL"
        ), {"t": LEDGER_TABLE}).scalar()
        if not exists:
            print(f"⚠️ 表 {LEDGER_TABLE} 不存在（worker 未升级或 BL-10 埋点未部署）——仅平台侧对照可继续")
        else:
            print(format_token_rows(summarize_by_token(conn, limit=args.limit)))
            print(format_day_rows(summarize_by_day(conn, days=args.days)))

    if args.token:
        print()
        print("=== 平台侧对照 ===")
        fp = token_fingerprint(args.token)
        print(f"token_fp: {fp}")
        bal = platform_balance(args.token)
        if bal is None:
            print("余额快照: 获取失败（网络/凭证问题，如实标注不编造）")
        else:
            print(f"余额快照: {bal}")
        if exists:
            try:
                with engine.connect() as conn:
                    print(f"本地累计调用（该 fp）: {count_by_fingerprint(conn, fp)}")
            except Exception:
                print("本地累计调用（该 fp）: 查询失败")
        print("说明: 平台侧无对账 API，仅余额差分——两次运行间「本地新增次数」对"
              "「平台余额下降量」做粗对；MXOU 按调用计费口径可能与本地台账存在正常偏差"
              "（5xx 重试是否计费以平台为准）。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
