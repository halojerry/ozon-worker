"""店铺币种解析工具（v0.83 gate B2）——本地凭证行币种读取唯一入口。

背景（gate v083 #4 实锤）：graph 信封不带 currency_code 时，定价链回源 Ozon
``/v1/seller/info`` 查询店铺币种；查询超时/失败（宿主与容器均现
``SSL: UNEXPECTED_EOF_WHILE_READING``）时**静默回落默认 RUB** → CNY 合约店
发出 RUB 价 → Ozon 拒 ``currency_differs_from_contract``。

本模块只提供**纯本地读**（credentials.currency，按 ozon_client_id + tenant
取 active 行）——不触网、不咨询 Ozon、无超时可能。信任序编排在 pricing_node：
``extensions.currency_code`` → 本地凭证行 → Ozon API（全败 → 显式
``CURRENCY_UNRESOLVED``，绝不静默 RUB）。

⚠️ 币种有效性白名单与 auth_node ``query_ozon_seller_info`` 保持一致
（CNY/RUB/USD/EUR/KZT）——未知值视同未解析，降级下一级而非当权威币种用。
"""
from __future__ import annotations

from typing import Any, Optional

# 与 auth_node.query_ozon_seller_info 的合法币种集合逐字一致（勿单边扩展）
VALID_CURRENCIES = frozenset({"CNY", "RUB", "USD", "EUR", "KZT"})


def normalize_currency(value: Any) -> str:
    """币种归一：strip + upper + 白名单校验。非 str / 未知值 → ""（视同未解析）。"""
    if not isinstance(value, str):
        return ""
    cc = value.strip().upper()
    return cc if cc in VALID_CURRENCIES else ""


def resolve_credential_currency(
    tenant_id: str,
    ozon_client_id: str,
    session: Optional[Any] = None,
) -> str:
    """纯本地读 credentials.currency（按 tenant + ozon_client_id 取 active 行）。

    - 不触网、不咨询 Ozon → 无超时可能（gate B2 的核心诉求）。
    - tenant_id / ozon_client_id 任一为空 → ""（跳过，不盲查）。
    - 无行 / 读取异常 → ""（调用方降级下一级，绝不在此 raise）；但异常记 WARNING
      便于区分「确实没配凭证」与「DB 读故障」。
    - session 可注入（测试用 mock，不连真实 PG）；默认从 storage.database.db 惰性取。

    Returns: 归一后的合法币种（CNY/RUB/USD/EUR/KZT）或 ""。
    """
    if not tenant_id or not ozon_client_id:
        return ""
    own_session = session is None
    if own_session:
        from storage.database.db import get_session

        session = get_session()
    try:
        from sqlalchemy import text

        row = session.execute(
            text(
                "SELECT currency FROM credentials "
                "WHERE tenant_id=:tenant_id AND ozon_client_id=:client_id "
                "AND status='active' ORDER BY created_at DESC LIMIT 1"
            ),
            {"tenant_id": str(tenant_id), "client_id": str(ozon_client_id)},
        ).fetchone()
        if row is None:
            return ""
        return normalize_currency(row[0])
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "本地凭证币种读取失败（降级下一级）", exc_info=True,
        )
        return ""
    finally:
        if own_session and session is not None:
            session.close()
