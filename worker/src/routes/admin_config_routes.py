"""v0.55 C1: 系统配置管理端点（admin）— worker/config/*.json 读 / 写 / 备份 / 回滚。

鉴权：_authenticate_admin（与 admin_routes 一致：_authenticate_token + require_admin，
非管理员 403；本地 local_dev 放行）。业务逻辑在 services/config_service.py。

端点（由 main.py 挂载 v1.include_router）：
    GET  /admin/config                 配置列表（13 个 *.json）
    GET  /admin/config/{name}          读取单个配置
    PUT  /admin/config/{name}          写入（自动备份，保留 5 份）
    GET  /admin/config/{name}/backups  备份列表
    POST /admin/config/{name}/rollback 回滚到指定备份

错误映射：非法 JSON / 非法文件名 → 400；未知配置 / 备份不存在 → 404。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from api.schemas import _examples
from services import admin_service, config_service

router = APIRouter(prefix="/admin/config", tags=["admin"])


class ConfigListItem(BaseModel):
    """config 目录下的配置文件名。"""
    model_config = _examples({"name": "image_prompts.json"})

    name: str


class ConfigContentIn(BaseModel):
    content: str


class BackupItem(BaseModel):
    """配置备份文件（命名 {name}.{YYYYMMDDHHMMSS}.json，mtime 为 epoch 秒）。"""
    model_config = _examples({
        "name": "image_prompts.json.20260911083000.json",
        "size": 5120,
        "mtime": 1789113600.0,
    })

    name: str
    size: int
    mtime: float


class RollbackIn(BaseModel):
    backup_name: str


async def _authenticate_admin(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    user_id = _authenticate_token(token)
    admin_service.require_admin(user_id)
    return user_id


@router.get("", response_model=list[ConfigListItem])
@router.get("/", response_model=list[ConfigListItem])
async def list_configs(request: Request):
    await _authenticate_admin(request)
    return config_service.list_configs()


# 响应示例（openapi_extra 路由级补——handler 返回裸 dict；返回值见 config_service）
_READ_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "system_prompt": "你是 Ozon 上架类目匹配专家",
    "model": "deepseek-v4-flash",
    "temperature": 0.2,
}}}}}}
_WRITE_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "backup_path": "/app/config/backup/image_prompts.json.20260911083000",
    "updated": True,
}}}}}}
_ROLLBACK_OK_EXTRA = {"responses": {"200": {"content": {"application/json": {"example": {
    "name": "image_prompts.json",
    "restored": True,
}}}}}}


@router.get("/{name}", response_model=dict, openapi_extra=_READ_OK_EXTRA)
async def read_config(name: str, request: Request):
    await _authenticate_admin(request)
    try:
        return config_service.read_config(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{name}", response_model=dict, openapi_extra=_WRITE_OK_EXTRA)
async def write_config(name: str, request: Request):
    await _authenticate_admin(request)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from exc
    try:
        model = ConfigContentIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return config_service.write_config(name, model.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{name}/backups", response_model=list[BackupItem])
async def list_backups(name: str, request: Request):
    await _authenticate_admin(request)
    try:
        return config_service.list_backups(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{name}/rollback", response_model=dict, openapi_extra=_ROLLBACK_OK_EXTRA)
async def rollback_config(name: str, request: Request):
    await _authenticate_admin(request)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from exc
    try:
        model = RollbackIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return config_service.rollback_config(name, model.backup_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
