#!/usr/bin/env python3
"""依赖方向立法（W3a 治理）——包级 import 方向的 CI 硬闸。

背景：utils 曾装下 HTTP 客户端/DB 单例/检索/定价核/任务编排器（task_processor
模块级 import graphs，被 23 模块反向引用）；main.py 4081 行 God module 被 30+
文件懒导入取鉴权/限流，循环依赖全靠懒导入注释续命。依赖方向既无法检查也无法
解释——本文件把法立起来。

法律（AST 扫 worker/src 全部 .py 的 Import/ImportFrom，含函数内懒导入）：

  R1 硬禁（今天已零违规，新增即红）：
     utils    ↛ graphs / api / routes / mcp_server / orchestrator
     services ↛ graphs / routes / mcp_server
     graphs   ↛ routes / main / mcp_server
     （utils = 最底层；orchestrator = 依赖 DAG 顶端唯一可 import graphs 的包）

  R2 棘轮（W3b composition root 拆解时只减不增，计数取自立法当日实盘）：
     utils→services ≤5 · utils→main ≤2 · services→main ≤6 · services→api ≤2
     services→orchestrator ≤1 · graphs→services ≤4 · graphs→api ≤2 · api→main ≤2
     <root>→main ≤2

  R3 文件冻结：import main 的文件集合封顶（新文件取鉴权/限流必须走
     api/deps_tenant 或 W3b 拆出的 api/security，不得新增 main 消费方）。

修法指引：新增跨包需求时向下依赖（utils ← services ← graphs ← orchestrator），
不要向上；确需向上 = 架构问题，先讨论再动法律。

运行: cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_import_direction.py -q
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _import_edges() -> dict[tuple[str, str], int]:
    """返回 {(所在包, 目标根包): import 语句数}；<root> = main.py/mcp_server.py 等顶层模块。"""
    stats: dict[tuple[str, str], int] = {}
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).as_posix()
        pkg = rel.split("/")[0] if "/" in rel else "<root>"
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for m in mods:
                root = m.split(".")[0]
                stats[(pkg, root)] = stats.get((pkg, root), 0) + 1
    return stats


def _files_importing_main() -> set[str]:
    out: set[str] = set()
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).as_posix()
        if rel == "main.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "main" or a.name.startswith("main.") for a in node.names):
                out.add(rel)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and node.module.split(".")[0] == "main":
                out.add(rel)
    return out


# ── R2 棘轮基线（2026-10 W3a 立法日实盘；W3b 收口后下调，禁止上调）──
RATCHET_BASELINE: dict[tuple[str, str], int] = {
    ("utils", "services"): 5,
    ("utils", "main"): 2,
    ("services", "main"): 6,
    ("services", "api"): 2,
    ("services", "orchestrator"): 1,
    ("graphs", "services"): 4,
    ("graphs", "api"): 2,
    ("api", "main"): 2,
    ("<root>", "main"): 2,
}

# ── R3 文件冻结基线（同日实盘；只减不增）──
MAIN_IMPORTER_FILES_FROZEN = {
    "mcp_server.py",
    "api/deps_tenant.py",
    "utils/progress_logger.py",
    "orchestrator/task_processor.py",
    "services/draft_service.py",
    "services/admin_service.py",
    "services/image_service.py",
    "services/mxou_login_service.py",
    "routes/admin_site_routes.py",
    "routes/settings_routes.py",
    "routes/image_tasks_routes.py",
    "routes/dashboard_routes.py",
    "routes/admin_routes.py",
    "routes/products_routes.py",
    "routes/admin_audit_routes.py",
    "routes/shelf_routes.py",
    "routes/admin_categories_routes.py",
    "routes/templates_routes.py",
    "routes/admin_queries_routes.py",
    "routes/images_routes.py",
    "routes/seo_keywords_routes.py",
    "routes/admin_config_routes.py",
    "routes/analytics_routes.py",
    "routes/admin_logistics_routes.py",
    "routes/store_actions_routes.py",
    "routes/credentials_routes.py",
    "routes/mxou_routes.py",
    "routes/drafts_routes.py",
    "routes/store_sync_routes.py",
    "routes/tasks_routes.py",
    "routes/orders_routes.py",
    "routes/source_candidates_routes.py",
    "routes/admin_data_sources_routes.py",
    "routes/estimate_routes.py",
}


def test_r1_utils_purity():
    """utils 是最底层：禁止 upward import（硬零）。"""
    stats = _import_edges()
    forbidden = ("graphs", "api", "routes", "mcp_server", "orchestrator")
    violations = [
        f"utils → {root} ×{n}（utils 不得依赖任何高层包；需求放 services 层）"
        for (pkg, root), n in sorted(stats.items())
        if pkg == "utils" and root in forbidden and n > 0
    ]
    assert not violations, "依赖方向违规（R1）：\n" + "\n".join(violations)


def test_r1_services_no_graphs():
    """services 禁止 import graphs/nodes/routes/mcp_server（硬零；api.schemas 允许）。"""
    stats = _import_edges()
    forbidden = ("graphs", "routes", "mcp_server")
    violations = [
        f"services → {root} ×{n}（services 在 graphs 下层）"
        for (pkg, root), n in sorted(stats.items())
        if pkg == "services" and root in forbidden and n > 0
    ]
    assert not violations, "依赖方向违规（R1）：\n" + "\n".join(violations)


def test_r1_graphs_no_routes_main():
    """graphs 禁止 import routes/main/mcp_server（硬零；api.errors/api.schemas 允许）。"""
    stats = _import_edges()
    forbidden = ("routes", "main", "mcp_server")
    violations = [
        f"graphs → {root} ×{n}（图层不得依赖路由/入口装配层）"
        for (pkg, root), n in sorted(stats.items())
        if pkg == "graphs" and root in forbidden and n > 0
    ]
    assert not violations, "依赖方向违规（R1）：\n" + "\n".join(violations)


def test_r2_ratchet_counts_only_shrink():
    """棘轮：跨包懒导入计数只减不增（W3b composition root 拆解的量化目标）。"""
    stats = _import_edges()
    regressed = []
    for edge, cap in sorted(RATCHET_BASELINE.items()):
        actual = stats.get(edge, 0)
        if actual > cap:
            regressed.append(f"{edge[0]} → {edge[1]}: {actual} > 基线 {cap}")
    assert not regressed, (
        "依赖方向棘轮回退（R2）——新需求向下依赖，不要新增 upward import：\n"
        + "\n".join(regressed)
    )


def test_r3_main_importer_files_frozen():
    """import main 的文件集合冻结——新文件禁止成为 main 消费方（W3b 收口目标=清零）。"""
    actual = _files_importing_main()
    new_files = sorted(actual - MAIN_IMPORTER_FILES_FROZEN)
    assert not new_files, (
        "新文件 import main（R3）——鉴权/限流/DB 单例走 api/deps_tenant，"
        f"或等 W3b 拆出 api/security；新增消费方文件: {new_files}"
    )
