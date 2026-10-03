"""card_audit 域：店铺卡不变量巡检服务（PLAN-card-audit-sweep-v1 §3/§5 落地）。

动因：2026-09-26 上架质量战役审计结论——全部终态守卫钉死在「提交/任务终态时点」，
而 Ozon 的真实损害（评分每日重算、审核数小时后才 declined、错货）几乎都发生在
终态之后。本服务把「人工发现 + 脚本救」变成 store_sync_jobs 的一个日级域：
每店每日一轮，四不变量体检 + 动作分级落 card_audit_finding。

四不变量与动作分级（§3；B 于 v0.83.2 升级为分级处置）：
  A rating_gap       评级 <90 且缺口 ⊆ 保守白名单 → 自动修（build_enrich_update_body
                     全量回显 UPDATE）；构造器保守放弃 → 只落 finding（不重试熔断）
  B declined         ✅ v0.83.2（fix/card-audit-declined-v1，2026-10-02 用户拍板）：
                     declined/validation-fail 卡分级终态处置（唯一决策源
                     utils/declined_disposition.declined_disposition）——
                     a) 可修拒因族 + 标题健康 → 自动修（patch_echo_for_declines
                        定向补丁 + 唯一构造器全量回显 UPDATE；DESCRIPTION_DECLINE
                        经 allow_annotation_replace 重建 4191）；
                     b) 标题残壳 / 资质族（BR_* 等）→ 自动归档（/v1/product/archive，
                        可逆 unarchive；kill-switch CARD_AUDIT_DECLINED_AUTO_ARCHIVE=0
                        降级为只报告 archive_suggested）；
                     c) 无错误码 / 未知混码 → 只报告（保守人工）。
                     旧「只报告」拍板废除依据：半年人工未至，4718259 店 41→67 张
                     declined 零处置累积实证；declined 卡不可售，归档可逆且全程留痕。
  C source_mismatch  现卡名称 vs listing_result_log 源侧事实 LLM 语义比对 →
                     只报告（错货卡唯一系统内检测通道；cap N/日成本闸）
  D price_sanity     old_price 缺失/低于现价/差价不足 → 阈值内自动修
                    （build_price_update_body 单字段口径）；min_price 关系异常
                     等其余 → 只报告
  E profit_reality   v0.83 批⑥ 第 5 不变量：预估利润 vs 当前 /v5 实盘利润
                     （utils/profit_reality 重算）差超双门阈值（PCT+ABS）→ 只报告
                     （价格域绝不改价；最低价门槛 + fallback 佣金设计内差异降为
                     计数不 open，防噪）

纪律红线（违者返工）：
- 自动修只允许经 utils/content_enrich 家族构造器（全量回显防洗卡，A6 纪律），
  绝不裸拼 /v3/product/import POST（B 的回显补丁只修 Ozon 点名问题，其余字节不动）；
- C 绝不自动改卡；B 的归档仅限「标题残壳/资质族」决策命中且开关开启，
  可逆（unarchive）、逐卡 finding 留痕、kill-switch 可整体关闭；
- 全程单卡 try/except 隔离：一张卡异常不拖垮整店轮次；
- 服务零业务逻辑外溢：不碰 mcp_server.py / langgraph 节点。

env 开关：
  CARD_AUDIT_ENABLED       默认 1；=0 时调度域整体跳过（due 查询不产 card_audit 水位）
  CARD_AUDIT_AUTOFIX       默认 1；=0 时 A/D 只报告不修（finding open + detail 标记）
  CARD_AUDIT_LLM_CAP_DAILY C 不变量 LLM 日封顶（默认 50；进程内计数，见 _llm_budget_take）
  CARD_AUDIT_LLM_TOKEN     C 不变量 LLM 比对用的 MXOU key（可选；未配置 → C 整体
                           跳过并在 summary 标 llm_skipped_no_token——后台域没有
                           用户任务 token，需要运维配置专用 key 才启用 C）
  CARD_AUDIT_INTERVAL_MIN  域水位间隔（默认 1440 = 日级）
  PROFIT_REALITY_GAP_PCT   E 不变量相对差异双门之一（默认 0.15）
  PROFIT_REALITY_GAP_ABS_CNY  E 不变量绝对差异双门之一 CNY（默认 20.0）
  PROFIT_REALITY_MIN_PRICE_RUB E 最低价门槛 RUB（默认 300，低价卡噪声大不判）

与 PLAN 的实现偏差（2026-09-26 落地实录）：
- LLM token：PLAN 未指明后台域 token 来源；实现为 env 配置专用 key，未配置则
  C 跳过（其他三不变量不受影响）；
- LLM 日封顶为进程内计数（单容器部署语义），多副本部署需改为 DB 计数——当前
  生产单容器，接受。
"""
from __future__ import annotations

import datetime
import json
import logging
import math
import os
import re
from typing import Any, Optional

from sqlalchemy import text

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# 域水位间隔（store_sync_jobs.due_credentials 域注册表消费）
CARD_AUDIT_INTERVAL_MIN = int(os.getenv("CARD_AUDIT_INTERVAL_MIN", "1440"))

# rating-by-sku 批量上限（实测 validateSKUs too many skus: count=315, limit=100）
_RATING_BATCH = 100
# /v3/product/list、/v3/product/info/list、/v4/product/info/attributes、/v5 批量上限
_LIST_BATCH = 1000
# A 不变量评级阈值（content_enrich.RATING_THRESHOLD 同源；>=90 复检闭环不动作口径）
_RATING_THRESHOLD = 90.0

INVARIANT_RATING_GAP = "rating_gap"
INVARIANT_DECLINED = "declined"
INVARIANT_SOURCE_MISMATCH = "source_mismatch"
INVARIANT_PRICE_SANITY = "price_sanity"
# ✅ v0.83 批⑥ 回执真值化：第 5 不变量——预估利润 vs 实盘利润（当前 /v5 响应重算）
INVARIANT_PROFIT_REALITY = "profit_reality"

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _profit_gap_pct_threshold() -> float:
    """实盘 vs 预估 差异相对阈值（双门之一，默认 0.15）。畸形 → 默认。"""
    try:
        return abs(float(os.getenv("PROFIT_REALITY_GAP_PCT", "0.15")))
    except (TypeError, ValueError):
        return 0.15


def _profit_gap_abs_threshold() -> float:
    """实盘 vs 预估 差异绝对阈值 CNY（双门之一，默认 20.0）。畸形 → 默认。"""
    try:
        return abs(float(os.getenv("PROFIT_REALITY_GAP_ABS_CNY", "20.0")))
    except (TypeError, ValueError):
        return 20.0


def _profit_min_price_rub() -> float:
    """最低价门槛（RUB；默认 300）——低价卡 fx/浮点噪声大，不参与判定。"""
    try:
        return abs(float(os.getenv("PROFIT_REALITY_MIN_PRICE_RUB", "300")))
    except (TypeError, ValueError):
        return 300.0


def card_audit_enabled() -> bool:
    """域总闸（默认开；=0 时 due_credentials 不注册 card_audit 域）。"""
    return os.getenv("CARD_AUDIT_ENABLED", "1").strip() != "0"


def _autofix_enabled() -> bool:
    """A/D 白名单自动修总闸（默认开；=0 只报告）。"""
    return os.getenv("CARD_AUDIT_AUTOFIX", "1").strip() != "0"


def _llm_cap_daily() -> int:
    try:
        return max(0, int(os.getenv("CARD_AUDIT_LLM_CAP_DAILY", "50")))
    except ValueError:
        return 50


def _llm_token() -> str:
    """C 不变量 LLM 比对专用 MXOU key（未配置 → C 跳过，不是错误）。"""
    return os.getenv("CARD_AUDIT_LLM_TOKEN", "").strip()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── LLM 日封顶（进程内计数；单容器部署语义，多副本需改 DB 计数）──────────

_llm_budget = {"date": "", "used": 0}


def _llm_budget_take() -> bool:
    """取一个 LLM 名额；当日额度用尽返回 False。测试可直操作 _llm_budget 重置。"""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    if _llm_budget["date"] != today:
        _llm_budget["date"] = today
        _llm_budget["used"] = 0
    if _llm_budget["used"] >= _llm_cap_daily():
        return False
    _llm_budget["used"] += 1
    return True


def reset_llm_budget() -> None:
    """测试辅助：清零进程内 LLM 计数。"""
    _llm_budget["date"] = ""
    _llm_budget["used"] = 0


# ── finding 幂等落库 ─────────────────────────────────────────────


def _record_finding(tenant_id: str, credential_id: str, ozon_product_id: str,
                    invariant: str, severity: str, detail: Optional[dict],
                    resolved: bool = False) -> None:
    """幂等落 finding（部分唯一索引语义，实测踩平：PG 部分索引冲突仲裁只对
    **满足谓词的新行**生效——status='auto_fixed' 的新行撞不到
    `WHERE status='open'` 的 arbiter，故 resolved 路径必须两步）。

    - open 分支：INSERT … ON CONFLICT (ozon_product_id, invariant)
      WHERE status='open' DO UPDATE——已有 open 行原位更新 severity/detail，
      无则新插（⚠️ 谓词必须与 uq_card_audit_finding_open 逐字一致）；
    - resolved（auto_fixed）分支：先 UPDATE 本店本卡本不变量的 open 行流转
      auto_fixed；零命中（无 open 行）再纯 INSERT 新行。
    - auto_fixed/triaging/fixed/wontfix 行不阻塞：问题复现时 open 分支新开一行
      （PLAN §5.4「已修但复现 → 新行」语义）。
    """
    pid, cid = str(ozon_product_id), str(credential_id)
    detail_json = json.dumps(detail or {}, ensure_ascii=False)
    base = {
        "t": tenant_id, "c": cid, "p": pid, "i": invariant,
        "sev": severity, "detail": detail_json,
    }
    with get_engine().begin() as conn:
        if resolved:
            moved = conn.execute(text(
                """
                UPDATE card_audit_finding SET status='auto_fixed',
                       severity=:sev, detail=CAST(:detail AS jsonb), updated_at=NOW()
                WHERE tenant_id=:t AND credential_id=:c AND ozon_product_id=:p
                  AND invariant=:i AND status='open'
                """
            ), base).rowcount
            if not moved:
                conn.execute(text(
                    """
                    INSERT INTO card_audit_finding
                        (tenant_id, credential_id, ozon_product_id, invariant,
                         severity, detail, status)
                    VALUES (:t, :c, :p, :i, :sev, CAST(:detail AS jsonb), 'auto_fixed')
                    """
                ), base)
        else:
            conn.execute(text(
                """
                INSERT INTO card_audit_finding
                    (tenant_id, credential_id, ozon_product_id, invariant,
                     severity, detail, status)
                VALUES (:t, :c, :p, :i, :sev, CAST(:detail AS jsonb), 'open')
                ON CONFLICT (ozon_product_id, invariant) WHERE status = 'open'
                DO UPDATE SET severity = EXCLUDED.severity,
                              detail = EXCLUDED.detail,
                              updated_at = NOW()
                """
            ), base)


def _count_open_findings(tenant_id: str, credential_id: str) -> int:
    with get_engine().connect() as conn:
        return int(conn.execute(text(
            "SELECT COUNT(*) FROM card_audit_finding "
            "WHERE tenant_id=:t AND credential_id=:c AND status='open'"
        ), {"t": tenant_id, "c": str(credential_id)}).scalar() or 0)


# ── Ozon 拉取（分批；批量口径与 content_rating_sweep 同源）───────────────


def _list_all_products(client_id: str, api_key: str) -> list[dict]:
    """/v3/product/list 分页 → [{product_id, offer_id}]（visibility=ALL，sweep 同款）。"""
    from utils.ozon_client import ozon_post

    out: list[dict] = []
    last_id = ""
    while True:
        resp = ozon_post(
            client_id, api_key, "/v3/product/list",
            {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": _LIST_BATCH},
            timeout=60, language="RU",
        )
        result = (resp or {}).get("result") or {}
        items = result.get("items") or []
        out.extend(
            {"product_id": str(it["product_id"]), "offer_id": str(it.get("offer_id") or "")}
            for it in items if isinstance(it, dict) and it.get("product_id")
        )
        last_id = str(result.get("last_id") or "")
        if len(items) < _LIST_BATCH or not last_id:
            break
    return out


def _fetch_info_map(client_id: str, api_key: str, product_ids: list[str]) -> dict[str, dict]:
    """/v3/product/info/list 批量 → {product_id: info}（statuses.moderate_status/vat/
    images360/is_archived；一次请求只传一组标识符——product_id 数组）。"""
    from utils.ozon_client import ozon_post

    out: dict[str, dict] = {}
    for i in range(0, len(product_ids), _LIST_BATCH):
        chunk = [p for p in product_ids[i:i + _LIST_BATCH] if str(p).isdigit()]
        if not chunk:
            continue
        resp = ozon_post(
            client_id, api_key, "/v3/product/info/list",
            {"product_id": chunk}, timeout=60,
        )
        for it in (resp or {}).get("items") or []:
            if isinstance(it, dict) and it.get("id"):
                out[str(it["id"])] = it
    return out


def _fetch_price_map(client_id: str, api_key: str, product_ids: list[str]) -> dict[str, dict]:
    """/v5/product/info/prices 批量现价 → {product_id: {price, old_price, currency_code, ...}}。

    ⚠️ 契约坑（sweep 已踩平）：/v3/product/info/list 不回价格；/v5 是嵌套 price
    对象（items[].price{price,old_price,currency_code}），product_id 走 filter
    且必须整数数组。
    ✅ v0.83 批⑥ 回执真值化：同一响应里被丢弃的实盘字段（marketing_seller_price/
    commissions/acquiring）原样保留（`price_item`=整条 item 供 utils/profit_reality
    消费），**零新增调用**。
    """
    from utils.ozon_client import ozon_post

    prices: dict[str, dict] = {}
    for i in range(0, len(product_ids), _LIST_BATCH):
        int_ids = [int(p) for p in product_ids[i:i + _LIST_BATCH] if str(p).isdigit()]
        if not int_ids:
            continue
        try:
            resp = ozon_post(
                client_id, api_key, "/v5/product/info/prices",
                {"filter": {"product_id": int_ids}, "limit": _LIST_BATCH},
                timeout=60, language="RU",
            )
        except Exception as exc:
            logger.warning("card_audit 拉现价失败（本轮 D 降级只报告）: %s", str(exc)[:150])
            return prices
        for it in (resp or {}).get("items") or []:
            if not isinstance(it, dict) or not it.get("product_id"):
                continue
            p = it.get("price") or {}
            prices[str(it["product_id"])] = {
                "price": p.get("price"),
                "old_price": p.get("old_price") or p.get("marketing_price") or p.get("price"),
                "currency_code": str(p.get("currency_code") or "CNY"),
                # v0.83 批⑥：实盘字段 + 整条 item（profit_reality 计算用）
                "marketing_seller_price": p.get("marketing_seller_price"),
                "commissions": it.get("commissions") or {},
                "acquiring": it.get("acquiring"),
                "price_item": it,
            }
    return prices


def _fetch_ratings(client_id: str, api_key: str, skus: list[str]) -> dict[str, dict]:
    """/v1/product/rating-by-sku 批量（100/批）→ {sku: product}。"""
    from utils.content_enrich import parse_rating_products
    from utils.ozon_client import ozon_post

    out: dict[str, dict] = {}
    for i in range(0, len(skus), _RATING_BATCH):
        chunk = skus[i:i + _RATING_BATCH]
        try:
            resp = ozon_post(
                client_id, api_key, "/v1/product/rating-by-sku",
                {"skus": chunk}, timeout=60, language="RU",
            )
        except Exception as exc:
            logger.warning("card_audit 批量评级失败（本批跳过）: %s", str(exc)[:150])
            continue
        for p in parse_rating_products(resp):
            if p.get("sku") is not None:
                out[str(p["sku"])] = p
    return out


def _fetch_card_echoes(client_id: str, api_key: str, product_ids: list[str]) -> dict[str, dict]:
    """/v4/product/info/attributes 批量全量回显 → {product_id: stored_item}。

    Ozon 对 info 族限流下会静默返回空——空批次退避重试一次（sweep 同款）。
    """
    import time as _time

    from utils.ozon_client import ozon_post

    echoes: dict[str, dict] = {}
    for i in range(0, len(product_ids), _LIST_BATCH):
        chunk = [p for p in product_ids[i:i + _LIST_BATCH] if str(p).isdigit()]
        if not chunk:
            continue
        items: list = []
        for attempt in range(2):
            resp = ozon_post(
                client_id, api_key, "/v4/product/info/attributes",
                {"filter": {"product_id": chunk}, "limit": _LIST_BATCH,
                 "sort_by": "id", "sort_dir": "asc"},
                timeout=60, language="RU",
            )
            items = (resp or {}).get("result") or []
            if items:
                break
            _time.sleep(1 + attempt)
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and it.get("id"):
                echoes[str(it["id"])] = it
    return echoes


# ── C 不变量：源-卡映射 + LLM 比对 ───────────────────────────────


def _source_facts(tenant_id: str, product_ids: list[str]) -> dict[str, dict]:
    """product_task_index → listing_result_log 取源侧事实 {product_id: {...}}。

    同 product 多任务（重提）取最近一条；无映射（workbuddy 时代卡）不出键。
    """
    if not product_ids:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            """
            SELECT pti.product_id, l.source_title_cn, l.source_category_path
            FROM product_task_index pti
            JOIN listing_result_log l ON l.task_db_id = pti.task_id::text
            WHERE pti.tenant_id = :t AND pti.product_id = ANY(:pids)
              AND (l.source_title_cn IS NOT NULL OR l.source_category_path IS NOT NULL)
            ORDER BY l.created_at DESC
            """
        ), {"t": tenant_id, "pids": list(product_ids)}).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        pid = str(r.product_id)
        if pid in out:
            continue  # 已取最近一条
        out[pid] = {
            "source_title_cn": str(r.source_title_cn or ""),
            "source_category_path": str(r.source_category_path or ""),
        }
    return out


def _follow_sell_ids(tenant_id: str, product_ids: list[str]) -> set:
    """v0.83 批②（A6 红线）：返回「跟卖卡」的 product_id 集（我方绝不写竞品卡面）。

    源：product_task_index → listing_result_log.pipeline_source='follow'（真跟卖标记，
    listing_result_log._pipeline_source 三态判定；discover 变体不是 follow）。
    查不到映射（workbuddy 时代卡）→ 不出键（保守：按普通卡处理，无 draft 证据时
    enrich 也只做保守裁决）。
    """
    if not product_ids:
        return set()
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                """
                SELECT DISTINCT pti.product_id
                FROM product_task_index pti
                JOIN listing_result_log l ON l.task_db_id = pti.task_id::text
                WHERE pti.tenant_id = :t AND pti.product_id = ANY(:pids)
                  AND l.pipeline_source = 'follow'
                """
            ), {"t": tenant_id, "pids": list(product_ids)}).fetchall()
    except Exception as exc:
        logger.warning("card_audit 跟卖标记查询失败（按空集处理）: %s", str(exc)[:150])
        return set()
    return {str(r[0]) for r in rows if r and r[0] is not None}


def _llm_source_match(llm_token: str, source_title_cn: str,
                      source_category_path: str, card_name: str) -> dict:
    """LLM 语义比对 → {verdict: match/mismatch/unsure, reason}。

    防御式解析（模型输出围栏/前后杂语容忍）；返回 unsure 的情形（响应 None、
    解析失败、verdict 非法）一律不产 finding——错报比漏报代价高。
    异常由调用方隔离（OutOfQuota 上抛终止本轮 C）。
    """
    from utils.mxou_api import call_mxou_chat_api

    system = (
        "你是跨境电商商品一致性审核助手。判断 Ozon 现卡商品与 1688 源商品是否为"
        "同一类商品（允许规格/颜色/措辞差异，不允许品类错放，如源是沥水篮而卡是"
        "除草剂）。只输出 JSON，不要多余文字："
        '{"verdict": "match|mismatch|unsure", "reason": "<=100字"}'
    )
    user = (
        f"1688 源标题(中文): {source_title_cn or '(无)'}\n"
        f"1688 源类目: {source_category_path or '(无)'}\n"
        f"Ozon 现卡名称: {card_name or '(无)'}"
    )
    resp = call_mxou_chat_api(llm_token, system, user, temperature=0.0,
                              max_tokens=200, timeout=30)
    if not resp:
        return {"verdict": "unsure", "reason": "llm_no_response"}
    raw = _JSON_FENCE_RE.sub("", str(resp).strip()).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"verdict": "unsure", "reason": "llm_unparsable"}
    try:
        data = json.loads(m.group())
    except ValueError:
        return {"verdict": "unsure", "reason": "llm_unparsable"}
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("match", "mismatch", "unsure"):
        return {"verdict": "unsure", "reason": f"llm_bad_verdict:{verdict[:20]}"}
    return {"verdict": verdict, "reason": str(data.get("reason") or "")[:200]}


# ── 四不变量判定 ────────────────────────────────────────────────


def _severity_for_rating(rating: float) -> str:
    if rating < 40:
        return "high"  # <40 几乎不展示（content_enrich 模块注释口径）
    if rating < 70:
        return "medium"
    return "low"


def _check_rating_gap(state: dict, pid: str, rating_p: dict, stored: Optional[dict],
                      px: dict, info: dict) -> None:
    """A rating_gap：评级 <90 且缺口 ⊆ 保守白名单 → 自动修；其余落 finding。"""
    from utils.content_enrich import (
        build_enrich_update_body,
        extract_improve_attrs,
    )
    from utils.ozon_client import ozon_post

    # ✅ v0.83 批②（A6 红线）：跟卖卡是竞品卡，我方一个字节都不写（含 4191/11254）。
    # 不落 finding（否则每轮巡检都重复开单），只计数留痕。
    if pid in (state.get("follow_ids") or set()):
        state["summary"]["follow_skipped"] = state["summary"].get("follow_skipped", 0) + 1
        logger.info("card_audit A 跳过跟卖卡 pid=%s（不写竞品卡面）", pid)
        return

    rating = float(rating_p.get("rating") or 0)
    improve = extract_improve_attrs(rating_p)
    detail = {
        "rating": rating,
        "improve_attrs": improve,
        "threshold": _RATING_THRESHOLD,
    }
    summary = state["summary"]

    if not _autofix_enabled():
        detail["action"] = "report_only"
        detail["reason"] = "autofix_disabled"
        _record_finding(state["tenant"], state["cid"], pid,
                        INVARIANT_RATING_GAP, _severity_for_rating(rating), detail)
        summary["findings_open"] += 1
        return

    if not stored:
        detail["action"] = "report_only"
        detail["reason"] = "no_card_echo"
        _record_finding(state["tenant"], state["cid"], pid,
                        INVARIANT_RATING_GAP, _severity_for_rating(rating), detail)
        summary["findings_open"] += 1
        return

    # 唯一构造器（A6 防洗卡：全量回显）；无 draft/schema 证据 → 只补 4191/11254
    body, audit = build_enrich_update_body(
        pid, stored, improve, {}, {},
        price=px.get("price"), old_price=px.get("old_price"),
        currency_code=px.get("currency_code") or "CNY",
        vat=info.get("vat"), images360=info.get("images360"),
    )
    detail["filled"] = audit.get("filled", [])
    detail["skipped"] = audit.get("skipped", [])
    detail["media_gap"] = audit.get("media_gap", [])

    if not body:
        detail["action"] = "report_only"
        detail["reason"] = audit.get("reason") or "constructor_abstained"
        _record_finding(state["tenant"], state["cid"], pid,
                        INVARIANT_RATING_GAP, _severity_for_rating(rating), detail)
        summary["findings_open"] += 1
        summary["autofix_failed_open"] += 1
        return

    try:
        ozon_post(state["client_id"], state["api_key"], "/v3/product/import",
                  body, timeout=60)
    except Exception as exc:
        detail["action"] = "report_only"
        detail["reason"] = f"import_failed:{str(exc)[:120]}"
        _record_finding(state["tenant"], state["cid"], pid,
                        INVARIANT_RATING_GAP, _severity_for_rating(rating), detail)
        summary["findings_open"] += 1
        summary["autofix_failed_open"] += 1
        return

    detail["action"] = "auto_fixed"
    _record_finding(state["tenant"], state["cid"], pid,
                    INVARIANT_RATING_GAP, "low", detail, resolved=True)
    summary["auto_fixed"] += 1


def _declined_auto_archive_enabled() -> bool:
    """B 归档动作 kill-switch：CARD_AUDIT_DECLINED_AUTO_ARCHIVE（缺省开）。

    归档仅限决策器判「标题残壳/资质族」的卡（可逆 unarchive、逐卡留痕）；
    置 0 → 该分支降级为只报告（action=archive_suggested）。
    """
    return os.getenv("CARD_AUDIT_DECLINED_AUTO_ARCHIVE", "1").strip() not in ("0", "false", "False")


def _check_declined(state: dict, pid: str, info: dict) -> None:
    """B declined（pass 1）：分级决策（唯一决策源 declined_disposition）。

    - report → 立即落 finding（保守人工：无错误码/未知混码）；
    - archive → 入 state["declined_archive"]，轮末批量 POST /v1/product/archive
      （_flush_declined_archives；kill-switch 关 → 落 archive_suggested finding）；
    - auto_repair → 入 state["declined_repair"][pid]，等回显（pass 2
      _apply_declined_repair 经唯一构造器全量回显 UPDATE）。
    - 跟卖卡：跟卖 = 竞品卡，我方零字节写入（含归档）——只计数（同 A 闸）。
    """
    summary = state["summary"]
    summary["declined"] = summary.get("declined", 0) + 1

    if pid in (state.get("follow_ids") or set()):
        summary["declined_follow_skipped"] = summary.get("declined_follow_skipped", 0) + 1
        logger.info("card_audit B 跳过跟卖卡 pid=%s（零写入含归档）", pid)
        return

    from utils.declined_disposition import declined_disposition

    decision = declined_disposition(info)
    action = decision.get("action")
    detail = {
        "moderate_status": str((info.get("statuses") or {}).get("moderate_status") or ""),
        "validation_status": str((info.get("statuses") or {}).get("validation_status") or ""),
        "codes": decision.get("codes") or [],
        "reason": decision.get("reason") or "",
        "action": "report_only",
    }

    if action == "report":
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_DECLINED, "high", detail)
        summary["findings_open"] += 1
        summary["declined_reported"] = summary.get("declined_reported", 0) + 1
        return

    if action == "archive":
        if not _declined_auto_archive_enabled():
            detail["action"] = "archive_suggested"
            detail["reason2"] = "auto_archive_disabled"
            _record_finding(state["tenant"], state["cid"], pid, INVARIANT_DECLINED, "high", detail)
            summary["findings_open"] += 1
            summary["declined_reported"] = summary.get("declined_reported", 0) + 1
            return
        state.setdefault("declined_archive", []).append(
            {"pid": pid, "detail": detail})
        return

    # auto_repair：等 pass 2 回显
    state.setdefault("declined_repair", {})[pid] = decision
    summary["declined_repair_attempted"] = summary.get("declined_repair_attempted", 0) + 1


def _apply_declined_repair(state: dict, pid: str, info: dict, echo: Optional[dict],
                           px: dict) -> bool:
    """B declined（pass 2）：可修族经唯一构造器全量回显 UPDATE。

    回显先过 patch_echo_for_declines 定向补丁（数值清洗/重量密度/维度 clamp/
    空值剔除——只修 Ozon 点名问题，其余字节不动）；DESCRIPTION_DECLINE 经
    allow_annotation_replace 重建 4191（构造器内部「更长才替换」防降级）。
    失败 → finding open 留痕（finding 幂等挡下轮重试 = 设计内熔断，同 A 闸）。
    返回 True **仅当** UPDATE POST 成功发出——调用方据此本轮跳过 A（✅
    v0.83.2 验收修复：A 的全量回显基是 POST 前拉的 echo、不含本函数的定向
    补丁，同卡同轮二连发会把修复洗掉、下轮再修再洗永不收敛）。
    """
    from utils.content_enrich import build_enrich_update_body
    from utils.declined_disposition import patch_echo_for_declines
    from utils.ozon_client import ozon_post

    summary = state["summary"]
    decision = (state.get("declined_repair") or {}).get(pid) or {}
    detail = {
        "codes": decision.get("codes") or [],
        "reason": decision.get("reason") or "",
    }

    if not echo:
        detail.update(action="report_only", reason="no_card_echo")
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_DECLINED, "high", detail)
        summary["findings_open"] += 1
        summary["declined_repair_failed"] = summary.get("declined_repair_failed", 0) + 1
        return False

    patched, changes = patch_echo_for_declines(echo, info)
    detail["patched"] = changes
    if patched is None:
        detail.update(action="report_only", reason="echo_shape_invalid")
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_DECLINED, "high", detail)
        summary["findings_open"] += 1
        summary["declined_repair_failed"] = summary.get("declined_repair_failed", 0) + 1
        return False

    body, audit = build_enrich_update_body(
        pid, patched, [], {}, {},
        price=px.get("price"), old_price=px.get("old_price"),
        currency_code=px.get("currency_code") or "CNY",
        vat=info.get("vat"), images360=info.get("images360"),
        allow_annotation_replace=("DESCRIPTION_DECLINE" in (decision.get("codes") or [])),
    )
    detail["filled"] = audit.get("filled", [])
    if not body:
        detail.update(action="report_only", reason=audit.get("reason") or "constructor_abstained")
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_DECLINED, "high", detail)
        summary["findings_open"] += 1
        summary["declined_repair_failed"] = summary.get("declined_repair_failed", 0) + 1
        return False

    try:
        ozon_post(state["client_id"], state["api_key"], "/v3/product/import",
                  body, timeout=60)
    except Exception as exc:
        detail.update(action="report_only", reason=f"import_failed:{str(exc)[:120]}")
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_DECLINED, "high", detail)
        summary["findings_open"] += 1
        summary["declined_repair_failed"] = summary.get("declined_repair_failed", 0) + 1
        return False

    detail["action"] = "auto_repaired"
    _record_finding(state["tenant"], state["cid"], pid, INVARIANT_DECLINED, "low",
                    detail, resolved=True)
    summary["declined_repaired"] = summary.get("declined_repaired", 0) + 1
    return True


def _flush_declined_archives(state: dict) -> None:
    """B 归档批量出口：/v1/product/archive（≤100/批，可逆 unarchive）。

    只处理 pass 1 决策器判 archive 的卡；逐卡 finding 留痕（成功 resolved
    action=auto_archived / 失败 open action=report_only）。批量失败不中断
    后续批（单批 try/except 隔离，同整域纪律）。
    """
    from utils.ozon_client import ozon_post

    batch = state.get("declined_archive") or []
    summary = state["summary"]
    for i in range(0, len(batch), 100):
        chunk = batch[i:i + 100]
        try:
            ozon_post(state["client_id"], state["api_key"], "/v1/product/archive",
                      {"product_id": [int(c["pid"]) for c in chunk]}, timeout=60)
            ok = {c["pid"] for c in chunk}
        except Exception as exc:
            logger.warning("card_audit B 归档批次失败（%s 张降级报告）: %s",
                           len(chunk), str(exc)[:150])
            ok = set()
        for c in chunk:
            detail = dict(c["detail"])
            if c["pid"] in ok:
                detail["action"] = "auto_archived"
                _record_finding(state["tenant"], state["cid"], c["pid"],
                                INVARIANT_DECLINED, "low", detail, resolved=True)
                summary["declined_archived"] = summary.get("declined_archived", 0) + 1
            else:
                detail["action"] = "report_only"
                detail["reason2"] = "archive_failed"
                _record_finding(state["tenant"], state["cid"], c["pid"],
                                INVARIANT_DECLINED, "high", detail)
                summary["findings_open"] += 1
                summary["declined_archive_failed"] = summary.get("declined_archive_failed", 0) + 1


def _price_violations(price: Optional[float], old_price: Optional[float],
                      min_price: Optional[float]) -> tuple[Optional[str], Optional[str]]:
    """D 判定 → (auto_fix_reason, report_reason)；均 None = 无违规。

    auto_fix 口径（PLAN §3）：缺 old_price / old_price < price / 差价不足
    （enforce_old_price_rule 会抬高）→ 单字段修复；
    report 口径：min_price 关系异常（可得时）。毛利率异常无成本汇率数据，出圈。
    """
    from utils.pricing_estimate import enforce_old_price_rule

    if not price or price <= 0:
        return None, None  # 无价卡（超价/隔离）无从判起，不产噪音

    if old_price is None or old_price <= 0:
        return "old_price_missing", None
    if old_price < price:
        return "old_price_below_price", None
    enforced = enforce_old_price_rule(price, old_price)
    try:
        if enforced is not None and int(float(enforced)) > int(float(old_price)):
            return "old_price_gap_insufficient", None
    except (TypeError, ValueError):
        return "old_price_gap_insufficient", None

    if min_price is not None and min_price > 0:
        if min_price < price * 0.5:
            return None, "min_price_below_half_price"
        if min_price > price:
            return None, "price_below_min_price"
    return None, None


def _check_price_sanity(state: dict, pid: str, stored: Optional[dict],
                        px: dict, info: dict) -> None:
    """D price_sanity：阈值内自动修（价格家族构造器）；其余只报告。"""
    from utils.content_enrich import build_price_update_body
    from utils.ozon_client import ozon_post

    price = _to_num(px.get("price"))
    old_price = _to_num(px.get("old_price"))
    min_price = _to_num(info.get("min_price"))
    fix_reason, report_reason = _price_violations(price, old_price, min_price)
    summary = state["summary"]

    if report_reason:
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_PRICE_SANITY,
                        "medium", {
                            "price": price, "old_price": old_price,
                            "min_price": min_price, "reason": report_reason,
                            "action": "report_only",
                        })
        summary["findings_open"] += 1
        summary["price_reported"] += 1
        return

    if not fix_reason:
        return
    if not _autofix_enabled():
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_PRICE_SANITY,
                        "medium", {
                            "price": price, "old_price": old_price,
                            "reason": fix_reason, "action": "report_only",
                            "reason2": "autofix_disabled",
                        })
        summary["findings_open"] += 1
        summary["price_reported"] += 1
        return
    if not stored:
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_PRICE_SANITY,
                        "medium", {
                            "price": price, "old_price": old_price,
                            "reason": fix_reason, "action": "report_only",
                            "reason2": "no_card_echo",
                        })
        summary["findings_open"] += 1
        summary["price_reported"] += 1
        return

    body, reason = build_price_update_body(
        pid, stored, price=px.get("price"), old_price=px.get("old_price"),
        currency_code=px.get("currency_code") or "CNY",
        vat=info.get("vat"), images360=info.get("images360"),
    )
    if not body:
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_PRICE_SANITY,
                        "medium", {
                            "price": price, "old_price": old_price,
                            "reason": fix_reason, "action": "report_only",
                            "reason2": f"constructor_abstained:{reason}",
                        })
        summary["findings_open"] += 1
        summary["price_reported"] += 1
        return
    try:
        ozon_post(state["client_id"], state["api_key"], "/v3/product/import",
                  body, timeout=60)
    except Exception as exc:
        _record_finding(state["tenant"], state["cid"], pid, INVARIANT_PRICE_SANITY,
                        "medium", {
                            "price": price, "old_price": old_price,
                            "reason": fix_reason, "action": "report_only",
                            "reason2": f"import_failed:{str(exc)[:120]}",
                        })
        summary["findings_open"] += 1
        summary["price_reported"] += 1
        return
    summary["price_fixed"] += 1
    summary["auto_fixed"] += 1
    _record_finding(state["tenant"], state["cid"], pid, INVARIANT_PRICE_SANITY,
                    "low", {"price": price, "old_price": old_price,
                            "new_old_price_rule_applied": True,
                            "reason": fix_reason, "action": "auto_fixed"},
                    resolved=True)


def _check_source_mismatch(state: dict, pid: str, card_name: str,
                           source: dict) -> None:
    """C source_mismatch：LLM 比对 → 只报告（绝不改卡）。cap 由调用方预算控制。"""
    verdict = _llm_source_match(
        state["llm_token"], source.get("source_title_cn", ""),
        source.get("source_category_path", ""), card_name)
    state["summary"]["source_checked"] += 1
    if verdict["verdict"] != "mismatch":
        return
    _record_finding(state["tenant"], state["cid"], pid, INVARIANT_SOURCE_MISMATCH,
                    "high", {
                        "card_name": card_name[:300],
                        "source_title_cn": source.get("source_title_cn", "")[:300],
                        "source_category_path": source.get("source_category_path", "")[:300],
                        "llm_verdict": verdict["verdict"],
                        "llm_reason": verdict["reason"],
                        "action": "report_only",
                    })
    state["summary"]["source_mismatch"] += 1
    state["summary"]["findings_open"] += 1


# ── 主入口 ─────────────────────────────────────────────────────


def _profit_facts(tenant_id: str, product_ids: list[str]) -> dict[str, dict]:
    """pti → listing_result_log 取预估侧事实 {product_id: {...}}（同 product 取最近一条）。

    预估利润来自提交期 pricing_info.profit_estimation.profit_cny；成本/运费同源
    pricing_info（cost_cny/logistics_cost_cny，缺失回落 listing_result_log.purchase_cost）。
    """
    if not product_ids:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            """
            SELECT pti.product_id, l.pricing_info, l.purchase_cost
            FROM product_task_index pti
            JOIN listing_result_log l ON l.task_db_id = pti.task_id::text
            WHERE pti.tenant_id = :t AND pti.product_id = ANY(:pids)
            ORDER BY l.created_at DESC
            """
        ), {"t": tenant_id, "pids": list(product_ids)}).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        pid = str(r.product_id)
        if pid in out:
            continue  # 已取最近一条
        pi = r.pricing_info if isinstance(r.pricing_info, dict) else {}
        pe = pi.get("profit_estimation") if isinstance(pi.get("profit_estimation"), dict) else {}
        predicted = _to_num(pe.get("profit_cny"))
        out[pid] = {
            "predicted_profit_cny": predicted,
            "purchase_cost_cny": _to_num(pi.get("cost_cny")) or _to_num(r.purchase_cost),
            "logistics_cost_cny": _to_num(pi.get("logistics_cost_cny")),
            "currency_code": str(pi.get("currency_code") or ""),
            "commission_source_at_submit": _commission_source_at_submit(pi),
        }
    return out


def _commission_source_at_submit(pi: dict) -> str:
    """提交期佣金来源归一：pricing_info.commission_source 只在超龄降级时落
    'stale_fallback'（pricing_core 口径）→ 归一为 'fallback:stale'；'fallback' 原样。
    其余/缺失 → ''（真实来源在场，差异不可归因于 fallback）。"""
    raw = str((pi or {}).get("commission_source") or "").strip()
    if raw == "stale_fallback":
        return "fallback:stale"
    if raw == "fallback":
        return "fallback"
    return raw


def _resolve_audit_fx_rate() -> float:
    """card_audit 实盘利润用的 CNY→RUB 汇率（一次解析，全轮复用；PG 缓存优先）。"""
    try:
        from utils.fx_rate_service import resolve_cny_rub_rate
        rate, _src = resolve_cny_rub_rate()
        return float(rate) if rate and rate > 0 else 12.0
    except Exception as exc:
        logger.warning("card_audit 实盘利润取汇率失败（按 12.0 兜底）: %s", str(exc)[:150])
        return 12.0


def _severity_for_gap(gap_pct: float) -> str:
    g = abs(float(gap_pct))
    if g >= 0.5:
        return "high"
    if g >= 0.25:
        return "medium"
    return "low"


def _check_profit_reality(state: dict, pid: str, px: dict, info: dict) -> None:
    """E profit_reality：预估利润 vs 当前 /v5 实盘利润，**双门 + 最低价门槛**防噪。

    - 双门（PCT + ABS）同时超才开 finding；
    - 最低价门槛：real_price_rub < PROFIT_REALITY_MIN_PRICE_RUB → 跳过（低价卡噪声）；
    - `commission_source_at_submit ∈ {fallback, fallback:stale}` 的卡：设计内差异，
      只计数 + 日志（severity=info），**不自动开 open finding**（防噪把不变量做废）；
    - 跟卖卡参与（我方定价口径，A6 内容红线不涉价格域）。
    只报告，绝不改卡（价格域不改价——改价是 D 唯一白名单）。
    """
    from utils.profit_reality import compute_profit_reality

    summary = state["summary"]
    facts = (state.get("profit_facts") or {}).get(pid)
    if not facts:
        summary["profit_skipped_no_predicted"] += 1
        return
    predicted = facts.get("predicted_profit_cny")
    price_item = px.get("price_item")
    if predicted is None or not isinstance(price_item, dict):
        summary["profit_skipped_no_predicted"] += 1
        return

    currency_code = str(px.get("currency_code") or facts.get("currency_code") or "RUB").upper()
    reality = compute_profit_reality(
        price_item,
        purchase_cost_cny=facts.get("purchase_cost_cny"),
        logistics_cost_cny=facts.get("logistics_cost_cny"),
        predicted_profit_cny=predicted,
        fx_rate=state.get("cny_rub_rate") or 1.0,
        currency_code=currency_code,
        commission_mode=state.get("commission_mode") or "rfbs",
    )
    if not reality:
        summary["profit_skipped_no_predicted"] += 1
        return
    summary["profit_checked"] += 1

    if float(reality.get("real_price_rub") or 0) < _profit_min_price_rub():
        summary["profit_below_price"] += 1
        return

    gap_cny = reality.get("gap_cny")
    gap_pct = reality.get("gap_pct")
    if gap_cny is None or gap_pct is None:
        return
    if abs(gap_pct) <= _profit_gap_pct_threshold() or abs(gap_cny) <= _profit_gap_abs_threshold():
        return  # 双门未同时超 → 不开 finding

    detail = {
        "predicted_profit_cny": reality.get("predicted_profit_cny"),
        "real_profit_cny": reality.get("real_profit_cny"),
        "gap_cny": gap_cny,
        "gap_pct": gap_pct,
        "real_price_rub": reality.get("real_price_rub"),
        "real_commission_pct": reality.get("real_commission_pct"),
        "acquiring_pct": reality.get("acquiring_pct"),
        "fbs_fees_rub": reality.get("fbs_fees_rub"),
        "unmodeled_fees": reality.get("unmodeled_fees"),
        "fx_rate": reality.get("fx_rate"),
        "commission_mode": reality.get("commission_mode"),
        "commission_source_at_submit": facts.get("commission_source_at_submit") or "",
        "action": "report_only",
    }
    if detail["commission_source_at_submit"] in ("fallback", "fallback:stale"):
        summary["profit_reported_info"] += 1
        logger.info(
            "card_audit E 实盘利润差异（fallback 佣金=设计内差异，不 open）pid=%s "
            "gap_cny=%s gap_pct=%s", pid, gap_cny, gap_pct,
        )
        return

    summary["profit_gap"] += 1
    summary["findings_open"] += 1
    _record_finding(state["tenant"], state["cid"], pid, INVARIANT_PROFIT_REALITY,
                    _severity_for_gap(gap_pct), detail)


# ── 主入口 ─────────────────────────────────────────────────────


def run_card_audit(tenant_id: str, credential_id: str) -> dict:
    """单店一轮巡检（调度经 run_card_audit_if_due；测试/运维可直调）。

    全程逐卡 try/except 隔离；任何单卡失败只计入 card_errors。
    返回 summary dict（含发现数/自动修数/capped 等）。
    """
    from services import credential_service

    client_id, api_key = credential_service.get_decrypted(tenant_id, credential_id)
    state = {
        "tenant": tenant_id,
        "cid": str(credential_id),
        "client_id": client_id,
        "api_key": api_key,
        "llm_token": _llm_token(),
        "summary": {
            "credential_id": str(credential_id),
            "total_cards": 0, "archived_skipped": 0,
            "rated": 0, "rating_below": 0,
            "auto_fixed": 0, "autofix_failed_open": 0,
            "declined": 0,
            # ✅ v0.83.2 B 分级处置计数（declined 总数之外的动作分布）
            "declined_reported": 0, "declined_repair_attempted": 0,
            "declined_repaired": 0, "declined_repair_failed": 0,
            "declined_archived": 0, "declined_archive_failed": 0,
            "declined_follow_skipped": 0,
            "source_skipped_no_source": 0, "source_checked": 0,
            "source_mismatch": 0, "capped": False, "llm_skipped_no_token": False,
            "price_fixed": 0, "price_reported": 0,
            "card_errors": 0, "findings_open": 0,
            "follow_skipped": 0,
            # ✅ v0.83 批⑥ E profit_reality 计数
            "profit_checked": 0, "profit_gap": 0, "profit_reported_info": 0,
            "profit_below_price": 0, "profit_skipped_no_predicted": 0,
        },
    }
    summary = state["summary"]

    # ① 全量商品 + 详情（statuses.moderate_status / vat / images360）
    items = _list_all_products(client_id, api_key)
    summary["total_cards"] = len(items)
    if not items:
        return summary
    info_map = _fetch_info_map(client_id, api_key, [it["product_id"] for it in items])
    # 顺手把 moderate_status（及全量字段）落 ozon_products_cache——复用商品域
    # 既有 upsert（同一数据源同一写路径，无新写入口）
    try:
        from services.store_sync_service import _upsert_products
        _upsert_products(tenant_id, credential_id, items, info_map)
    except Exception as exc:
        logger.warning("card_audit 缓存回写失败（不阻断巡检）tenant=%s store=%s: %s",
                       tenant_id, credential_id, str(exc)[:150])

    # ② 现价（/v5）+ 评级（/v1 rating-by-sku，100/批）
    pids = [it["product_id"] for it in items]
    price_map = _fetch_price_map(client_id, api_key, pids)
    ratings = _fetch_ratings(client_id, api_key, pids)
    summary["rated"] = len(ratings)

    # ③ 逐卡判定：归档卡整卡跳过（评分/审核/一致性对其无意义）
    live: list[tuple[str, dict]] = []
    for it in items:
        pid = it["product_id"]
        info = info_map.get(pid) or {}
        if info.get("is_archived") or info.get("is_autoarchived"):
            summary["archived_skipped"] += 1
            continue
        live.append((pid, info))

    # ✅ v0.83 批②：跟卖卡标记（A rating_gap 跳过写竞品卡面，A6 红线）
    state["follow_ids"] = _follow_sell_ids(tenant_id, [p for p, _ in live])

    # ✅ v0.83 批⑥ E 不变量前置：预估侧事实（pti→listing_result_log）+ 汇率/模式一次解析。
    # 巡检面无信封 extensions → 履约模式默认 rFBS 主通道；汇率一次解析全轮复用。
    state["commission_mode"] = "rfbs"
    state["cny_rub_rate"] = _resolve_audit_fx_rate()
    try:
        state["profit_facts"] = _profit_facts(tenant_id, [p for p, _ in live])
    except Exception as exc:
        summary["card_errors"] += 1
        logger.warning("card_audit E 预估侧事实查询失败（本轮 E 跳过）: %s", str(exc)[:150])
        state["profit_facts"] = {}

    need_echo: list[str] = []
    for pid, info in live:
        try:
            # B declined/validation-fail（分级处置 pass 1；先于 A——declined 卡
            # 不需要再补评分）。✅ v0.83.2 触发面扩 validation_status=fail：
            # 校验失败卡与 declined 同为不可售死卡（4718259 实盘 11 张），同路处置。
            statuses = info.get("statuses") if isinstance(info.get("statuses"), dict) else {}
            _b_mod = str(statuses.get("moderate_status") or "").strip()
            _b_val = str(statuses.get("validation_status") or "").strip()
            if _b_mod == "declined" or (_b_val == "fail"):
                _check_declined(state, pid, info)
                # auto_repair 决策的卡需要回显（pass 2 修复用）
                if pid in (state.get("declined_repair") or {}):
                    need_echo.append(pid)
        except Exception as exc:
            summary["card_errors"] += 1
            logger.warning("card_audit B 不变量异常 pid=%s: %s", pid, str(exc)[:150])
        try:
            # A rating_gap
            rating_p = ratings.get(pid)
            rating = float(rating_p.get("rating") or 0) if rating_p else None
            if rating is not None and rating < _RATING_THRESHOLD:
                summary["rating_below"] += 1
                need_echo.append(pid)
        except Exception as exc:
            summary["card_errors"] += 1
            logger.warning("card_audit A 判定异常 pid=%s: %s", pid, str(exc)[:150])
        try:
            # D 判定（修复部分需要回显，先收集）
            px = price_map.get(pid) or {}
            info_min = _to_num(info.get("min_price"))
            fix_reason, report_reason = _price_violations(
                _to_num(px.get("price")), _to_num(px.get("old_price")), info_min)
            if report_reason:
                _check_price_sanity(state, pid, None, px, info)
            elif fix_reason:
                need_echo.append(pid)
        except Exception as exc:
            summary["card_errors"] += 1
            logger.warning("card_audit D 判定异常 pid=%s: %s", pid, str(exc)[:150])

    # ④ 批量回显（A 修复 ∪ D 修复候选一次拉取）
    echoes = _fetch_card_echoes(client_id, api_key, sorted(set(need_echo))) if need_echo else {}

    for pid, info in live:
        _b_repaired = False
        try:
            # ✅ v0.83.2 B pass 2：可修族 declined 修复（回显补丁 + 唯一构造器）
            # ✅ v0.83.2 验收修复：B 成功发出 UPDATE 后本轮**跳过 A**——A 的
            # 全量回显基（POST 前拉的 echo）不含 B 的定向补丁，同卡同轮二连发
            # 会把修复洗掉、下轮再修再洗永不收敛（D/E 只读，不受影响照常跑）。
            if pid in (state.get("declined_repair") or {}):
                _b_repaired = _apply_declined_repair(state, pid, info, echoes.get(pid),
                                                     price_map.get(pid) or {})
        except Exception as exc:
            summary["card_errors"] += 1
            logger.warning("card_audit B 修复异常 pid=%s: %s", pid, str(exc)[:150])
        try:
            rating_p = ratings.get(pid)
            rating = float(rating_p.get("rating") or 0) if rating_p else None
            if rating is not None and rating < _RATING_THRESHOLD and not _b_repaired:
                _check_rating_gap(state, pid, rating_p, echoes.get(pid),
                                  price_map.get(pid) or {}, info)
        except Exception as exc:
            summary["card_errors"] += 1
            logger.warning("card_audit A 不变量异常 pid=%s: %s", pid, str(exc)[:150])
        try:
            px = price_map.get(pid) or {}
            fix_reason, report_reason = _price_violations(
                _to_num(px.get("price")), _to_num(px.get("old_price")),
                _to_num(info.get("min_price")))
            if fix_reason and not report_reason:
                _check_price_sanity(state, pid, echoes.get(pid), px, info)
        except Exception as exc:
            summary["card_errors"] += 1
            logger.warning("card_audit D 不变量异常 pid=%s: %s", pid, str(exc)[:150])
        try:
            # E 实盘利润（v0.83 批⑥；只报告，绝不改价）
            _check_profit_reality(state, pid, price_map.get(pid) or {}, info)
        except Exception as exc:
            summary["card_errors"] += 1
            logger.warning("card_audit E 不变量异常 pid=%s: %s", pid, str(exc)[:150])

    # ✅ v0.83.2 B 轮末：归档批量出口（决策器判 archive 的卡，≤100/批可逆）
    try:
        _flush_declined_archives(state)
    except Exception as exc:
        summary["card_errors"] += 1
        logger.warning("card_audit B 归档出口异常: %s", str(exc)[:150])

    # ⑤ C source_mismatch（LLM；无 token / 超 cap → 本轮跳过并如实标记）
    if not state["llm_token"]:
        # 后台域无用户任务 token：未配置 CARD_AUDIT_LLM_TOKEN 时 C 整体跳过
        # （其余三不变量不受影响），summary 如实标记。
        summary["llm_skipped_no_token"] = True
    else:
        try:
            sources = _source_facts(tenant_id, [p for p, _ in live])
            summary["source_skipped_no_source"] = len(live) - len(sources)
            for pid, info in live:
                source = sources.get(pid)
                if not source:
                    continue  # workbuddy 时代无源侧映射 → 跳过
                if not _llm_budget_take():
                    summary["capped"] = True
                    break
                try:
                    _check_source_mismatch(state, pid, str(info.get("name") or ""), source)
                except Exception as exc:
                    summary["card_errors"] += 1
                    logger.warning("card_audit C 不变量异常 pid=%s: %s", pid, str(exc)[:150])
        except Exception as exc:
            summary["card_errors"] += 1
            logger.warning("card_audit C 源映射异常（本轮 C 跳过）: %s", str(exc)[:150])

    summary["findings_open_end"] = _count_open_findings(tenant_id, credential_id)
    return summary


def _to_num(v: Any) -> Optional[float]:
    """宽松数值化：None/畸形/NaN → None（D 不变量判定专用）。"""
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def run_card_audit_if_due(tenant_id: str, credential_id: str,
                          force: bool = False) -> Optional[dict]:
    """调度入口：enabled + 域水位判定 → run_card_audit；失败落 domain_state 不上抛。

    返回 None = 本轮不该跑（总闸关 / 水位未到）；dict = 巡检 summary（或 {"error"}）。
    与 returns/rating 等域同口径：巡检失败不置 job failed，只落域错误。
    """
    if not card_audit_enabled():
        return None
    from services.store_sync_service import _domain_due, _domain_state_update

    if not _domain_due(tenant_id, credential_id, "card_audit", CARD_AUDIT_INTERVAL_MIN, force):
        return None
    try:
        summary = run_card_audit(tenant_id, credential_id)
    except Exception as exc:
        logger.warning("card_audit 巡检失败 tenant=%s store=%s: %s",
                       tenant_id, credential_id, str(exc)[:200])
        try:
            _domain_state_update(tenant_id, credential_id, "card_audit",
                                 error=str(exc)[:200])
        except Exception:
            pass
        return {"error": str(exc)[:200]}
    try:
        _domain_state_update(
            tenant_id, credential_id, "card_audit",
            last_synced_at=_now_iso(), error="",
            scanned=int(summary.get("total_cards") or 0),
            findings=int(summary.get("findings_open") or 0),
            auto_fixed=int(summary.get("auto_fixed") or 0),
        )
    except Exception as exc:
        logger.warning("card_audit 水位推进失败（不阻断）: %s", str(exc)[:150])
    _notify_summary(tenant_id, credential_id, summary)
    return summary


def _notify_summary(tenant_id: str, credential_id: str, summary: dict) -> None:
    """发现/自动修汇总经任务通知通道外发（复用 TASK_NOTIFY_URL + SSRF 校验）。

    零配置（TASK_NOTIFY_URL 未设）时 _send_task_notify 静默返回——与本域
    「零配置静默跳过」口径一致。零发现零修复不打扰。
    """
    found = int(summary.get("findings_open") or 0) + int(summary.get("auto_fixed") or 0)
    if not found and not summary.get("error"):
        return
    try:
        from orchestrator.task_processor import _send_task_notify  # ✅ W3a: 编排器归位
        _send_task_notify(
            task_id=f"card_audit:{credential_id}",
            status="card_audit_sweep",
            graph_result={"product_summary": {"domain": "card_audit", **summary}},
            payload={"ozon_client_id": ""},
        )
    except Exception as exc:
        logger.warning("card_audit 通知失败（不影响巡检结果）: %s", str(exc)[:150])
