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

# COS manifest 地址：环境变量 SKILL_MANIFEST_URL 覆盖
_DEFAULT_MANIFEST_URL = os.environ.get(
    "SKILL_MANIFEST_URL",
    "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-skill/manifest.json",
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
    # ⚠️ 必须顶层是 dict：字符串/list 对 `k in data` 的语义不同，
    # 可能误通过校验后 TypeError（P2）
    if not isinstance(data, dict):
        return None
    if not all(k in data for k in ("version", "url", "sha256")):
        return None
    return data


def _fetch_manifest(manifest_url: str = "") -> tuple[dict[str, Any] | None, bool]:
    """拉取并解析远端 manifest。

    Returns: (data, ok) — data 为更新信息或 None；ok=False 表示网络/解析失败
    （与"无更新"区分：无更新时 ok=True, data=None）。
    """
    url = manifest_url or _DEFAULT_MANIFEST_URL
    if not url:
        return None, False
    try:
        resp = requests.get(url, timeout=CHECK_TIMEOUT)
        if resp.status_code != 200:
            return None, False
        # 显式 UTF-8 解码，避免中文 notes 依赖 chardet 探测乱码（P2）
        resp.encoding = "utf-8"
        data = parse_manifest(resp.text)
        if not data:
            return None, False
        if _version_key(data["version"]) <= _version_key(get_local_version()):
            return None, True   # 已是最新（同版本或更旧）
        return data, True
    except Exception as exc:
        logger.debug("更新检查失败（静默）: %s", exc)
        return None, False


def check_update(manifest_url: str = "") -> dict[str, Any] | None:
    """静默检查远端更新。返回更新信息或 None（无更新/失败）。

    失败（网络/超时/格式错）一律返回 None，绝不阻断主流程。
    """
    data, _ = _fetch_manifest(manifest_url)
    return data


def auto_update_if_available(manifest_url: str = "") -> dict[str, Any] | None:
    """自动更新入口（v0.18.0）：有新版本且非源码目录时自动应用，失败自动回滚。

    - 源码开发目录（存在 compile.py）不自动更新，返回 None
    - 无更新/网络失败返回 None（静默）
    - 有更新返回 apply_update 的结果 dict（{"ok": bool, ...}）
    """
    if _is_source_layout():
        return None
    info, ok = _fetch_manifest(manifest_url)
    if not ok or not info:
        return None
    return apply_update(info, auto_confirm=True)


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


def _is_source_layout() -> bool:
    """检测是否源码开发目录（存在 compile.py/pyproject.toml）而非 dist 分发包。

    源码目录更新会误删仓库文件（compile.py 等非包文件），拒绝自动更新（P2）。
    """
    root = skill_dir()
    return (root / "compile.py").exists() or (root / "pyproject.toml").exists()


def apply_update(update_info: dict[str, Any], auto_confirm: bool = False) -> dict[str, Any]:
    """应用更新：下载 → 校验 → 备份 → 覆盖 → 回滚兜底。

    返回 {"ok": bool, "old_version": str, "new_version": str, "error": str}
    """
    result = {"ok": False, "old_version": get_local_version(),
              "new_version": update_info.get("version", ""), "error": ""}
    root = skill_dir()
    dl_url = update_info.get("url", "")
    expect_sha = update_info.get("sha256", "")

    # ⚠️ 源码开发目录拒绝自动更新：会误删 compile.py 等非包文件（P2）
    if _is_source_layout():
        return {**result, "error":
                "检测到源码开发目录（存在 compile.py），不执行自动更新。"
                "请使用 dist/ 分发包或手动 git pull。"}

    # ⚠️ PR-3 (D6): 跨进程文件锁 — 两个并发 CLI 同时 auto-update 会互相破坏备份/覆盖。
    # 拿不到锁（另一个进程正在更新）→ 直接返回，绝不阻塞或并发覆盖。
    _lock_fd = None
    try:
        _lock_path = skill_dir() / "data" / ".update.lock"
        _lock_path.parent.mkdir(parents=True, exist_ok=True)
        _lock_fd = open(_lock_path, "w")
        try:
            import fcntl  # Unix
            fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            import msvcrt  # Windows
            msvcrt.locking(_lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if _lock_fd:
            _lock_fd.close()
        return {**result, "error": "另一个更新正在进行中，本次跳过（稍后自动重试）"}

    try:
        return _apply_update_locked(update_info, auto_confirm, result)
    finally:
        try:
            if _lock_fd:
                _lock_fd.close()
        except Exception:
            pass


def _apply_update_locked(update_info: dict[str, Any], auto_confirm: bool,
                         result: dict[str, Any]) -> dict[str, Any]:
    """加锁后的更新主流程（原 apply_update 主体）。"""
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
            f.writelines(resp.iter_content(65536))
        resp.close()

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
        # ⚠️ P1 中断安全：若上次更新中断残留备份（root 缺文件），先恢复旧版本
        # 再继续，避免删除"最后一份可回滚副本"后新版本有问题回不去
        if backup.exists():
            missing = [item.name for item in backup.iterdir()
                       if not (root / item.name).exists()]
            if missing:
                print("⚠️ 检测到上次未完成的更新，先恢复旧版本...")
                _rollback(result)
            else:
                shutil.rmtree(backup, ignore_errors=True)
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

    except PermissionError as exc:
        return _fail_file_locked(result, exc)
    except shutil.Error as exc:
        # Windows 上 copytree 复制被进程锁的 .pyd 时抛 shutil.Error
        # （条目是 (src, dst, errmsg) 元组，errmsg 含 Permission denied）
        # 需识别并给友好提示
        err_text = str(exc)
        if "Permission" in err_text or "Errno 13" in err_text \
                or "denied" in err_text.lower():
            return _fail_file_locked(result, exc)
        return _fail_rollback(result, exc)

    except (KeyboardInterrupt, SystemExit) as exc:
        # ⚠️ P1 中断安全：Ctrl+C/进程终止也要回滚到一致状态
        _rollback(result)
        return {**result, "error": f"更新被中断，已回滚: {exc}"}

    except Exception as exc:
        return _fail_rollback(result, exc)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _fail_file_locked(result: dict, exc: Exception) -> dict:
    """Windows 文件被占用：回滚备份并提示关闭终端重试。"""
    _rollback(result)
    return {**result, "error":
            f"更新失败（文件被占用）: {exc}\n"
            "   ⚠️ Windows 上请关闭所有正在运行 skill 的终端/窗口后重试 `skill update`"}


def _fail_rollback(result: dict, exc: Exception) -> dict:
    """通用失败：回滚备份。"""
    _rollback(result)
    return {**result, "error": f"更新失败已回滚: {exc}"}


def _rollback(result: dict) -> None:
    """从备份恢复原文件（幂等，失败仅记日志不抛出）。"""
    root = skill_dir()
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
        logger.warning("更新失败，已回滚")
    except Exception as rollback_exc:
        logger.error("回滚也失败: %s", rollback_exc)


def run_update_command() -> int:
    """`skill update` 命令：检查 + 应用更新。返回 0 成功/无更新，1 失败。"""
    info, ok = _fetch_manifest()
    if not ok:
        # 区分"检查失败"与"无更新"：离线不误报"已是最新"（P2）
        print("⚠️ 无法连接更新服务器（网络或 COS 配置问题），请稍后重试")
        return 1
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
