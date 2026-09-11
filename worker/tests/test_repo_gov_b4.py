#!/usr/bin/env python3
"""仓库治理 B4（fix/repo-gov-b4）死代码处置回归测试。

依据 docs/audit/2026-09-11-repo-gov/A5-pipeline-deadcode-conflicts.md §2：
- T1(D-03)：utils/error_classifier.py 整模块删除（ErrorClassifier 实为
  runtime/helpers.py 另一实现；本模块 fix_path 指向已不存在的旧拓扑节点）
- T2(D-02)：graphs/state.py 旧 4 节点管线死模型 8 个删除
  （⚠️ VariantLoopOutput/VariantLoopState 是活的，必须保留）
- T3(D-04)：死配置 2 个删除（category_match_llm_cfg / product_assembly_cfg）
- T5(D-01)：TASK_NOT_CANCELLABLE 接线——cancel_task 对非 pending 任务
  由 200+{status:failed} 改返 409 统一错误信封（可编程处理）
- T6(D-05)：shelf bulk 3 死端点删除（唯一消费方是已归档 webui-archive；
  现行批量操作走 /stores/{id}/actions）——GET /products 与 /products/ozon 保留

纯 mock 无需 PG（T5 直接调用端点协程，不起 TestClient/lifespan）。
运行：cd worker && PYTHONPATH=src python tests/test_repo_gov_b4.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = WORKER_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

# main 导入前置 env（对齐 tests/test_categories_readonly_endpoints.py 的守卫集）
os.environ.setdefault("CREDENTIAL_MASTER_KEY", "0123456789abcdef0123456789abcdef")
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"
os.environ["SKIP_STORE_SYNC"] = "1"


# ============================================================
# T1 (D-03)：error_classifier 整模块已删 + 零回潮引用
# ============================================================

def test_error_classifier_module_removed():
    assert not (SRC_DIR / "utils" / "error_classifier.py").exists(), (
        "utils/error_classifier.py 应已删除（A5 §2 D-03）"
    )
    assert importlib.util.find_spec("utils.error_classifier") is None


def test_error_classifier_zero_references_in_worker_src():
    """worker/src 下不得再 import 已删的 utils.error_classifier 模块。
    ⚠️ main.py 里 self.error_classifier / error_classifier=... 是
    runtime/helpers.py 另一实现（ErrorClassifier）的属性名，不受影响、须放行。"""
    bad_tokens = (
        "utils.error_classifier",
        "utils import error_classifier",
        ".error_classifier import",
        "import error_classifier",
    )
    hits = [
        f"{p.relative_to(SRC_DIR)}:{i + 1}"
        for p in SRC_DIR.rglob("*.py")
        for i, line in enumerate(
            p.read_text(encoding="utf-8", errors="replace").splitlines())
        if any(tok in line for tok in bad_tokens)
    ]
    assert hits == [], f"worker/src 仍有 utils.error_classifier import: {hits}"


# ============================================================
# T2 (D-02)：state.py 死模型已删，活模型保留
# ============================================================

def test_state_dead_models_removed_and_live_models_kept():
    import graphs.state as gs

    dead = [
        "CategoryLookupInput", "CategoryLookupOutput",
        "AttributesFetchInput", "AttributesFetchOutput",
        "AttributesLLMInput", "AttributesLLMOutput",
        "AttributesLearningInput", "AttributesLearningOutput",
        "VariantLoopInput",  # variant_primary_loop 子图实际用 VariantLoopState
    ]
    alive = ["VariantLoopState", "VariantLoopOutput", "VariantPrimaryLoopOutput"]
    for name in dead:
        assert not hasattr(gs, name), f"state.py 死模型未删: {name}"
    for name in alive:
        assert hasattr(gs, name), f"state.py 活模型被误删: {name}"


# ============================================================
# T3 (D-04)：死配置已删，活配置仍在
# ============================================================

def test_dead_configs_removed_and_live_configs_kept():
    for name in ("category_match_llm_cfg.json", "product_assembly_cfg.json"):
        assert not (WORKER_DIR / "config" / name).exists(), f"死配置未删: {name}"
    for name in ("category_match_v2_cfg.json", "attributes_llm_cfg.json",
                 "scene_generation_llm_cfg.json", "visual_vars_llm_cfg.json"):
        assert (WORKER_DIR / "config" / name).exists(), f"误删活配置: {name}"


# ============================================================
# T5 (D-01)：cancel_task 不可取消 → 409 TASK_NOT_CANCELLABLE
# ============================================================

def _import_main():
    import main as main_mod
    return main_mod


class _FakeProcessor:
    """cancel_task 桩：返回值由 outcome 控制，记录调用。"""

    def __init__(self, outcome: bool):
        self.outcome = outcome
        self.calls: list[str] = []

    async def cancel_task(self, task_id: str) -> bool:
        self.calls.append(task_id)
        return self.outcome


def test_cancel_task_not_cancellable_returns_409(monkeypatch):
    from fastapi.responses import JSONResponse

    main_mod = _import_main()
    fake = _FakeProcessor(outcome=False)  # 非 pending（终态/运行中）
    monkeypatch.setattr(main_mod, "task_processor", fake)
    res = asyncio.run(main_mod.http_cancel_task("task-abc"))
    assert isinstance(res, JSONResponse), "不可取消应返回 error_response(JSONResponse)"
    assert res.status_code == 409
    import json as _json
    body = _json.loads(bytes(res.body))
    assert body["ok"] is False
    assert body["error_code"] == "TASK_NOT_CANCELLABLE"
    assert fake.calls == ["task-abc"]


def test_cancel_task_pending_still_succeeds(monkeypatch):
    main_mod = _import_main()
    fake = _FakeProcessor(outcome=True)
    monkeypatch.setattr(main_mod, "task_processor", fake)
    res = asyncio.run(main_mod.http_cancel_task("task-xyz"))
    assert isinstance(res, dict), "可取消路径保持原 dict 契约"
    assert res["status"] == "success"
    assert res["task_id"] == "task-xyz"


# ============================================================
# T6 (D-05)：shelf bulk 3 死端点已删，GET 两端点保留
# ============================================================

def test_shelf_bulk_endpoints_removed_list_endpoints_kept():
    main_mod = _import_main()
    paths = main_mod.app.openapi()["paths"]
    for path in ("/products/bulk-prices", "/products/bulk-stocks",
                 "/products/bulk-archive"):
        for prefix in ("", "/api/v1"):
            assert f"{prefix}{path}" not in paths, f"shelf 死端点未删: POST {path}"
    for path in ("/products", "/products/ozon"):
        assert (
            path in paths or f"/api/v1{path}" in paths
        ), f"shelf 保留端点缺失: GET {path}"


# ============================================================
# T9 (BL-31)：多 worker 误部署启动告警（warning-only）
# ============================================================

def test_multi_worker_env_triggers_warning(monkeypatch, caplog):
    main_mod = _import_main()
    import logging
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.delenv("WORKERS", raising=False)
    with caplog.at_level(logging.WARNING, logger="main"):
        main_mod._warn_if_multi_worker()
    assert any("WEB_CONCURRENCY=4" in r.message and "workers=1" in r.message
               for r in caplog.records), "WEB_CONCURRENCY>1 应触发 workers=1 告警"


def test_single_worker_env_no_warning(monkeypatch, caplog):
    main_mod = _import_main()
    import logging
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.delenv("WORKERS", raising=False)
    with caplog.at_level(logging.WARNING, logger="main"):
        main_mod._warn_if_multi_worker()
    assert not any("workers=1" in r.message for r in caplog.records), (
        "workers=1 部署不应告警"
    )


# ============================================================
# 独立运行入口（对齐仓内单文件测试风格；带 fixture 的用例请走 pytest）
# ============================================================

def _main() -> int:  # pragma: no cover
    import inspect
    failed = 0
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)
           and not inspect.signature(v).parameters]
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed"
          f"（cancel 409 两用例需经 pytest 运行）")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
