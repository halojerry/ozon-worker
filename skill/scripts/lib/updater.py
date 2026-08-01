"""Skill 自动更新 — COS manifest 检测 + 下载/sha256 校验/备份/覆盖/回滚。

流程（每次命令静默检查）：
1. 读本地 skill/VERSION
2. GET 远端 manifest.json（COS，超时 5s，失败静默跳过）
3. 版本比本地新 → 提示"更新可用"，用户运行 `skill update` 或确认后应用
4. 应用：下载 tar.gz → sha256 校验 → 备份当前 → 覆盖 scripts/ 文档 VERSION
   → 保留 data/（凭证/登录态/缓存）→ 失败自动回滚

manifest.json 格式（CI 发布时生成，见 build-skill.yml）：
    {"version": "0.12.0", "url": "https://<bucket>.cos.<region>.myqcloud.com/skill/ozon-worker-skill-0.12.0.tar.gz",
     "sha256": "...", "released_at": "...", "notes": "..."}
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# COS manifest 地址：环境变量 SKILL_MANIFEST_URL 覆盖；未配置则跳过更新检查
_DEFAULT_MANIFEST_URL = os.environ.get(
    "SKILL_MANIFEST_URL",
    "https://skill-update.mxou.cn/manifest.json",  # 占位，发布时替换为真实 COS 域名
)

CHECK_TIMEOUT = 5      # manifest 检查超时（静默失败）
DOWNLOAD_TIMEOUT = 120  # 下载超时

# 更新时备份/保留的目录
_PRESERVE_DIRS = {"data"}          # 凭证/登录态/缓存/选品日志全部保留
_BACKUP_DIR_NAME = "_update_backup"


def skill_dir() -> Path:
    """返回 Skill 包根目录（源码运行=skill/，dist 运行=dist/）。"""
    return Path(__file__).resolve().parent.parent.parent


def get_local_version() -> str:
    """读取本地 skill/VERSION。"""
    try:
        v = (skill_dir() / "VERSION").read_text(encoding="utf-8").strip()
        return v or "0.0.0"
    except Exception:
        return "0.0.0"


def parse_manifest(text: str) -> dict[str, Any] | None:
    """解析 manifest JSON，校验必填字段。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not all(k in data for k in ("version", "url", "sha256")):
        return None
    return data


def check_update(manifest_url: str = "") -> dict[str, Any] | None:
    """静默检查远端更新。返回更新信息或 None（无更新/失败）。

    失败（网络/超时/格式错）一律返回 None，绝不阻断主流程。
    """
    url = manifest_url or _DEFAULT_MANIFEST_URL
    if not url or url.startswith("https://skill-update.mxou.cn"):  # 未配置真实域名
        return None
    try:
        resp = requests.get(url, timeout=CHECK_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = parse_manifest(resp.text)
        if not data:
            return None
        if _version_key(data["version"]) <= _version_key(get_local_version()):
            return None
        return data
    except Exception as exc:
        logger.debug("更新检查失败（静默）: %s", exc)
        return None


def _version_key(version: str) -> tuple[int, ...]:
    """'0.12.0' → (0, 12, 0)，用于版本比较。"""
    try:
        return tuple(int(p) for p in str(version).split(".")[:3])
    except ValueError:
        return (0, 0, 0)


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """解压 tar.gz / zip 到 dest_dir（自动识别格式）。"""
    if archive_path.name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest_dir)
    else:
        with tarfile.open(archive_path, "r:*") as tf:
            tf.extractall(dest_dir, filter="data")  # 安全解压（拒绝路径穿越）


def _copy_contents(src: Path, dst: Path) -> list[Path]:
    """复制 src 下所有内容到 dst，返回已复制文件列表（用于回滚）。"""
    copied: list[Path] = []
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            if target.exists():
                target.unlink()
            shutil.copy2(item, target)
        copied.append(target)
    return copied


def apply_update(update_info: dict[str, Any], auto_confirm: bool = False) -> dict[str, Any]:
    """应用更新：下载 → 校验 → 备份 → 覆盖 → 回滚兜底。

    返回 {"ok": bool, "old_version": str, "new_version": str, "error": str}
    """
    result = {"ok": False, "old_version": get_local_version(),
              "new_version": update_info.get("version", ""), "error": ""}
    root = skill_dir()
    dl_url = update_info.get("url", "")
    expect_sha = update_info.get("sha256", "")

    if not auto_confirm:
        confirm = input(f"发现新版本 {result['new_version']}，是否更新？(y/N) ")
        if confirm.lower() != "y":
            return {**result, "error": "用户取消"}

    tmp_dir = Path(tempfile.mkdtemp(prefix="skill-update-"))
    try:
        # 1. 下载
        archive = tmp_dir / "update.tar.gz"
        resp = requests.get(dl_url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        resp.raise_for_status()
        with open(archive, "wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)

        # 2. sha256 校验
        if expect_sha and _sha256_of_file(archive) != expect_sha:
            return {**result, "error": "sha256 校验失败，已中止（可能下载损坏或被篡改）"}

        # 3. 解压到临时目录
        extract_dir = tmp_dir / "pkg"
        extract_dir.mkdir()
        _extract_archive(archive, extract_dir)
        # 处理单层包裹（tar.gz 可能含顶层目录）
        if len(list(extract_dir.iterdir())) == 1 and next(extract_dir.iterdir()).is_dir():
            inner = next(extract_dir.iterdir())
            pkg_root = inner
        else:
            pkg_root = extract_dir

        # 4. 备份当前（除 data/ 外）到 root/_update_backup
        backup = root / _BACKUP_DIR_NAME
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir(parents=True, exist_ok=True)
        for item in root.iterdir():
            if item.name in _PRESERVE_DIRS or item.name == _BACKUP_DIR_NAME:
                continue
            shutil.move(str(item), str(backup / item.name))

        # 5. 覆盖新包（同样跳过 data/ 与备份目录）
        for item in pkg_root.iterdir():
            if item.name in _PRESERVE_DIRS or item.name == _BACKUP_DIR_NAME:
                continue
            target = root / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

        # 6. 清理备份
        shutil.rmtree(backup, ignore_errors=True)
        result["ok"] = True
        logger.info("✅ Skill 已更新 %s → %s", result["old_version"], result["new_version"])
        return result

    except Exception as exc:
        # 7. 失败回滚：从备份恢复
        backup = root / _BACKUP_DIR_NAME
        try:
            if backup.exists():
                for item in backup.iterdir():
                    target = root / item.name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    shutil.move(str(item), str(root / item.name))
                shutil.rmtree(backup, ignore_errors=True)
            logger.warning("更新失败，已回滚: %s", exc)
        except Exception as rollback_exc:
            logger.error("回滚也失败: %s", rollback_exc)
        return {**result, "error": f"更新失败已回滚: {exc}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run_update_command() -> int:
    """`skill update` 命令：检查 + 应用更新。返回 0 成功/无更新，1 失败。"""
    info = check_update()
    if not info:
        print(f"✅ 已是最新版本（v{get_local_version()}）")
        return 0
    print(f"📦 发现新版本 v{info['version']}（当前 v{get_local_version()}）")
    if info.get("notes"):
        print(f"   更新说明: {info['notes']}")
    result = apply_update(info, auto_confirm=True)
    if result["ok"]:
        print(f"✅ 更新完成: v{result['old_version']} → v{result['new_version']}")
        print("   ⚠️ 请重启终端后重新运行命令，让新版本生效")
        return 0
    print(f"❌ 更新失败: {result['error']}")
    return 1
