"""T7a: 生图工作台服务（WebUI 生图缓存版本化 + 强制重生成）。

- list_images: 该任务全部图片行（slot/version/url/params）
- regen_image: 强制重生成单槽位 → 节点 config 注入 force_regen + regen_version →
  绕过缓存读 → 写 version=prev+1 新行。节点入参从缓存 params 快照重建（与上次完全一致）。
- 租户隔离：任务必须属于调用者 tenant_id（local_dev 放行，对齐 resubmit 先例）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import requests
from fastapi import HTTPException
from sqlalchemy import text

from services import credential_service  # T14 凭证解密（非循环依赖）
from storage.database.db import get_engine
from utils import image_quality_evaluator, task_image_cache as tic
from utils.ozon_client import ozon_post

logger = logging.getLogger(__name__)

# slot → 生图节点（graph.py 硬编码节点名，C3b BLOCKER 1 冻结映射）
SLOT_TO_NODE = {
    "white_bg": "white_bg_gen",
    "multi_angle": "multi_angle_gen",
    "main": "main_image_gen",
    "detail": "detail_gen",
    "social_proof": "social_proof_gen",
    "comparison": "comparison_gen",
    "scene_1": "scene_1_gen",
    "scene_2": "scene_2_gen",
    "scene_3": "scene_3_gen",
}
# variant_{idx} → variant_primary_loop（循环节点，regen 全量重生成变体主图）
VARIANT_SLOT_PREFIX = "variant_"


def _get_task_tenant(task_id: str) -> Optional[str]:
    """任务归属租户（ozon_product_tasks.tenant_id）；任务不存在 → None。"""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT tenant_id FROM ozon_product_tasks WHERE id = :tid"),
                {"tid": task_id},
            ).fetchone()
        return str(row[0]) if row else None
    except Exception as e:
        logger.debug("image_service._get_task_tenant 失败(task=%s): %s", task_id, e)
        return None


def _ensure_task_tenant(task_id: str, tenant_id: str) -> None:
    """租户隔离：任务不存在/跨租户 → 404（防越权，对齐 resubmit v0.38.1）。"""
    task_tenant = _get_task_tenant(task_id)
    if task_tenant is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if task_tenant and tenant_id != "local_dev" and task_tenant != tenant_id:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")


def list_images(task_id: str, tenant_id: str) -> dict:
    """GET /tasks/{id}/images — 该任务全部图片行（含 params 快照）。"""
    _ensure_task_tenant(task_id, tenant_id)
    return {
        "task_id": task_id,
        "images": tic.list_images(task_id),
    }


def _resolve_node_id(slot: str) -> Optional[str]:
    if slot in SLOT_TO_NODE:
        return SLOT_TO_NODE[slot]
    if slot.startswith(VARIANT_SLOT_PREFIX):
        return "variant_primary_loop"
    return None


async def regen_image(
    task_id: str,
    slot: str,
    tenant_id: str,
    run_node: Optional[Callable[..., Any]] = None,
) -> dict:
    """POST /tasks/{id}/images/{slot}/regen — 强制重生成（version++ 新行）。

    - 节点入参 = 最新缓存行的 params 快照（与上次生成完全一致的 Input schema）
    - config 注入 force_regen=True + regen_version=prev+1 → 节点绕过缓存读 → save 新行
    - run_node 可注入（测试 mock）；默认懒导入 main.service.run_node（防循环导入）
    """
    _ensure_task_tenant(task_id, tenant_id)

    node_id = _resolve_node_id(slot)
    if node_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"未知槽位 {slot!r}（支持 {sorted(SLOT_TO_NODE)} + {VARIANT_SLOT_PREFIX}*）",
        )

    info = tic.get_image_info(task_id, slot)
    if not info:
        raise HTTPException(status_code=404, detail=f"槽位 {slot} 无缓存，无法重生成")
    prev_version = int(info["version"])
    next_version = prev_version + 1
    params = info.get("params") or {}

    if run_node is None:
        from main import service as _graph_service  # 延迟导入防循环
        run_node = _graph_service.run_node

    run_config = {
        "thread_id": task_id,          # 节点写缓存到当前任务
        "force_regen": True,           # 节点绕过缓存读（无静默命中）
        "regen_version": next_version,  # save 显式 version=prev+1
    }
    try:
        await run_node(node_id, params, extra_config=run_config)
    except Exception as e:
        logger.error("image_service.regen 节点执行失败(task=%s slot=%s): %s", task_id, slot, e)
        raise HTTPException(status_code=500, detail=f"重生成失败: {e}")

    new_info = tic.get_image_info(task_id, slot, version=next_version)
    if not new_info:
        raise HTTPException(status_code=500, detail="重生成未产生新版本行（节点可能短路/失败）")
    return new_info


# ────────────────────────────────────────────────────────────
# T14: 在线商品改图全量重传（POST /products/{product_id}/update_images）
# ────────────────────────────────────────────────────────────

# C1b: 索引行 UPSERT（回填/刷新商品↔任务↔店铺映射，approved 路径挂钩）
_INDEX_UPSERT_SQL = text(
    "INSERT INTO product_task_index (product_id, tenant_id, offer_id, task_id, credential_id) "
    "VALUES (:product_id, :tenant_id, :offer_id, :task_id, :credential_id) "
    "ON CONFLICT (product_id) DO UPDATE SET "
    "  offer_id = EXCLUDED.offer_id, task_id = EXCLUDED.task_id, "
    "  credential_id = EXCLUDED.credential_id, created_at = NOW()"
)


def _lookup_index(tenant_id: str, product_id: str) -> Optional[dict]:
    """product_task_index 定位（租户隔离）；无索引 → None（调用方转 404）。"""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT product_id, offer_id, task_id, credential_id FROM product_task_index "
                "WHERE product_id=:pid AND tenant_id=:tenant_id"
            ), {"pid": product_id, "tenant_id": tenant_id}).fetchone()
    except Exception as e:
        logger.warning("product_task_index 查询失败 product_id=%s: %s", product_id, e)
        return None
    if row is None:
        return None
    return {
        "product_id": str(row[0]),
        "offer_id": str(row[1]),
        "task_id": str(row[2]),
        "credential_id": str(row[3]) if row[3] is not None else None,
    }


def _upsert_index(tenant_id: str, product_id: str, offer_id: str, task_id: str, credential_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(_INDEX_UPSERT_SQL, {
            "product_id": product_id, "tenant_id": tenant_id, "offer_id": offer_id,
            "task_id": task_id, "credential_id": credential_id,
        })


def _mark_task_pending_moderation(task_id: str) -> None:
    """改图触发重新审核：原任务 status → pending_moderation（前端展示「重新审核中」）。"""
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE ozon_product_tasks SET status='pending_moderation', updated_at=NOW() WHERE id=:tid"
        ), {"tid": task_id})


def _query_ozon_status(client_id: str, api_key: str, product_id: str, post: Callable[..., Any]) -> str:
    """查询商品 ozon 审核状态（/v3/product/info/list → statuses.moderate_status）。

    最佳努力：任何失败按 "pending" 处理（重传已成功，审核状态查询失败不阻断响应）。
    """
    try:
        resp = post(
            client_id, api_key, "/v3/product/info/list",
            {"product_id": [int(product_id)], "seller_tag": []}, timeout=15,
        )
        items = (resp.get("result") or {}).get("items") or []
        if not items:
            return "pending"
        return str((items[0].get("statuses") or {}).get("moderate_status") or "pending")
    except Exception as e:  # 审核状态为辅助查询，fail-open 对齐 auth/balance 先例
        logger.warning("查询商品审核状态失败 product_id=%s: %s", product_id, e)
        return "pending"


def update_product_images(
    tenant_id: str,
    product_id: str,
    images: list[str],
    *,
    url_checker: Optional[Callable[[str], bool]] = None,
    ozon_post_fn: Optional[Callable[..., Any]] = None,
) -> dict:
    """POST /products/{product_id}/update_images — 在线商品改图全量重传。

    ① product_task_index 定位（task_id + credential_id）；无索引 → 404「商品未找到，可能已归档」
    ② URL 存活检查（GET+Range，复用 image_quality_evaluator）→ 死 URL 过滤
    ③ /v3/product/import 全量重传（product_id + offer_id + 新 images）
    ④ ozon_status approved → 索引行回填；否则任务 status → pending_moderation + 「重新审核中」
    """
    index = _lookup_index(tenant_id, product_id)
    if index is None:
        raise HTTPException(status_code=404, detail="商品未找到，可能已归档")
    if not images:
        raise HTTPException(status_code=400, detail="images 不能为空")

    if index["credential_id"]:
        client_id, api_key = credential_service.get_decrypted(tenant_id, index["credential_id"])
    else:
        raise HTTPException(status_code=400, detail="该商品未绑定店铺凭证，无法重传")

    checker = url_checker or image_quality_evaluator.check_url_alive
    alive, dead = [], []
    for url in images:
        (alive if checker(url) else dead).append(url)
    if not alive:
        raise HTTPException(status_code=422, detail="全部图片不可达，无存活图片可重传")

    post = ozon_post_fn or ozon_post
    body = {"items": [{"product_id": int(product_id), "offer_id": index["offer_id"], "images": alive}]}
    try:
        result = post(client_id, api_key, "/v3/product/import", body, timeout=30)
    except requests.HTTPError as exc:
        resp = exc.response
        if resp is not None and resp.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Ozon 凭证无效，请到店铺管理更新凭证")
        raise HTTPException(status_code=502, detail=f"Ozon 重传失败: {(resp.text if resp else exc)[:300]}")
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Ozon 网络错误: {exc}")

    import_task_id = str((result.get("result") or {}).get("task_id", ""))
    if _query_ozon_status(client_id, api_key, product_id, post) == "approved":
        _upsert_index(tenant_id, product_id, index["offer_id"], index["task_id"], index["credential_id"])
        return {
            "ok": True, "product_id": product_id, "offer_id": index["offer_id"],
            "import_task_id": import_task_id, "status": "approved", "re_under_review": False,
            "message": "更新成功，已同步商品图片", "images": alive, "images_filtered": dead,
        }

    _mark_task_pending_moderation(index["task_id"])
    return {
        "ok": True, "product_id": product_id, "offer_id": index["offer_id"],
        "import_task_id": import_task_id, "status": "pending_moderation", "re_under_review": True,
        "message": "图片已更新，商品重新审核中", "images": alive, "images_filtered": dead,
    }
