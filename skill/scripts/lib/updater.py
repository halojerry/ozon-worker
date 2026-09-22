"""Skill 自动更新 — COS manifest 检测 + 下载/sha256 校验/备份/覆盖/回滚。

流程（每次命令静默检查）：
1. 读本地 skill/VERSION
2. GET 远端 manifest.json（COS，超时 5s，失败静默跳过）
   v0.76 T32(cicd-H2): PROD_PUBKEY 非空时同址拉取 manifest.sig 做 minisign
   验签（纯 Python Ed25519，客户端无 minisign 二进制）；任何失败 → 本次检查
   按失败处理（更新被拦截，fail-closed）。PROD_PUBKEY 为空 = 跳过验签 + warn
   （与 deploy/cos-update.sh 的 COS_UPDATE_SKIP_VERIFY 逃生门同口径）。
3. 版本比本地新 → 提示"更新可用"，用户运行 `skill update` 或确认后应用
4. 应用：下载 tar.gz → sha256 校验 → 备份当前 → 覆盖 scripts/ 文档 VERSION
   → 保留 data/（凭证/登录态/缓存）→ 失败自动回滚

manifest.json 格式（CI 发布时生成，见 build-skill.yml）：
    {"version": "0.12.0", "url": "https://<bucket>.cos.<region>.myqcloud.com/skill/ozon-worker-skill-0.12.0.tar.gz",
     "sha256": "...", "released_at": "...", "notes": "..."}
"""
from __future__ import annotations

import base64
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

from scripts.lib.utils import safe_rmtree, safe_unlink

logger = logging.getLogger(__name__)

# COS manifest 地址：环境变量 SKILL_MANIFEST_URL 覆盖
_DEFAULT_MANIFEST_URL = os.environ.get(
    "SKILL_MANIFEST_URL",
    "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-skill/manifest.json",
)

CHECK_TIMEOUT = 5      # manifest 检查超时（静默失败）
DOWNLOAD_TIMEOUT = 120  # 下载超时

# ── v0.76 T32(cicd-H2): manifest 签名校验公钥（minisign 公钥文件原文）──
# ⚠️ 待生产公钥生成后填入（一次性人工动作：`minisign -G` → 公钥原文填这里 +
#    提交 deploy/cos-update.pub + 私钥进 CI secret + 离线冷备）。
#    空值语义 = 跳过验签 + warn（与 deploy/cos-update.sh 的 COS_UPDATE_SKIP_VERIFY
#    逃生门同口径）。⚠️ 填入前须先给 build-skill.yml 加 manifest 签名步骤，否则
#    skill manifest 无签名 → 本函数 fail-closed → skill 更新会被拦截。
PROD_PUBKEY = ""  # 待生产公钥生成后填入

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


# ═══ v0.76 T32(cicd-H2): manifest 签名校验（minisign 同语义，纯 Python 实现）═══
# 客户端机器没有 minisign 二进制，这里内建 Ed25519 验签（RFC 8032，仅 verify
# 路径；stdlib-only）。参考实现形态来自 ed25519.cr.yp.to 公版代码（public
# domain），正确性由 RFC 8032 官方测试向量锁定（tests/test_updater_manifest_sig_v076.py）。

_ED_P = 2**255 - 19
_ED_L = 2**252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * pow(121666, _ED_P - 2, _ED_P)) % _ED_P
_ED_I = pow(2, (_ED_P - 1) // 4, _ED_P)
_MINISIGN_ALGO = b"Ed"


def _ed_inv(x: int) -> int:
    return pow(x, _ED_P - 2, _ED_P)


def _ed_xrecover(y: int) -> int:
    xx = (y * y - 1) * _ed_inv(_ED_D * y * y + 1)
    x = pow(xx, (_ED_P + 3) // 8, _ED_P)
    if (x * x - xx) % _ED_P != 0:
        x = (x * _ED_I) % _ED_P
    if x % 2 != 0:
        x = _ED_P - x
    return x


_ED_BASE_Y = (4 * _ed_inv(5)) % _ED_P
_ED_BASE = (_ed_xrecover(_ED_BASE_Y), _ED_BASE_Y)


def _ed_on_curve(pt: tuple[int, int]) -> bool:
    x, y = pt
    return (-x * x + y * y - 1 - _ED_D * x * x * y * y) % _ED_P == 0


def _ed_add(p1: tuple[int, int], p2: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p1
    x2, y2 = p2
    x3 = (x1 * y2 + x2 * y1) * _ed_inv(1 + _ED_D * x1 * x2 * y1 * y2)
    y3 = (y1 * y2 + x1 * x2) * _ed_inv(1 - _ED_D * x1 * x2 * y1 * y2)
    return (x3 % _ED_P, y3 % _ED_P)


def _ed_scalarmult(pt: tuple[int, int], e: int) -> tuple[int, int]:
    acc = (0, 1)  # 群单位元
    while e > 0:
        if e & 1:
            acc = _ed_add(acc, pt)
        pt = _ed_add(pt, pt)
        e >>= 1
    return acc


def _ed_decode_point(s: bytes) -> tuple[int, int]:
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    x = _ed_xrecover(y)
    if (x & 1) != ((s[31] >> 7) & 1):
        x = _ED_P - x
    pt = (x % _ED_P, y % _ED_P)
    if not _ed_on_curve(pt):
        raise ValueError("point not on curve")
    return pt


def _ed25519_verify(sig: bytes, msg: bytes, pub: bytes) -> bool:
    """RFC 8032 Ed25519 验签（sig/pub 定长检查 + s<L 严格性防可塑性）。"""
    if len(sig) != 64 or len(pub) != 32:
        return False
    try:
        r_pt = _ed_decode_point(sig[:32])
        a_pt = _ed_decode_point(pub)
    except ValueError:
        return False
    s_val = int.from_bytes(sig[32:], "little")
    if s_val >= _ED_L:
        return False
    h_val = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little")
    return _ed_scalarmult(_ED_BASE, s_val) == _ed_add(r_pt, _ed_scalarmult(a_pt, h_val))


def _parse_minisign_pubkey(pubkey_text: str) -> tuple[bytes, bytes] | None:
    """解析 minisign 公钥（接受 deploy/cos-update.pub 文件原文或其中 base64 行）。

    返回 (keynum(8B), pubkey(32B))；格式不符返回 None。
    """
    for line in pubkey_text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("untrusted comment"):
            continue
        try:
            raw = base64.b64decode(line, validate=True)
        except Exception:
            continue
        if len(raw) == 42 and raw[:2] == _MINISIGN_ALGO:
            return raw[2:10], raw[10:42]
    return None


def _parse_minisign_sig(sig_text: str) -> tuple[bytes, bytes] | None:
    """解析 minisign 签名文件第二行 base64 blob。返回 (keynum(8B), sig(64B)) 或 None。"""
    for line in sig_text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("untrusted comment", "trusted comment")):
            continue
        try:
            raw = base64.b64decode(line, validate=True)
        except Exception:
            continue
        if len(raw) == 74 and raw[:2] == _MINISIGN_ALGO:
            return raw[2:10], raw[10:74]
    return None


def verify_minisign_signature(pubkey_text: str, sig_text: str, message: bytes) -> bool:
    """验 minisign 签名（与 `minisign -V -p <pub> -x <sig> -m <file>` 同语义）。

    强制校验: ①主签名 Ed25519(pub=固定内置公钥, msg=manifest 原始字节)
    ②签名 blob 的 keynum 与公钥 keynum 一致。trusted comment 的二次签名
    不校验（展示性元数据，不影响消息认证强度——minisign -V 会验，纯 Python
    路径省略并在测试注释留痕）。
    """
    pub = _parse_minisign_pubkey(pubkey_text)
    if pub is None:
        logger.warning("manifest 验签: 公钥格式无法解析（需 minisign 公钥文件内容）")
        return False
    pub_keynum, pub_key = pub
    sig = _parse_minisign_sig(sig_text)
    if sig is None:
        logger.warning("manifest 验签: 签名格式无法解析（需 minisign 签名文件）")
        return False
    sig_keynum, sig_val = sig
    if pub_keynum != sig_keynum:
        logger.warning("manifest 验签: 签名 keynum 与公钥不一致")
        return False
    return _ed25519_verify(sig_val, message, pub_key)


def verify_manifest_authenticity(manifest_url: str, manifest_text: str) -> bool:
    """manifest 验签入口（语义与 deploy/cos-update.sh 一致）。

    - PROD_PUBKEY 为空 → warn 跳过并放行（逃生门口径；填入公钥后自动收紧）
    - 非空 → 拉取 `<manifest_url>.sig`，下载失败/格式错/验签不过一律 False
      （fail-closed：调用方按「检查失败」处理，更新被拦截）
    """
    if not PROD_PUBKEY.strip():
        logger.warning(
            "PROD_PUBKEY 未配置——跳过 manifest 签名校验"
            "（应急逃生门口径, 同 deploy/cos-update.sh COS_UPDATE_SKIP_VERIFY）"
        )
        return True
    sig_url = manifest_url + ".sig"
    try:
        resp = requests.get(sig_url, timeout=CHECK_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("manifest 签名拉取失败 %s (HTTP %s)", sig_url, resp.status_code)
            return False
        resp.encoding = "utf-8"
        return verify_minisign_signature(
            PROD_PUBKEY, resp.text, manifest_text.encode("utf-8")
        )
    except Exception as exc:
        logger.warning("manifest 验签异常（fail-closed 拦截本次更新）: %s", exc)
        return False


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
        # v0.76 T32(cicd-H2): 验签不过 = 检查失败（更新被拦截, fail-closed）；
        # PROD_PUBKEY 为空时内部 warn 跳过（逃生门口径）。
        if not verify_manifest_authenticity(url, resp.text):
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
    dl_url = update_info.get("url", "")
    expect_sha = update_info.get("sha256", "")
    root = skill_dir()

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
                        safe_rmtree(target)
                    else:
                        safe_unlink(target)
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
