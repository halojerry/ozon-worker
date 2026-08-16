"""上架配置模板服务（P0-1）：listing_templates 表 CRUD + 注入。

注入语义：模板参数只在「草稿 extensions 未显式设置该字段」时注入
（模板补缺省，不覆盖草稿已有值）。offer_id_prefix 仅对新建上架生效
（更新模式 update_product_id 忽略——重上不变式）。
"""

import copy
import logging
import uuid
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# ── config 白名单：只接受这些 key，非法 key 拒绝（防注入） ──
CONFIG_KEYS = (
    "margin_rate",        # 利润率（0-1），None→worker 默认 0.25
    "commission_rate",    # 佣金率（0-0.5），0=让 worker 自动查店铺真实佣金
    "fx_buffer",          # 汇率缓冲（0-0.5），None→worker 默认 0.05
    "offer_id_prefix",    # 货号前缀（仅新建；同店铺多批次防重）
    "follow_type",        # 跟卖方式（hand 防侵权 / api 强制）
    "stock",              # 上架后库存（extensions.stock）
    "warehouse_id",       # 仓库（extensions.warehouse_id）
)

# 数值边界（create/update 时校验）
_NUMERIC_LIMITS = {
    "margin_rate": (0.0, 1.0),
    "commission_rate": (0.0, 0.5),
    "fx_buffer": (0.0, 0.5),
}


def _select_cols() -> str:
    return "id, tenant_id, name, description, platform, is_default, config, store_overrides, created_at, updated_at"


def _row_to_dict(row) -> dict:
    return {
        "id": str(row.id),
        "tenant_id": row.tenant_id,
        "name": row.name,
        "description": row.description,
        "platform": row.platform,
        "is_default": row.is_default,
        "config": row.config or {},
        "store_overrides": row.store_overrides or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _parse_id(template_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(template_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="上架配置模板不存在")


def _validate_config(config: dict) -> dict:
    """白名单 + 数值边界校验；返回过滤后的 config。"""
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config 必须是 JSON 对象")
    unknown = [k for k in config if k not in CONFIG_KEYS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"config 含非法字段: {', '.join(unknown)}（允许: {', '.join(CONFIG_KEYS)}）",
        )
    cleaned: dict = {}
    for key, val in config.items():
        if val is None:
            continue
        if key in _NUMERIC_LIMITS:
            try:
                num = float(val)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"config.{key} 必须是数字")
            lo, hi = _NUMERIC_LIMITS[key]
            if not (lo <= num <= hi):
                raise HTTPException(status_code=422, detail=f"config.{key} 必须在 [{lo}, {hi}] 范围内")
            cleaned[key] = num
        elif key == "stock":
            try:
                stock = int(val)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="config.stock 必须是整数")
            if stock < 0:
                raise HTTPException(status_code=422, detail="config.stock 不能为负数")
            cleaned[key] = stock
        elif key == "follow_type":
            ft = str(val).lower()
            if ft not in ("hand", "api"):
                raise HTTPException(status_code=422, detail="config.follow_type 必须是 hand 或 api")
            cleaned[key] = ft
        else:
            cleaned[key] = str(val).strip()
    return cleaned


def _validate_store_overrides(overrides: Any) -> dict:
    """P1b 店铺级覆盖校验：{credential_id: {config 子集}}，逐店铺跑 _validate_config。"""
    if overrides in (None, ""):
        return {}
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=422, detail="store_overrides 必须是对象 {credential_id: {config}}")
    cleaned: dict = {}
    for cred_id, cfg in overrides.items():
        if not isinstance(cfg, dict):
            raise HTTPException(status_code=422, detail=f"store_overrides[{cred_id}] 必须是对象")
        cleaned[str(cred_id)] = _validate_config(cfg)
    return cleaned


def list_templates(tenant_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_select_cols()} FROM listing_templates "
            "WHERE tenant_id=:tenant_id ORDER BY is_default DESC, created_at DESC"
        ), {"tenant_id": tenant_id}).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_template(tenant_id: str, template_id: str) -> dict:
    uid = _parse_id(template_id)
    with get_engine().connect() as conn:
        row = conn.execute(text(
            f"SELECT {_select_cols()} FROM listing_templates "
            "WHERE id=:id AND tenant_id=:tenant_id"
        ), {"id": uid, "tenant_id": tenant_id}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="上架配置模板不存在或不属于当前用户")
    return _row_to_dict(row)


def create_template(tenant_id: str, data: dict) -> dict:
    """新建模板；is_default=true 时先清旧默认（部分唯一索引兜底 → 409）。"""
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="配置名称不能为空")
    config = _validate_config(data.get("config") or {})
    is_default = bool(data.get("is_default"))
    if is_default:
        _clear_default(tenant_id)
    with get_engine().begin() as conn:
        try:
            row = conn.execute(text(
                "INSERT INTO listing_templates "
                "(tenant_id, name, description, platform, is_default, config, store_overrides) "
                "VALUES (:tenant_id, :name, :description, :platform, :is_default, CAST(:config AS jsonb), CAST(:store_overrides AS jsonb)) "
                "RETURNING id, tenant_id, name, description, platform, is_default, config, store_overrides, created_at, updated_at"
            ), {
                "tenant_id": tenant_id,
                "name": name,
                "description": str(data.get("description") or ""),
                "platform": str(data.get("platform") or "OZON"),
                "is_default": is_default,
                "config": __import__("json").dumps(config, ensure_ascii=False),
                "store_overrides": __import__("json").dumps(_validate_store_overrides(data.get("store_overrides")), ensure_ascii=False),
            }).fetchone()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="同一租户只能有一个默认配置模板")
    logger.info("上架配置模板已创建 tenant=%s name=%s default=%s", tenant_id, name, is_default)
    return _row_to_dict(row)


def update_template(tenant_id: str, template_id: str, data: dict) -> dict:
    """部分更新：仅更新提供的字段；is_default 从 false→true 时清旧默认。"""
    uid = _parse_id(template_id)
    # 先确认归属
    get_template(tenant_id, template_id)

    fields: list[str] = []
    params: dict = {"id": uid, "tenant_id": tenant_id}
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="配置名称不能为空")
        fields.append("name=:name")
        params["name"] = name
    if "description" in data:
        fields.append("description=:description")
        params["description"] = str(data.get("description") or "")
    if "platform" in data:
        fields.append("platform=:platform")
        params["platform"] = str(data.get("platform") or "OZON")
    if "config" in data:
        fields.append("config=CAST(:config AS jsonb)")
        params["config"] = __import__("json").dumps(_validate_config(data.get("config")), ensure_ascii=False)
    if "store_overrides" in data:
        fields.append("store_overrides=CAST(:store_overrides AS jsonb)")
        params["store_overrides"] = __import__("json").dumps(
            _validate_store_overrides(data.get("store_overrides")), ensure_ascii=False)
    if "is_default" in data:
        want_default = bool(data.get("is_default"))
        if want_default:
            _clear_default(tenant_id, exclude=uid)
        fields.append("is_default=:is_default")
        params["is_default"] = want_default
    if not fields:
        raise HTTPException(status_code=422, detail="没有可更新的字段")

    with get_engine().begin() as conn:
        try:
            row = conn.execute(text(
                f"UPDATE listing_templates SET {', '.join(fields)}, updated_at=NOW() "
                "WHERE id=:id AND tenant_id=:tenant_id "
                "RETURNING id, tenant_id, name, description, platform, is_default, config, store_overrides, created_at, updated_at"
            ), params).fetchone()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="同一租户只能有一个默认配置模板")
    return _row_to_dict(row)


def delete_template(tenant_id: str, template_id: str) -> None:
    uid = _parse_id(template_id)
    with get_engine().begin() as conn:
        result = conn.execute(text(
            "DELETE FROM listing_templates WHERE id=:id AND tenant_id=:tenant_id"
        ), {"id": uid, "tenant_id": tenant_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="上架配置模板不存在或不属于当前用户")


def set_default(tenant_id: str, template_id: str) -> dict:
    """设默认：清旧默认 → 标记当前；返回更新后模板。"""
    uid = _parse_id(template_id)
    get_template(tenant_id, template_id)
    _clear_default(tenant_id, exclude=uid)
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "UPDATE listing_templates SET is_default=true, updated_at=NOW() "
            "WHERE id=:id AND tenant_id=:tenant_id "
            "RETURNING id, tenant_id, name, description, platform, is_default, config, store_overrides, created_at, updated_at"
        ), {"id": uid, "tenant_id": tenant_id}).fetchone()
    return _row_to_dict(row)


def get_default_template(tenant_id: str) -> Optional[dict]:
    with get_engine().connect() as conn:
        row = conn.execute(text(
            f"SELECT {_select_cols()} FROM listing_templates "
            "WHERE tenant_id=:tenant_id AND is_default LIMIT 1"
        ), {"tenant_id": tenant_id}).fetchone()
    return _row_to_dict(row) if row else None


def _clear_default(tenant_id: str, exclude: Optional[uuid.UUID] = None) -> None:
    with get_engine().begin() as conn:
        if exclude:
            conn.execute(text(
                "UPDATE listing_templates SET is_default=false, updated_at=NOW() "
                "WHERE tenant_id=:tenant_id AND is_default AND id<>:exclude"
            ), {"tenant_id": tenant_id, "exclude": exclude})
        else:
            conn.execute(text(
                "UPDATE listing_templates SET is_default=false, updated_at=NOW() "
                "WHERE tenant_id=:tenant_id AND is_default"
            ), {"tenant_id": tenant_id})


def apply_template_to_envelope(
    envelope: dict,
    template: dict,
    *,
    is_update: bool = False,
    credential_id: Optional[str] = None,
) -> dict:
    """把模板参数注入 envelope.extensions（草稿已有值优先，模板补缺省）。

    is_update=True（更新上架）→ 忽略 offer_id_prefix（重上不变式：
    更新必须保持原 offer_id 不变，否则 Ozon 创建新卡）。

    P1b 多店铺差异化：credential_id 在 template.store_overrides 有覆盖 →
    该店铺的覆盖参数（合并进 config，覆盖值优先于全局 config 同 key）。

    返回深拷贝后的 envelope，不修改入参。
    """
    result = copy.deepcopy(envelope)
    config = dict(template.get("config") or {})
    # P1b：店铺级覆盖合并进 config（覆盖值优先）
    overrides = template.get("store_overrides") or {}
    if credential_id and isinstance(overrides, dict):
        store_cfg = overrides.get(str(credential_id))
        if isinstance(store_cfg, dict):
            for k, v in store_cfg.items():
                if v is not None:
                    config[k] = v
    if not config:
        return result
    ext = result.setdefault("extensions", {})
    if not isinstance(ext, dict):
        ext = {}
        result["extensions"] = ext

    for key, val in config.items():
        if key == "offer_id_prefix":
            if is_update or not val:
                continue
            # prefix 注入为单独 key，prepare 层消费
            if not ext.get("offer_id_prefix"):
                ext["offer_id_prefix"] = val
            continue
        # 草稿已显式设置 → 不覆盖（模板只补缺省）
        if key in ext and ext[key] not in (None, ""):
            continue
        ext[key] = val
    return result
