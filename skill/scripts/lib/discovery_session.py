#!/usr/bin/env python3
"""discover Session canonical 落盘（v0.83 批⑤，方案 docs/PLAN-v083-quality-campaign-v1.md §3⑤）。

**改本模块前先读方案该批节。** 本模块是「一次 discover = 一个自包含 session 文档」
的唯一事实源：

- schema ``discover.session.v1``：``{schema_version, session_run_id, created_at,
  entry, params, env, candidates[], summary}``；candidate 按分组结构
  （id/ozon/metrics/competition/match_1688/pricing/status/status_reason/
  discovery_meta/provenance），provenance 存 raw 证据（图 ≤10 张 / 文本截 500 字 /
  卖家列表 ≤20 条 cap）。
- run_id 形如 ``disc_<yymmdd_hhmmss>_<6hex>``——**带 ``disc_`` 前缀，绝不用裸 id**
  （worker 侧 run_id/task_id 语义已被占用，见实锤 F）。
- 本地落盘：``data/discovery/sessions/{run_id}.json``（单文件自包含）+
  ``sessions/index.jsonl``（一行一 run：run_id/entry/summary/created_at）。
  替代旧 ``discovery_*.json``（旧文件不回填；``load_latest_discovery`` 兼容读旧）。
- 上报 canonical：``POST /api/v1/discovery/runs`` 带 ``session_run_id`` /
  ``schema_version`` / ``session_json``（自包含）+ 现有 ``candidates`` 投影（向后兼容）。
  上报幂等（worker 侧按 session_run_id upsert），故 ``--sync-sessions`` 补传安全。

**红线（gitleaks）**：session 文档绝不落 cookie/token 明文——本模块只序列化
候选选品字段，任何敏感键都不在字段集内；``test_discovery_session_v083`` 有断言锁定。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "discover.session.v1"

# skill/data/discovery/sessions —— 与 ozon_discovery.DISCOVERY_CACHE_DIR 同根
_DISCOVERY_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "discovery"
SESSIONS_DIR = _DISCOVERY_DIR / "sessions"
INDEX_PATH = SESSIONS_DIR / "index.jsonl"
_INDEX_LOCK = SESSIONS_DIR / ".index.lock"

# raw 证据 cap（单候选；方案 §3⑤：图 ≤10 张 / 文本截 500 字 / 卖家列表 ≤20 条）
_CAP_IMAGES = 10
_CAP_SELLERS = 20
_CAP_TEXT = 500

_VALID_ENTRY_KINDS = ("discover", "discover-multi", "discover-task", "seller", "queries")


# ─────────────────────────────────────────────────────────────────────────
# run_id 与进程内 session 上下文
# ─────────────────────────────────────────────────────────────────────────

# 进程内当前 session（CLI 进程启动 begin_session 后全链路共享；库直调无 = 走旧落盘）。
_CURRENT: dict | None = None


def new_run_id(now: time.struct_time | None = None) -> str:
    """生成 ``disc_<yymmdd_hhmmss>_<6hex>``（不可枚举：随机段 16^6）。"""
    ts = time.strftime("%y%m%d_%H%M%S", now or time.localtime())
    return f"disc_{ts}_{secrets.token_hex(3)}"


def is_valid_run_id(run_id: str) -> bool:
    """``disc_<6digits>_<6digits>_<6hex>`` 形态校验（防路径注入/裸 id）。"""
    rid = (run_id or "").strip()
    if not rid.startswith("disc_"):
        return False
    parts = rid.split("_")
    if len(parts) != 4:
        return False
    _, d, t, hx = parts
    if len(d) != 6 or len(t) != 6 or not (d + t).isdigit():
        return False
    return len(hx) == 6 and all(ch in "0123456789abcdef" for ch in hx)


def begin_session(*, kind: str, keyword: str = "", url: str = "",
                  keywords: list[str] | None = None, seller_id: str = "",
                  params: dict | None = None, env: dict | None = None) -> str:
    """CLI 进程启动即调用——生成 run_id 并登记进程内 session 上下文。

    返回 session_run_id（全链路共享：落盘/上报/尾 JSON/信封 discovery_meta.run_id）。
    """
    global _CURRENT
    if kind not in _VALID_ENTRY_KINDS:
        kind = "discover"
    _CURRENT = {
        "schema_version": SCHEMA_VERSION,
        "session_run_id": new_run_id(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entry": {
            "kind": kind,
            "keyword": keyword or "",
            "url": url or "",
            "keywords": list(keywords or []),
            "seller_id": seller_id or "",
        },
        "params": dict(params or {}),
        "env": dict(env or {}),
        "session_path": "",
    }
    return _CURRENT["session_run_id"]


def current_session() -> dict | None:
    """当前进程 session 上下文副本（无 → None）。"""
    return dict(_CURRENT) if _CURRENT else None


def current_run_id() -> str:
    return str((_CURRENT or {}).get("session_run_id") or "")


def session_path() -> str:
    return str((_CURRENT or {}).get("session_path") or "")


def set_session_path(path: str) -> None:
    """落盘后回填 session_path（供尾 JSON ``_out`` 使用；无上下文忽略）。"""
    if _CURRENT is not None:
        _CURRENT["session_path"] = str(path or "")


def end_session() -> None:
    """清空进程内上下文（测试/进程收尾用；CLI 单次跑可不调用）。"""
    global _CURRENT
    _CURRENT = None


# ─────────────────────────────────────────────────────────────────────────
# 候选：扁平 dict ⇄ 分组结构（映射不丢字段）
# ─────────────────────────────────────────────────────────────────────────

# 各分组字段集（值来自 ProductCandidate 扁平名；分组只为可读与证据分层）。
_GROUP_ID = ("ozon_product_id",)
_GROUP_OZON = (
    "ozon_title", "ozon_price", "ozon_url", "category", "brand",
    "rating", "review_count", "sales_schema", "ozon_old_price",
    "page_category_path", "page_web_category_id", "ozon_category",
)
_GROUP_METRICS = (
    "monthly_sales", "monthly_revenue", "sales_growth", "drr", "create_days",
    "has_analytics", "session_count", "conv_to_cart_pdp", "conv_to_cart_search",
    "days_in_promo", "discount", "promo_revenue_share", "days_with_trafarets",
    "nullable_redemption_rate", "return_cancel_rate", "custom_click_rate",
    "blue_ocean_score",
)
_GROUP_COMPETITION = (
    "competing_sellers", "min_competing_price", "follow_profit_cny", "follow_margin",
)
_GROUP_MATCH = (
    "match_1688_url", "match_1688_title", "match_1688_price",
    "match_1688_category_id", "match_1688_category_name",
    "match_confidence", "match_badge_eff", "match_reject_reason",
    "match_category_divergent", "match_semantic_unknown", "review_decision",
    "match_1688_freight_cny",
)
_GROUP_PRICING = (
    "estimated_logistics_cny", "estimated_commission", "estimated_profit_cny",
    "profit_margin", "estimate_source", "commission_source",
    "logistics_estimated", "logistics_fallback_chain",
    "commission_fbp", "commission_rfbs",
    "commission_rfbs_segments", "commission_fbp_segments",
    "weight_g", "dimensions_mm",
)
# raw 证据（provenance）——大列表/证据链，按 cap 裁剪。
_PROV_IMAGES = ("ozon_images", "match_1688_images")
_PROV_LISTS = ("competing_seller_list", "source_chain")

_KNOWN_GROUPS = (
    _GROUP_ID, _GROUP_OZON, _GROUP_METRICS, _GROUP_COMPETITION,
    _GROUP_MATCH, _GROUP_PRICING,
)
_ALL_GROUPED_KEYS = frozenset(k for g in _KNOWN_GROUPS for k in g)


def _truncate_text(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _CAP_TEXT:
        return v[:_CAP_TEXT]
    return v


def _cap_list(v: Any, cap: int) -> list:
    if not isinstance(v, list):
        return []
    return list(v[:cap])


def _flatten_value(v: Any) -> Any:
    return v


def _flat_candidate(c: Any) -> dict:
    """候选对象 → 扁平 dict（dataclass asdict 优先；已是 dict 原样浅拷贝）。"""
    if isinstance(c, dict):
        return dict(c)
    if is_dataclass(c):
        try:
            return asdict(c)
        except Exception:
            pass
    return dict(getattr(c, "__dict__", {}) or {})


def group_candidate(flat: dict) -> dict:
    """扁平候选 dict → canonical 分组结构（raw 证据进 provenance 并按 cap 裁剪）。"""
    grouped: dict[str, dict] = {}
    for group_name, keys in (
        ("id", _GROUP_ID), ("ozon", _GROUP_OZON), ("metrics", _GROUP_METRICS),
        ("competition", _GROUP_COMPETITION), ("match_1688", _GROUP_MATCH),
        ("pricing", _GROUP_PRICING),
    ):
        grouped[group_name] = {k: _flatten_value(flat.get(k)) for k in keys}

    grouped["status"] = str(flat.get("status") or "")
    grouped["status_reason"] = str(flat.get("error") or "")
    _dm = flat.get("discovery_meta")
    grouped["discovery_meta"] = dict(_dm) if isinstance(_dm, dict) else {}

    prov: dict[str, Any] = {
        "ozon_images": [_truncate_text(u) for u in _cap_list(flat.get("ozon_images"), _CAP_IMAGES)],
        "match_1688_images": [_truncate_text(u) for u in _cap_list(
            flat.get("match_1688_images"), _CAP_IMAGES)],
        "competing_seller_list": _cap_list(flat.get("competing_seller_list"), _CAP_SELLERS),
        "source_chain": _cap_list(flat.get("source_chain"), _CAP_SELLERS),
        "page_truth": {
            "category_path": _truncate_text(flat.get("page_category_path") or ""),
            "web_category_id": _truncate_text(flat.get("page_web_category_id") or ""),
        },
        "chain_depth": flat.get("chain_depth", 0),
        "seed_category_id": flat.get("_seed_category_id", flat.get("seed_category_id", 0)),
        "session_run_id": flat.get("session_run_id", "") or "",
    }
    grouped["provenance"] = prov
    return grouped


def flatten_candidate(grouped: dict) -> dict:
    """canonical 分组结构 → 扁平候选 dict（``ProductCandidate(**flat)`` 可还原）。

    group_candidate 的确定性逆（raw 证据从 provenance 取回），用于
    ``load_latest_discovery`` / ``--sync-sessions`` 投影。
    """
    if not isinstance(grouped, dict):
        return {}
    flat: dict[str, Any] = {}
    for group_name in ("id", "ozon", "metrics", "competition", "match_1688", "pricing"):
        grp = grouped.get(group_name)
        if isinstance(grp, dict):
            flat.update(grp)
    flat["status"] = grouped.get("status") or ""
    flat["error"] = grouped.get("status_reason") or ""
    dm = grouped.get("discovery_meta")
    if isinstance(dm, dict) and dm:
        flat["discovery_meta"] = dm
    prov = grouped.get("provenance") or {}
    if isinstance(prov, dict):
        if prov.get("ozon_images"):
            flat["ozon_images"] = list(prov.get("ozon_images") or [])
        if prov.get("match_1688_images"):
            flat["match_1688_images"] = list(prov.get("match_1688_images") or [])
        if prov.get("competing_seller_list"):
            flat["competing_seller_list"] = list(prov.get("competing_seller_list") or [])
        if prov.get("source_chain"):
            flat["source_chain"] = list(prov.get("source_chain") or [])
        page = prov.get("page_truth") or {}
        if isinstance(page, dict):
            if page.get("category_path"):
                flat["page_category_path"] = page.get("category_path")
            if page.get("web_category_id"):
                flat["page_web_category_id"] = page.get("web_category_id")
        if prov.get("chain_depth"):
            flat["chain_depth"] = prov.get("chain_depth")
        if prov.get("seed_category_id"):
            flat["_seed_category_id"] = prov.get("seed_category_id")
        if prov.get("session_run_id"):
            flat["session_run_id"] = prov.get("session_run_id")
    return flat


# ─────────────────────────────────────────────────────────────────────────
# session 文档构造 / 落盘
# ─────────────────────────────────────────────────────────────────────────

def build_summary(flat_candidates: list[dict]) -> dict:
    """summary：总数 + 状态分布 + 关键计数（profitable/有货源）。"""
    dist: dict[str, int] = {}
    for c in flat_candidates:
        st = str(c.get("status") or "unknown")
        dist[st] = dist.get(st, 0) + 1
    matched = sum(1 for c in flat_candidates if c.get("match_1688_url"))
    return {
        "total": len(flat_candidates),
        "status_distribution": dist,
        "matched": matched,
        "profitable": dist.get("profitable", 0),
    }


def build_session_document(flat_candidates: list[dict], *,
                           session: dict | None = None) -> dict:
    """扁平候选 dict 列表 → 自包含 canonical session 文档。"""
    sess = session or _CURRENT or {}
    entry = dict(sess.get("entry") or {})
    env = dict(sess.get("env") or {})
    # env 真值回填：analytics_logged_in（是否有 seller 后台运营数据）+
    # commission_source（候选佣金来源集合）。
    if flat_candidates:
        env["analytics_logged_in"] = any(
            bool(c.get("has_analytics")) for c in flat_candidates)
        srcs = sorted({str(c.get("commission_source")) for c in flat_candidates
                       if c.get("commission_source")})
        if srcs:
            env["commission_source"] = ",".join(srcs)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "session_run_id": str(sess.get("session_run_id") or ""),
        "created_at": str(sess.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S")),
        "entry": entry,
        "params": dict(sess.get("params") or {}),
        "env": env,
        "candidates": [group_candidate(c) for c in flat_candidates],
        "summary": build_summary(flat_candidates),
    }
    return doc


def _index_entry(doc: dict) -> dict:
    entry = doc.get("entry") or {}
    summary = doc.get("summary") or {}
    return {
        "session_run_id": doc.get("session_run_id", ""),
        "schema_version": doc.get("schema_version", SCHEMA_VERSION),
        "created_at": doc.get("created_at", ""),
        "entry": {"kind": entry.get("kind", ""), "keyword": entry.get("keyword", ""),
                  "url": entry.get("url", "")},
        "summary": summary,
    }


def _update_index(doc: dict) -> None:
    """index.jsonl 幂等 upsert（同 run_id 只保留一行——预匹配/后匹配两次落盘不重复）。

    锁外读取+锁内写；损坏行静默跳过（fail-open，绝不阻断选品主流程）。
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    lock = None
    try:
        from scripts.lib.lock_utils import try_acquire

        lock = try_acquire(_INDEX_LOCK, timeout=5.0)
    except Exception:
        lock = None
    try:
        lines: list[dict] = []
        if INDEX_PATH.exists():
            for raw in INDEX_PATH.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if row.get("session_run_id") != doc.get("session_run_id"):
                    lines.append(row)
        lines.append(_index_entry(doc))
        tmp = INDEX_PATH.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in lines) + "\n",
            encoding="utf-8")
        os.replace(tmp, INDEX_PATH)
    finally:
        if lock is not None:
            try:
                lock.close()
            except Exception:
                pass


def save_session(doc: dict) -> Path | None:
    """落盘 ``sessions/{run_id}.json`` + upsert index（同 run_id 覆盖，原子写）。"""
    run_id = str(doc.get("session_run_id") or "")
    if not is_valid_run_id(run_id):
        logger.warning("session 落盘跳过：非法 run_id %r", run_id)
        return None
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSIONS_DIR / f"{run_id}.json"
        tmp = SESSIONS_DIR / f".{run_id}.json.tmp"
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        _update_index(doc)
        return path
    except Exception as exc:
        logger.error("session 落盘失败: %s", exc)
        return None


def load_session(run_id: str) -> dict | None:
    if not is_valid_run_id(run_id):
        return None
    path = SESSIONS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("session 读取失败 %s: %s", path, exc)
        return None


def session_files() -> list[Path]:
    if not SESSIONS_DIR.exists():
        return []
    return sorted(SESSIONS_DIR.glob("disc_*.json"))


def latest_session_mtime() -> float:
    """最新 session 文件的 mtime（无 → 0.0），供 load_latest_discovery 择新。"""
    files = session_files()
    if not files:
        return 0.0
    try:
        return max(p.stat().st_mtime for p in files)
    except OSError:
        return 0.0


def latest_session_doc() -> dict | None:
    """最新的 session 文档（按 mtime；无 → None）。"""
    files = session_files()
    if not files:
        return None
    try:
        newest = max(files, key=lambda p: p.stat().st_mtime)
        return json.loads(newest.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_latest_candidates() -> list[dict]:
    """最新 session 的候选扁平 dict 列表（batch_test 复用 / load_latest_discovery）。"""
    doc = latest_session_doc()
    if not doc:
        return []
    return [flatten_candidate(g) for g in (doc.get("candidates") or [])
            if isinstance(g, dict)]


# ─────────────────────────────────────────────────────────────────────────
# 上报（canonical + 幂等补传）
# ─────────────────────────────────────────────────────────────────────────

# 上报投影白名单（与 ozon_discovery.REPORT_FIELDS 同源；此处兜底防御）。
_DEFAULT_REPORT_FIELDS: tuple[str, ...] = ()


def _project_rows(flat_rows: list[dict], report_fields: tuple[str, ...]) -> list[dict]:
    """扁平候选 → 上报投影（白名单字段 + 派生 ozon_image 单键）。"""
    rows = []
    for row in flat_rows:
        if str(row.get("status") or "") not in ("ok", "matched", "profitable"):
            continue
        out = {}
        for k in report_fields:
            if k not in row:
                continue
            val = row.get(k)
            if val is None:
                continue
            if k == "match_category_divergent" and val is not True:
                continue
            out[k] = val
        imgs = row.get("ozon_images") or []
        if imgs:
            out["ozon_image"] = imgs[0]
        rows.append(out)
    return rows


def build_report_payload(session_doc: dict, token: str,
                         report_fields: tuple[str, ...], *,
                         keyword: str | None = None,
                         filters: dict | None = None) -> dict:
    """canonical 上报请求体：session_run_id/schema_version/session_json + 投影 candidates。"""
    entry = session_doc.get("entry") or {}
    flat_rows = [flatten_candidate(g) for g in (session_doc.get("candidates") or [])
                 if isinstance(g, dict)]
    return {
        "token": token,
        "keyword": keyword if keyword is not None else entry.get("keyword", ""),
        "filters": filters if filters is not None else (session_doc.get("params") or {}),
        "candidates": _project_rows(flat_rows, report_fields),
        "session_run_id": session_doc.get("session_run_id", ""),
        "schema_version": session_doc.get("schema_version", SCHEMA_VERSION),
        "session_json": session_doc,
    }


def _reported_marker(run_id: str) -> Path:
    return SESSIONS_DIR / f"{run_id}.reported"


def mark_reported(run_id: str) -> None:
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        _reported_marker(run_id).write_text(
            time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    except Exception:
        pass


def is_reported(run_id: str) -> bool:
    return _reported_marker(run_id).exists()


def pending_sessions() -> list[str]:
    """未上报（无 .reported sidecar）的 session run_id 列表（新→旧）。"""
    out = []
    for fp in reversed(session_files()):
        rid = fp.stem
        if not is_reported(rid):
            out.append(rid)
    return out


def post_session_report(session_doc: dict, token: str,
                        report_fields: tuple[str, ...], *,
                        keyword: str | None = None,
                        filters: dict | None = None,
                        timeout: float = 20.0) -> bool:
    """POST canonical 上报（单次）。成功（<300）→ True，并打 .reported sidecar。"""
    try:
        import requests as _req

        from scripts._const import CLOUD_API_BASE
    except Exception:
        return False
    payload = build_report_payload(session_doc, token, report_fields,
                                   keyword=keyword, filters=filters)
    try:
        resp = _req.post(f"{CLOUD_API_BASE}/api/v1/discovery/runs",
                         json=payload, timeout=timeout)
    except Exception as exc:
        logger.warning("session 上报异常: %s", exc)
        return False
    if resp.status_code >= 300:
        logger.warning("session 上报失败: HTTP %s", resp.status_code)
        return False
    rid = str(session_doc.get("session_run_id") or "")
    if rid:
        mark_reported(rid)
    return True


def sync_sessions(report_fields: tuple[str, ...],
                  token_getter: Callable[[], str], *,
                  limit: int = 50) -> dict:
    """``--sync-sessions``：扫描未上报 session 逐个重传（幂等安全）。

    worker 端按 session_run_id upsert，重复上报只覆盖不新增；无 token/无待传
    → 直接返回。返回 ``{scanned, synced, failed, reason}``。
    """
    token = ""
    try:
        token = token_getter() or ""
    except Exception:
        token = ""
    pending = pending_sessions()
    result = {"scanned": len(pending), "synced": 0, "failed": 0, "reason": ""}
    if not token:
        result["reason"] = "no_token"
        return result
    if not pending:
        return result
    for rid in pending[:max(1, limit)]:
        doc = load_session(rid)
        if not doc:
            mark_reported(rid)  # 损坏/缺失 → 标记，避免每轮重复扫
            continue
        if post_session_report(doc, token, report_fields):
            result["synced"] += 1
        else:
            result["failed"] += 1
    return result
