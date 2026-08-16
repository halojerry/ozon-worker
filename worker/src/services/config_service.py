"""C1: 配置文件管理服务 — worker/config/*.json 的读 / 写 / 备份 / 回滚。

config 目录解析优先级：
1. `APP_WORKSPACE_PATH` 环境变量（Docker 内为 /app）→ `<ws>/config/`
2. 回退：本文件 worker/src/services/config_service.py → 上溯 3 级到 worker/ → `worker/config`

约定：
- 所有写操作先 `json.loads` 校验（非法 → ValueError，不写不备份）
- 写前把当前文件备份到 `config/backup/{name}.{timestamp}.json`，备份保留最新 5 份
- 写文件用原子写（同目录 tmp + os.replace），崩溃不损坏原文件
- 文件名统一走 `_resolve_path`（穿越防护），禁止路径分隔符 / `..` / 非 .json
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# 备份保留份数
_BACKUP_KEEP = 5


def _resolve_config_dir() -> Path:
    """解析配置目录：APP_WORKSPACE_PATH 优先，回退到 worker/config。"""
    ws = os.environ.get("APP_WORKSPACE_PATH")
    if ws:
        return Path(ws) / "config"
    # worker/src/services/config_service.py → worker/src → worker → worker/config
    return Path(__file__).resolve().parent.parent.parent / "config"


CONFIG_DIR = _resolve_config_dir()


def _resolve_path(name: str) -> Path:
    """校验配置文件名并返回绝对路径（路径穿越防护）。

    拒绝：空名、含 / 或 \\ 、含 .. 、不以 .json 结尾。
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"非法配置文件名: {name!r}")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"配置文件名不允许路径分隔符或 ..: {name!r}")
    if not name.endswith(".json"):
        raise ValueError(f"配置文件必须以 .json 结尾: {name!r}")
    base = CONFIG_DIR.resolve()
    resolved = (base / name).resolve()
    if resolved.parent != base:
        raise ValueError(f"配置文件名越界: {name!r}")
    return resolved


def _timestamp() -> str:
    """备份文件名时间戳（秒级，字典序即时间序）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _atomic_write(path: Path, content: str) -> None:
    """原子写：同目录临时文件 + os.replace（写一半崩溃不损坏原文件）。"""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _prune_backups(config_name: str) -> None:
    """裁剪备份：按文件名（含时间戳）排序，仅保留最新 _BACKUP_KEEP 份。"""
    backup_dir = CONFIG_DIR / "backup"
    backups = sorted(p for p in backup_dir.glob(f"{config_name}.*.json") if p.is_file())
    for old in backups[:-_BACKUP_KEEP]:
        old.unlink(missing_ok=True)


def _backup_current(path: Path) -> Path | None:
    """把当前文件备份到 config/backup/{name}.{ts}.json；文件不存在 → None。"""
    if not path.is_file():
        return None
    backup_dir = CONFIG_DIR / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.name}.{_timestamp()}.json"
    _atomic_write(backup_path, path.read_text(encoding="utf-8"))
    _prune_backups(path.name)
    return backup_path


def list_configs() -> list[dict]:
    """列出 config 目录下所有 *.json 文件名（排除 backup 子目录与非 json）。"""
    items = []
    if CONFIG_DIR.is_dir():
        for p in sorted(CONFIG_DIR.iterdir()):
            if p.is_file() and p.suffix == ".json":
                items.append({"name": p.name})
    return items


def read_config(name: str) -> dict:
    """读取并解析配置文件（非法 JSON → ValueError；不存在 → FileNotFoundError）。"""
    path = _resolve_path(name)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"配置文件不存在: {name}") from exc
    return json.loads(raw)


def write_config(name: str, content: str) -> dict:
    """写入配置：先校验 JSON（非法 → ValueError，不写不备份）→ 备份当前文件 → 原子写。"""
    json.loads(content)  # 先校验，非法内容直接拒绝
    path = _resolve_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {name}")
    backup_path = _backup_current(path)
    _atomic_write(path, content)
    return {"backup_path": str(backup_path) if backup_path else "", "updated": True}


def list_backups(name: str) -> list[dict]:
    """列出指定配置的备份（名称倒序，含 size / mtime）。"""
    _resolve_path(name)  # 名字校验（穿越防护）
    items = []
    backup_dir = CONFIG_DIR / "backup"
    if backup_dir.is_dir():
        for p in backup_dir.glob(f"{name}.*.json"):
            if p.is_file():
                st = p.stat()
                items.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    items.sort(key=lambda item: item["name"], reverse=True)
    return items


def rollback_config(name: str, backup_name: str) -> dict:
    """从备份回滚：校验备份名（仅 basename + .json）→ 读取并校验 JSON → 写回主文件。

    回滚不产生新备份（直接用备份内容覆盖主文件）。
    """
    _resolve_path(name)
    if not backup_name or "/" in backup_name or "\\" in backup_name or ".." in backup_name:
        raise ValueError(f"非法备份文件名: {backup_name!r}")
    if not backup_name.endswith(".json"):
        raise ValueError(f"备份文件名必须以 .json 结尾: {backup_name!r}")
    backup_path = CONFIG_DIR / "backup" / backup_name
    if not backup_path.is_file():
        raise FileNotFoundError(f"备份不存在: {backup_name}")
    content = backup_path.read_text(encoding="utf-8")
    json.loads(content)  # 备份内容非法 → ValueError
    _atomic_write(_resolve_path(name), content)
    return {"name": name, "restored": True}
