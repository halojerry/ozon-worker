"""类目树刷新脚本（PLAN-category-tree-refresh-v1，F-B03 收口）。

用 Ozon Seller API 全量拉取现役类目树（每语言一次调用），刷新 category_tree_nodes：
  1. POST /v1/description-category/tree {"language": lang}（走 ozon_post：限流/重试/类型化错误）
  2. 原始树落 category_cache JSONB（审计/回滚快照）
  3. sync_category_tree_nodes 全量 upsert（advisory lock 防并发）
  4. 消失节点软失效：新树没有的 type 节点置 disabled=true（不物理删除——
     L0/category_mapping 的历史 dc/tp 引用需要可识别失效，物理删除会让 join 静默丢失）
  5. 差异报告：打印 + 落 data/category_tree_refresh_<ts>.json（含 L0 影响面预警）

用法：
    export OZON_CLIENT_ID=<id> OZON_API_KEY=<key>
    python scripts/refresh_category_tree.py --dry-run              # 拉树+差异报告，不落库
    python scripts/refresh_category_tree.py                        # 全量刷新（RU+ZH_HANS）
    python scripts/refresh_category_tree.py --languages RU,ZH_HANS

幂等：连跑两次第二次零变更。全程 2 次 API 调用，分钟级。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.logger import get_logger  # noqa: E402

logger = get_logger("refresh_category_tree")

DEFAULT_LANGUAGES = ["RU", "ZH_HANS"]
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


# ── 纯函数（单测覆盖）──

def flatten_tree_keys(tree_result: list[dict]) -> set[tuple[int, int]]:
    """遍历 Ozon 树，收集 type 节点的 (description_category_id, type_id) 键集。

    软失效只对 type 节点比较——消费方（路径精配/jieba/L0）全部以 type 节点为准，
    category 中间节点随树形状变化大且不被单独引用。叶子继承父级 dc（Ozon 叶子
    节点可能只带 type_id 不带 description_category_id）。
    """
    keys: set[tuple[int, int]] = set()

    def _walk(children: list[dict], parent_dc: int) -> None:
        for node in children:
            dc = int(node.get("description_category_id") or 0) or parent_dc
            type_id = node.get("type_id")
            if type_id:
                keys.add((dc, int(type_id)))
            sub = node.get("children") or []
            if sub:
                _walk(sub, dc)

    for top in tree_result or []:
        dc = int(top.get("description_category_id") or 0)
        type_id = top.get("type_id")
        if type_id:
            keys.add((dc, int(type_id)))
        sub = top.get("children") or []
        if sub:
            _walk(sub, dc)
    return keys


def compute_diff(old_keys: set[tuple[int, int]],
                 new_keys: set[tuple[int, int]]) -> tuple[set, set]:
    """返回 (added, removed)：新增/消失的 (dc, tp) 集合。"""
    return new_keys - old_keys, old_keys - new_keys


def keys_to_disable(old_keys: set[tuple[int, int]],
                    new_keys: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """待软失效键 = 旧有而新树没有（消失）且不已 disabled 的行由 SQL 侧过滤，这里给全集。"""
    return sorted(old_keys - new_keys)


def load_existing_keys(engine, language: str) -> set[tuple[int, int]]:
    """读表中现役 type 节点键集（含 disabled=true 的行——它们若在新树会被 upsert 复活）。"""
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT description_category_id, type_id FROM category_tree_nodes "
            "WHERE language = :lang AND node_type = 'type' AND type_id IS NOT NULL"
        ), {"lang": language}).fetchall()
    return {(int(r[0]), int(r[1])) for r in rows}


# ── 主流程 ─=

def fetch_tree(client_id: str, api_key: str, language: str) -> list[dict]:
    """拉取单语言全量类目树，返回 result 数组。失败抛 OzonError（类型化错误上抛）。"""
    from utils.ozon_client import ozon_post

    logger.info("拉取 %s 类目树（/v1/description-category/tree）...", language)
    data = ozon_post(client_id, api_key, "/v1/description-category/tree",
                     {"language": language}, timeout=120)
    result = data.get("result") or []
    if not result:
        raise RuntimeError(f"{language} 树响应为空")
    logger.info("%s 树拉取成功：%d 个顶层类目", language, len(result))
    return result


def count_mapping_refs(engine, removed_keys: list[tuple[int, int]]) -> int:
    """category_mapping 中引用了消失 (dc,tp) 的行数（L0 影响面预警）。"""
    if not removed_keys:
        return 0
    from sqlalchemy import text

    total = 0
    with engine.connect() as conn:
        for i in range(0, len(removed_keys), 500):
            batch = removed_keys[i:i + 500]
            values = ", ".join(
                f"(:dc{j}, :tp{j})" for j in range(len(batch)))
            params: dict[str, Any] = {}
            for j, (dc, tp) in enumerate(batch):
                params[f"dc{j}"] = dc
                params[f"tp{j}"] = tp
            row = conn.execute(text(
                f"SELECT COUNT(*) FROM category_mapping cm "
                f"JOIN (VALUES {values}) AS k(dc, tp) "
                f"ON cm.description_category_id = k.dc AND cm.type_id = k.tp "
                f"WHERE cm.is_active = true"
            ), params).scalar()
            total += int(row or 0)
    return total


def soft_disable_missing(engine, language: str,
                         removed_keys: list[tuple[int, int]]) -> int:
    """消失节点软失效：disabled=true（保留行供 L0/映射失效识别）。返回失效数。"""
    if not removed_keys:
        return 0
    from sqlalchemy import text

    disabled = 0
    with engine.begin() as conn:
        for i in range(0, len(removed_keys), 500):
            batch = removed_keys[i:i + 500]
            values = ", ".join(f"(:dc{j}, :tp{j})" for j in range(len(batch)))
            params: dict[str, Any] = {"lang": language}
            for j, (dc, tp) in enumerate(batch):
                params[f"dc{j}"] = dc
                params[f"tp{j}"] = tp
            res = conn.execute(text(
                f"UPDATE category_tree_nodes t SET disabled = true "
                f"FROM (VALUES {values}) AS k(dc, tp) "
                f"WHERE t.language = :lang AND t.node_type = 'type' "
                f"  AND t.description_category_id = k.dc AND t.type_id = k.tp "
                f"  AND t.disabled = false"
            ), params)
            disabled += res.rowcount or 0
    return disabled


def refresh_language(engine, client_id: str, api_key: str, language: str,
                     dry_run: bool) -> dict[str, Any]:
    """单语言刷新：拉树 → diff → （非 dry-run）快照 + sync + 软失效。返回报告片段。"""
    from utils.local_db_manager import LocalDBManager
    from utils.ozon_category_query import get_category_query

    tree = fetch_tree(client_id, api_key, language)
    new_keys = flatten_tree_keys(tree)
    old_keys = load_existing_keys(engine, language)
    added, removed_sets = compute_diff(old_keys, new_keys)
    removed = keys_to_disable(old_keys, new_keys)
    logger.info("[%s] 节点数 %d → %d（type 键）；新增 %d，消失 %d",
                language, len(old_keys), len(new_keys), len(added), len(removed))

    report: dict[str, Any] = {
        "language": language,
        "old_type_nodes": len(old_keys),
        "new_type_nodes": len(new_keys),
        "added": len(added),
        "removed": len(removed),
        "l0_refs_on_removed": 0,
        "applied": False,
    }

    if dry_run:
        report["l0_refs_on_removed"] = count_mapping_refs(engine, removed)
        return report

    # ① 原始树快照（审计/回滚）
    local_db = LocalDBManager()
    local_db.set_category_cache(client_id, {"result": tree}, language=language)
    # ② 全量 upsert（新树行恢复 disabled 值，含复活此前被软失效的类目）
    written = get_category_query().sync_category_tree_nodes(
        {"result": tree}, language, skip_if_nonempty=False)
    # ③ 消失节点软失效
    disabled = soft_disable_missing(engine, language, removed)
    report.update({
        "applied": True,
        "upserted": written,
        "soft_disabled": disabled,
        "l0_refs_on_removed": count_mapping_refs(engine, removed),
    })
    if report["l0_refs_on_removed"]:
        logger.warning(
            "[%s] ⚠️ %d 条激活 L0 映射引用了消失类目（建议人工复核 category_mapping）",
            language, report["l0_refs_on_removed"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Ozon 类目树刷新（双语全量）")
    parser.add_argument("--languages", default=",".join(DEFAULT_LANGUAGES),
                        help="逗号分隔语言（默认 RU,ZH_HANS）")
    parser.add_argument("--dry-run", action="store_true",
                        help="拉树+差异报告，不落库")
    args = parser.parse_args()

    client_id = os.getenv("OZON_CLIENT_ID_WARM", "") or os.getenv("OZON_CLIENT_ID", "")
    api_key = os.getenv("OZON_API_KEY_WARM", "") or os.getenv("OZON_API_KEY", "")
    if not client_id or not api_key:
        print("❌ 需要 Ozon 凭证：export OZON_CLIENT_ID=<id> OZON_API_KEY=<key>")
        return 2

    from storage.database.db import get_engine
    engine = get_engine()

    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    started = time.time()
    reports = []
    for lang in languages:
        try:
            reports.append(refresh_language(engine, client_id, api_key, lang,
                                            dry_run=args.dry_run))
        except Exception as exc:
            logger.error("[%s] 刷新失败：%s", lang, exc)
            reports.append({"language": lang, "error": str(exc)[:300]})

    report_doc = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": args.dry_run,
        "languages": reports,
        "elapsed_sec": round(time.time() - started, 1),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(
        DATA_DIR,
        f"category_tree_refresh_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report_doc, f, ensure_ascii=False, indent=2)
    print(json.dumps(report_doc, ensure_ascii=False, indent=2))
    print(f"📄 报告已落: {out}")
    failed = [r for r in reports if r.get("error")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
