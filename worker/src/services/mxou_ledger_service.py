"""MXOU 调用台账（BL-10，repo-gov B2-β）——LLM 网关调用审计（fire-and-forget）。

record_call 是**唯一**写入口，接口契约已锁定（BL-08 接线方按此调用）：
    record_call(*, tenant_id: str | None, token_fp: str, endpoint: str,
                model: str | None = None) -> int | None

v0.77.2：补 model 列（观测修复）——调用方从 endpoint 'image_gen:<model>' 拆分或
直接传请求参数 model；旧行 NULL 不回填。库内**不写 cost 金额**（真钱数只有网关侧有）。

批D（v0.78，取证 I5）：成败观测——
- record_call 返回插入行 id（供 mxou_api 在 POST 返回/异常处 finish_call 回写）；
  新行 outcome 以 'pending' 起步；台账失败返回 None（回写侧跳过）；
- finish_call(ledger_id, outcome=..., duration_ms=...)：UPDATE 回写终态
  （'ok' / 'failed' / 'config_error' 白名单），行不存在/列未迁移/DB 异常 →
  吞掉 + debug 日志（观测面宁缺毋滞，绝不影响业务）。

关键约束（红线）：**绝不影响业务调用路径**——
1. 每次调用开独立短事务（get_engine().begin()）插入一行，不复用业务 session
   （防业务事务被台账写失败连带回滚）；
2. 整体吞错（logger.warning）——台账写失败只损失一条观测数据，绝不抛回调用方；
3. 不做重试、不做队列——观测面 宁缺毋滞。

表结构见 storage/database/shared/model.py 的 MxouCallLedger（append-only，
outcome/duration_ms 为批D v0.78 加列——行内容不删改，仅回写终态）。
token_fp 传指纹（如 sha256(token) hex 前 64），**绝不明文 key**。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

_ENDPOINT_MAX = 200
_TOKEN_FP_MAX = 64
_MODEL_MAX = 80

# 批D v0.78: outcome 白名单（回写终态；'pending' 仅由 INSERT 默认写入）
_OUTCOMES = ("ok", "failed", "config_error")


def record_call(
    *,
    tenant_id: str | None,
    token_fp: str,
    endpoint: str,
    model: str | None = None,
) -> int | None:
    """记一次 MXOU 调用（fire-and-forget，同步短插入，整体吞错）。

    Args:
        tenant_id: 租户（user_id）；匿名/解析失败场景传 None（列可空）
        token_fp: token 指纹（sha256 hex，绝不明文 key；写入侧截 64）
        endpoint: 网关端点路径（写入侧截 200）
        model: 模型 id（v0.77.2，可空；写入侧截 80）——按模型对账费用用

    Returns:
        插入行 id（批D v0.78，供 finish_call 回写成败）；台账失败返回 None。
    """
    try:
        with get_engine().begin() as conn:
            result = conn.execute(
                text(
                    "INSERT INTO mxou_call_ledger "
                    "(called_at, tenant_id, token_fp, endpoint, model, outcome) "
                    "VALUES (:ts, :tenant_id, :token_fp, :endpoint, :model, :outcome) "
                    "RETURNING id"
                ),
                {
                    "ts": time.time(),
                    "tenant_id": tenant_id,
                    "token_fp": (token_fp or "")[:_TOKEN_FP_MAX],
                    "endpoint": (endpoint or "")[:_ENDPOINT_MAX],
                    "model": (str(model)[:_MODEL_MAX] if model else None),
                    "outcome": "pending",
                },
            )
            return result.scalar()
    except Exception as exc:
        logger.warning(
            "mxou 台账写入失败（不阻断业务）endpoint=%s: %s",
            (endpoint or "")[:80], str(exc)[:200],
        )
        return None


def finish_call(
    ledger_id: int | None,
    *,
    outcome: str,
    duration_ms: int | None = None,
) -> None:
    """批D v0.78: 回写一次调用的终态（outcome/duration_ms）——fire-and-forget。

    - 仅允许 pending → 终态单向回写（WHERE outcome='pending'，防重复写覆盖）；
    - outcome 白名单外（含未知值）直接跳过（不写库）；
    - 容错：ledger_id 为空 / 行不存在（rowcount=0）/ 列未迁移 / DB 异常 →
      吞掉 + debug 日志——观测面绝不影响业务调用路径（红线）。
    """
    if not ledger_id:
        return
    if outcome not in _OUTCOMES:
        logger.debug("mxou 台账回写跳过（非法 outcome=%s）ledger_id=%s", outcome, ledger_id)
        return
    try:
        with get_engine().begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE mxou_call_ledger "
                    "SET outcome = :outcome, duration_ms = :duration_ms "
                    "WHERE id = :id AND outcome = 'pending'"
                ),
                {
                    "outcome": outcome,
                    "duration_ms": int(duration_ms) if duration_ms is not None else None,
                    "id": ledger_id,
                },
            )
            if result.rowcount == 0:
                # 行不存在 / 已回写过（幂等保护）——debug 留痕即可
                logger.debug(
                    "mxou 台账回写无行更新（不存在或已回写）ledger_id=%s", ledger_id,
                )
    except Exception as exc:
        # 含列未迁移（升级前 worker 先起）场景——吞掉，观测面宁缺毋滞
        logger.debug(
            "mxou 台账回写失败（不阻断业务）ledger_id=%s: %s",
            ledger_id, str(exc)[:200],
        )
