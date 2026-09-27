"""v0.80 进度映射收口（arch-findings #2：进度条从高位跳回 0%）。

三段锁定：
1. STAGE_ORDER 拓扑序重排（集合等价 + 精确顺序）；
2. _NODE_STAGE_MAP 对 graphs.graph 全部节点名覆盖（新增节点漏登记即红——
   漏登记时 ProgressCallback 回落裸节点名，update_progress 视为未知阶段）；
3. update_progress / ProgressCallback 防倒退语义：
   - 未知节点名不回退百分比（保留上一阶段，只刷 message）；
   - 已知阶段展示序回跳被钳制（pricing 后 assemble→category_match 不降）；
   - auth 是重试重跑的重置哨兵（否则重试整轮钉在旧高位）；
   - on_chain_error("error") 旁路调用同样不归零。
终态归位（completed/failed/rejected 覆盖 progress）不走 update_progress，
由 http_task_status 负责，不在本文件断言范围。
纯 mock：task_progress_service.emit 打桩，零 DB 依赖。
"""
import pytest

# STAGE_ORDER 的 13 个阶段（集合不变量：仅展示序重排，阶段成员永不变）
_STAGE_SET = {
    "auth", "check_quota", "ingest", "category_match", "pricing",
    "attributes", "description", "image_generation", "prepare_ozon_upload",
    "ozon_validate", "ozon_upload", "ozon_status", "learning_record",
}

# v0.80 前缺失、导致进度倒退的 8 个节点（回归钉：删映射即红）
_PREVIOUSLY_MISSING = [
    "assemble_ozon_product", "scene_generation_llm", "visual_vars_llm",
    "check_quota", "fetch_back", "validation_retry_wrapper",
    "follow_sell_import", "variant_primary_loop",
]


def _pct(idx: int, total: int = 13) -> int:
    return int((idx / total) * 100)


@pytest.fixture()
def progress_env(monkeypatch):
    """干净进度环境：清内存进度 + 打桩 emit（防本地 PG 污染/连接等待）。"""
    import main as main_mod

    emitted = []

    def _fake_emit(task_id, stage, step="", status="progress", message="", detail=None):
        emitted.append((task_id, stage, message))
        return 1

    monkeypatch.setattr("services.task_progress_service.emit", _fake_emit)
    keys_before = set(main_mod._task_progress.keys())
    yield main_mod, emitted
    # 还原本用例写入的键（singleflight 教训：测试间不得互相污染）
    for k in list(main_mod._task_progress.keys()):
        if k not in keys_before:
            main_mod._task_progress.pop(k, None)


# ============================================================
# 1. STAGE_ORDER：集合等价 + 拓扑展示序
# ============================================================

def test_stage_order_set_equivalence(progress_env):
    main_mod, _ = progress_env
    assert len(main_mod.STAGE_ORDER) == 13
    assert set(main_mod.STAGE_ORDER) == _STAGE_SET
    assert len(set(main_mod.STAGE_ORDER)) == 13, "阶段不得重复"


def test_stage_order_topology_sequence(progress_env):
    """展示序对齐真实拓扑：check_quota 是 auth 后第二跳（graph.py 路由实证）。"""
    main_mod, _ = progress_env
    assert main_mod.STAGE_ORDER == [
        "auth", "check_quota", "ingest", "category_match", "pricing",
        "attributes", "description", "image_generation", "prepare_ozon_upload",
        "ozon_validate", "ozon_upload", "ozon_status", "learning_record",
    ]


# ============================================================
# 2. _NODE_STAGE_MAP：覆盖 graphs.graph 全部节点
# ============================================================

def test_node_stage_map_covers_all_graph_nodes():
    """反射 builder 注册表：每个节点的解析阶段必须落在 STAGE_ORDER 内。

    覆盖口径与 ProgressCallback 一致：`_NODE_STAGE_MAP.get(name, name)`——
    同名阶段节点（auth/pricing 等）可走回落，其余必须显式登记。
    """
    from graphs.graph import builder
    from utils.task_processor import _NODE_STAGE_MAP

    import main as main_mod

    node_names = {str(k) for k in builder.nodes}
    node_names = {n for n in node_names if not n.startswith("__")}
    assert len(node_names) >= 25, f"graph 节点数异常（拓扑大改？）: {sorted(node_names)}"
    uncovered = []
    for name in sorted(node_names):
        stage = _NODE_STAGE_MAP.get(name, name)
        if stage not in main_mod.STAGE_ORDER:
            uncovered.append(f"{name} -> {stage}")
    assert not uncovered, (
        "以下 graph 节点未映射到 STAGE_ORDER 阶段（新增节点必须登记 "
        f"_NODE_STAGE_MAP，否则进度归 0 倒退）: {uncovered}"
    )


def test_node_stage_map_previously_missing_nodes_pinned():
    from utils.task_processor import _NODE_STAGE_MAP

    for name in _PREVIOUSLY_MISSING:
        assert name in _NODE_STAGE_MAP, f"回归：{name} 曾因漏映射致进度跳 0%"
    # 语义就近映射钉死（改映射须同步改本断言并复核展示效果）
    assert _NODE_STAGE_MAP["assemble_ozon_product"] == "category_match"
    assert _NODE_STAGE_MAP["scene_generation_llm"] == "description"
    assert _NODE_STAGE_MAP["visual_vars_llm"] == "description"
    assert _NODE_STAGE_MAP["variant_primary_loop"] == "image_generation"
    assert _NODE_STAGE_MAP["check_quota"] == "check_quota"
    assert _NODE_STAGE_MAP["fetch_back"] == "ozon_status"
    assert _NODE_STAGE_MAP["validation_retry_wrapper"] == "ozon_status"
    assert _NODE_STAGE_MAP["follow_sell_import"] == "ingest"


# ============================================================
# 3. update_progress / ProgressCallback 防倒退语义
# ============================================================

def test_unknown_node_does_not_regress_percent(progress_env):
    """核心修复断言：未知节点名回调不把百分比打回 0%（旧行为必现倒退）。"""
    main_mod, emitted = progress_env
    from utils.task_processor import ProgressCallback

    task = "t-unknown-node"
    cb = ProgressCallback(task, main_mod.update_progress)
    cb.on_chain_start({"name": "main_image_gen"}, {})  # image_generation，高位
    hi = main_mod._task_progress[task]
    assert hi["stage"] == "image_generation"
    assert hi["percent"] == _pct(7)

    # 未来新增/漏登记节点（stage 不在 STAGE_ORDER）→ 保留阶段与百分比
    cb.on_chain_start({"name": "brand_new_future_node"}, {})
    now = main_mod._task_progress[task]
    assert now["percent"] == hi["percent"], "未知节点不得回退百分比"
    assert now["stage_index"] == hi["stage_index"]
    assert now["stage"] == "image_generation"
    assert now["message"] == "执行 brand_new_future_node..."  # message 照常刷新
    assert emitted, "进度事件时间线照常 emit"


def test_known_stage_display_regression_clamped(progress_env):
    """展示序与真实拓扑局部错位（pricing 后 assemble→category_match）不降百分比。"""
    main_mod, _ = progress_env
    from utils.task_processor import ProgressCallback

    task = "t-clamp"
    cb = ProgressCallback(task, main_mod.update_progress)
    cb.on_chain_start({"name": "pricing"}, {})  # idx 4
    at_pricing = main_mod._task_progress[task]
    assert at_pricing["percent"] == _pct(4)

    # assemble 实际在 pricing 之后执行，映射阶段 category_match 展示序在前
    cb.on_chain_start({"name": "assemble_ozon_product"}, {})
    now = main_mod._task_progress[task]
    assert now["percent"] == _pct(4), "已知阶段展示序回跳应被钳制"
    assert now["stage"] == "pricing", "stage 标签跟随钳后下标保持自洽"
    assert now["stages_completed"] == main_mod.STAGE_ORDER[:4]
    assert now["message"] == "执行 assemble_ozon_product..."

    # 正常前进不受钳制影响
    cb.on_chain_start({"name": "scene_generation_llm"}, {})  # description idx 6
    assert main_mod._task_progress[task]["percent"] == _pct(6)


def test_auth_resets_baseline_for_retry(progress_env):
    """auth 是图唯一入口=重试重跑哨兵：允许基线归零，否则重试整轮钉在旧高位。"""
    main_mod, _ = progress_env
    from utils.task_processor import ProgressCallback

    task = "t-retry"
    cb = ProgressCallback(task, main_mod.update_progress)
    cb.on_chain_start({"name": "learning_record"}, {})  # 上一轮末段 idx 12
    assert main_mod._task_progress[task]["percent"] == _pct(12)

    cb.on_chain_start({"name": "auth"}, {})  # 重试新一轮
    now = main_mod._task_progress[task]
    assert now["stage"] == "auth"
    assert now["percent"] == 0, "auth 哨兵应重置重试基线"


def test_on_chain_error_keeps_progress(progress_env):
    """on_chain_error 旁路 stage="error" 同样不归零（旧行为会把进度打回 0%）。"""
    main_mod, _ = progress_env
    from utils.task_processor import ProgressCallback

    task = "t-error"
    cb = ProgressCallback(task, main_mod.update_progress)
    cb.on_chain_start({"name": "prepare_ozon_upload"}, {})
    hi = main_mod._task_progress[task]
    assert hi["percent"] == _pct(8)

    cb.on_chain_error(RuntimeError("node boom"), run_id="r1")
    now = main_mod._task_progress[task]
    assert now["percent"] == hi["percent"], "error 旁路不得回退百分比"
    assert now["stage"] == "prepare_ozon_upload"
