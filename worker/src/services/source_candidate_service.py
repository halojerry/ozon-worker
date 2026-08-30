"""PRD M3/M5b: 货源匹配主数据 source_candidates。

product_id(Ozon 商品) ↔ 1688 offer/url 匹配记录,含 match_score /
match_method(aibuy|ak|cdp|text|image|keyword|manual|discovery)/ status(valid|expired)。

来源:
- skill 跟卖/图搜上报(POST /api/v1/source-candidates,带 credential_id 或 client_id);
- discover 选品回调派生(_handle_discovery_run_report 内调用,无店绑定时
  credential_id = 全零占位,「未匹配货源」工作台后续手动补店);
- 自营上架成功回填(learning_record_node 调 upsert_from_envelope 时顺带写)。

约束:唯一键 (tenant_id, credential_id, product_id, source_offer_id),重复上报
按 source_offer_id 更新(新匹配覆盖旧匹配信息,保留 valid 状态)。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# discover 候选无店绑定时用全零占位 credential(webui 工作台可展示,绑定后补店)
NO_STORE_CREDENTIAL = "00000000-0000-0000-0000-000000000000"

VALID_METHODS = {
    "aibuy", "ak", "cdp", "text", "image", "keyword", "manual", "discovery", "envelope",
}


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _offer_id_from_url(url: str) -> str:
    """从 1688 detail URL 提取 offer_id(detail.1688.com/offer/123.html)。"""
    m = re.search(r"/offer/(\d+)", url or "")
    return m.group(1) if m else ""


def upsert_source_candidates(
    tenant_id: str,
    credential_id: str,
    rows: list[dict],
) -> dict:
    """批量 upsert source_candidates(唯一键含 source_offer_id,重复 → 更新)。"""
    inserted = 0
    updated = 0
    if not rows:
        return {"inserted": 0, "updated": 0}
    with get_engine().begin() as conn:
        for row in rows:
            product_id = str(row.get("product_id") or "").strip()
            source_url = str(row.get("source_url") or "").strip()
            if not product_id or not source_url:
                continue
            source_offer_id = str(row.get("source_offer_id") or "").strip()
            if not source_offer_id:
                source_offer_id = _offer_id_from_url(source_url)
            if not source_offer_id:
                source_offer_id = source_url
            method = str(row.get("match_method") or "manual").strip().lower()
            if method not in VALID_METHODS:
                method = "manual"
            status = str(row.get("status") or "valid").strip().lower()
            if status not in ("valid", "expired"):
                status = "valid"
            result = conn.execute(text(
                """
                INSERT INTO source_candidates
                    (tenant_id, credential_id, product_id, source_offer_id, source_url,
                     price_cny, match_score, match_method, status, created_at)
                VALUES (:t, :c, :p, :o, :url, :price, :score, :method, :status, NOW())
                ON CONFLICT (tenant_id, credential_id, product_id, source_offer_id)
                DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    price_cny = EXCLUDED.price_cny,
                    match_score = EXCLUDED.match_score,
                    match_method = EXCLUDED.match_method,
                    status = EXCLUDED.status
                RETURNING (xmax = 0) AS was_inserted
                """
            ), {
                "t": tenant_id, "c": str(credential_id), "p": product_id,
                "o": source_offer_id, "url": source_url,
                "price": _num(row.get("price_cny")), "score": _num(row.get("match_score")),
                "method": method, "status": status,
            })
            if result and result.rowcount:
                was_inserted = bool(result.fetchone()[0])
            else:
                was_inserted = True
            if was_inserted:
                inserted += 1
            else:
                updated += 1
    if inserted or updated:
        logger.info("source_candidates 落库: tenant=%s inserted=%d updated=%d",
                    tenant_id, inserted, updated)
    return {"inserted": inserted, "updated": updated}


def derive_from_discovery_run(tenant_id: str, candidates: list[dict]) -> int:
    """discover 回调派生:候选带 match_1688_url 且状态 ok/matched/profitable → 落 source_candidates。

    无店绑定(discover 阶段未选店)→ credential_id 用全零占位;match_score 取
    profit_margin/blue_ocean 归一化兜底 0.5;match_method=discovery。
    """
    if not candidates:
        return 0
    rows = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        if str(c.get("status") or "") not in ("ok", "matched", "profitable"):
            continue
        source_url = str(c.get("match_1688_url") or "").strip()
        product_id = str(c.get("ozon_product_id") or "").strip()
        if not source_url or not product_id:
            continue
        price = _num(c.get("match_1688_price"))
        score = _num(c.get("profit_margin"))
        if score is None:
            score = _num(c.get("blue_ocean_score"))
        if score is None:
            score = 0.5
        rows.append({
            "product_id": product_id,
            "source_url": source_url,
            "price_cny": price,
            "match_score": score,
            "match_method": "discovery",
        })
    if not rows:
        return 0
    res = upsert_source_candidates(tenant_id, NO_STORE_CREDENTIAL, rows)
    return res["inserted"] + res["updated"]


def list_by_product(
    tenant_id: str,
    credential_id: str,
    product_id: str,
    limit: int = 20,
) -> list[dict]:
    """按商品列出候选(含占位店候选,方便工作台展示)。"""
    if not product_id:
        return []
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            """
            SELECT source_offer_id, source_url, price_cny, match_score, match_method,
                   status, created_at
            FROM source_candidates
            WHERE tenant_id=:t AND product_id=:p
              AND (credential_id::text=:c OR credential_id::text=:noc)
            ORDER BY match_score DESC NULLS LAST, created_at DESC
            LIMIT :lim
            """
        ), {
            "t": tenant_id, "p": product_id, "c": str(credential_id),
            "noc": NO_STORE_CREDENTIAL, "lim": limit,
        }).fetchall()
    return [{
        "source_offer_id": str(r[0]), "source_url": str(r[1]),
        "price_cny": r[2], "match_score": r[3], "match_method": str(r[4]),
        "status": str(r[5]),
        "created_at": r[6].isoformat() if r[6] else None,
    } for r in rows]


def resolve_credential(
    tenant_id: str,
    credential_id: Optional[str] = None,
    client_id: Optional[str] = None,
) -> Optional[str]:
    """上报方给的 credential_id 或 client_id → 校验归属并返回 credential_id。"""
    from services.credential_service import find_credential_id_by_client, get_decrypted

    if credential_id:
        try:
            get_decrypted(tenant_id, str(credential_id))
        except Exception:
            return None
        return str(credential_id)
    if client_id:
        return find_credential_id_by_client(tenant_id, str(client_id))
    return None
