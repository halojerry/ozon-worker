"""MXOU 调用台账（BL-10，repo-gov B2-β）——LLM 网关调用审计（fire-and-forget）。

record_call 是**唯一**写入口，接口契约已锁定（BL-08 接线方按此调用）：
    record_call(*, tenant_id: str | None, token_fp: str, endpoint: str) -> None

关键约束（红线）：**绝不影响业务调用路径**——
1. 每次调用开独立短事务（get_engine().begin()）插入一行，不复用业务 session
   （防业务事务被台账写失败连带回滚）；
2. 整体吞错（logger.warning）——台账写失败只损失一条观测数据，绝不抛回调用方；
3. 不做重试、不做队列——观测面 宁缺毋滞。

表结构见 storage/database/shared/model.py 的 MxouCallLedger（append-only）。
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


def record_call(*, tenant_id: str | None, token_fp: str, endpoint: str) -> None:
    """记一次 MXOU 调用（fire-and-forget，同步短插入，整体吞错）。

    Args:
        tenant_id: 租户（user_id）；匿名/解析失败场景传 None（列可空）
        token_fp: token 指纹（sha256 hex，绝不明文 key；写入侧截 64）
        endpoint: 网关端点路径（写入侧截 200）
    """
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mxou_call_ledger (called_at, tenant_id, token_fp, endpoint) "
                    "VALUES (:ts, :tenant_id, :token_fp, :endpoint)"
                ),
                {
                    "ts": time.time(),
                    "tenant_id": tenant_id,
                    "token_fp": (token_fp or "")[:_TOKEN_FP_MAX],
                    "endpoint": (endpoint or "")[:_ENDPOINT_MAX],
                },
            )
    except Exception as exc:
        logger.warning(
            "mxou 台账写入失败（不阻断业务）endpoint=%s: %s",
            (endpoint or "")[:80], str(exc)[:200],
        )
