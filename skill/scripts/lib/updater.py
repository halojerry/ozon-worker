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
   → 保留 data/ 与全部点开头条目（.1688-AK/.workbuddy 等本地状态，ISSUE-1）
   → 失败自动回滚

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
import time
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
# 2026-09-23 信任根落地：keynum 234E049C1061DBDF（指纹登记
# docs/audit/2026-09-23-minisign-trust-root.md；私钥在 CI secret
# COS_UPDATE_SIGN_KEY + 用户离线冷备，绝不进源码树）。build-skill.yml
# 已加同款签名步骤（manifest.sig 与包同传），本值填入即端到端收紧。
# 逃生门口径不变：空值 = 跳过验签 + warn。
PROD_PUBKEY = (
    "untrusted comment: minisign public key 234E049C1061DBDF\n"
    "RWTf22EQnAROI7lY39Cx8wo0BtrPA9w55wBTqTAE17AKf7WIqqS/6rYq"
)

# 更新时保留的条目（这些名字绝不进备份/覆盖流程）
_PRESERVE_DIRS = {"data"}          # 凭证/登录态/缓存/选品日志全部保留
_BACKUP_DIR_NAME = "_update_backup"


def _is_preserved(name: str) -> bool:
    """更新流程永不触碰的根级条目：data/ + 备份目录 + 全部点开头条目。

    ⚠️ ISSUE-1（report 53857013，v0.76.0 Windows 真机数据丢失）：.1688-AK/
    .workbuddy 等本地状态目录曾被搬进 _update_backup，叠加「残留备份误回滚」
    被 8 月过期快照覆盖、最新内容进回收站。点开头 = 本地状态约定（发布包
    不含点条目，保留它们零副作用）。"""
    return (name in _PRESERVE_DIRS or name == _BACKUP_DIR_NAME
            or name.startswith("."))


def _discard_backup(backup: Path, quiet: bool = False) -> bool:
    """清理备份目录：删除失败（Windows 沙箱/防护拦截删除——用户机实测会把
    shutil.rmtree 的删除改道回收站并 fail-closed）→ 原地改名为
    ``_update_backup.stale-<ts>``（改名不触发删除拦截）；仍失败返回 False。

    绝不静默：非 quiet 时打印人话提示；失败路径调用方决定中止/告警。"""
    try:
        shutil.rmtree(backup, ignore_errors=True)
    except OSError:
        pass  # 被环境 shim 替换的删除实现可能直接 raise——与 ignore_errors 同义
    if not backup.exists():
        return True
    base = time.strftime("%Y%m%d_%H%M%S")
    stale = backup.with_name(f"{_BACKUP_DIR_NAME}.stale-{base}")
    n = 1
    while stale.exists():  # 同秒多次清理（启动残留 + 成功后清理）名字递增防撞
        n += 1
        stale = backup.with_name(f"{_BACKUP_DIR_NAME}.stale-{base}-{n}")
    try:
        backup.rename(stale)
    except OSError as exc:
        logger.warning("备份目录删除与改名均失败: %s: %s", backup, exc)
        if not quiet:
            print(f"⚠️ 备份目录 {backup.name} 无法清理（文件被占用/沙箱限制），"
                  "请手动删除后重试")
        return False
    if not quiet:
        print(f"⚠️ 备份目录无法删除，已改名为 {stale.name}（可手动清理）")
    return True


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


def _ed25519_verify(sig: bytes, msg: bytes, pub: bytes, prehashed: bool = False) -> bool:
    """RFC 8032 Ed25519 验签（sig/pub 定长检查 + s<L 严格性防可塑性）。

    prehashed=True 走 minisign 预哈希模式（0.12+ `-S` 默认 / 0.11 `-V` 默认要求）：
    消息先过**无键 BLAKE2b-64**（libsodium ``crypto_generichash``，digest_size=64），
    摘要作为消息做纯 Ed25519——**不是** RFC 8032 Ed25519ph 的 dom2 构造（0.11 源码
    ``message_load_hashed`` 实证，2026-09-23 真机交叉验证锁定，见
    docs/audit/2026-09-23-minisign-trust-root.md）。
    """
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
    if prehashed:
        msg = hashlib.blake2b(msg, digest_size=64).digest()
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


def _parse_minisign_sig(sig_text: str) -> tuple[bytes, bytes, bool] | None:
    """解析 minisign 签名文件 base64 blob。返回 (keynum(8B), sig(64B), prehashed) 或 None。

    ⚠️ 算法字是**签名模式标记**（2026-09-23 真 minisign + 0.11 源码实证）：
    ``Ed`` = 纯 Ed25519 legacy（需 ``-l`` 旗标）；``ED`` = 预哈希（**0.11 与 0.12
    的 ``-S`` 默认**，构造 = 无键 BLAKE2b-64 摘要做纯 Ed25519）。此前只认 ``Ed``，
    意味着**CI 真实签名（0.11 默认产出 ED）会被一律拒绝**——首个发版就会炸的
    链路级缺陷（RFC 8032 向量测试用手搓 blob 掩盖；真机交叉验证抓出，见
    docs/audit/2026-09-23-minisign-trust-root.md）。
    """
    for line in sig_text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("untrusted comment", "trusted comment")):
            continue
        try:
            raw = base64.b64decode(line, validate=True)
        except Exception:
            continue
        if len(raw) == 74 and raw[:2] in (b"Ed", b"ED"):
            return raw[2:10], raw[10:74], raw[:2] == b"ED"
    return None


def verify_minisign_signature(pubkey_text: str, sig_text: str, message: bytes) -> bool:
    """验 minisign 签名（与 `minisign -V -p <pub> -x <sig> -m <file>` 同语义）。

    强制校验: ①主签名 Ed25519/Ed25519ph（pub=固定内置公钥, msg=manifest 原始
    字节；签名模式由算法字 Ed/ED 自动分流——0.11 默认纯签名，0.12+ 默认预哈希）
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
    sig_keynum, sig_val, prehashed = sig
    if pub_keynum != sig_keynum:
        logger.warning("manifest 验签: 签名 keynum 与公钥不一致")
        return False
    return _ed25519_verify(sig_val, message, pub_key, prehashed=prehashed)


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

        # 4. 备份将被新包替换的条目到 root/_update_backup。
        # ⚠️ ISSUE-1（report 53857013）三防线（改这段前必读）：
        #   ① 只备份「包内同名条目」——本地独有条目（.workbuddy/.1688-AK/普通
        #      文件）原地不动，从不进备份，回滚也碰不到它们；
        #   ② 残留备份一律**不再回滚**：旧逻辑按「备份有条目而 root 没有」判定
        #      上次中断并 _rollback，但 Windows 下备份清理失败曾被 ignore_errors
        #      静默吞掉 → 过期快照残留 → 下次更新把数月前的旧快照覆盖回根目录。
        #      overlay 每次都是全量包，重跑即自愈——「启动时回滚」没有收益只有
        #      数据丢失风险；
        #   ③ 清理失败不静默：rmtree 失败改名为 .stale-<ts>（见 _discard_backup），
        #      两个都失败则中止本次更新（fail-closed），绝不带着脏备份继续。
        pkg_names = {item.name for item in pkg_root.iterdir()}
        backup = root / _BACKUP_DIR_NAME
        if backup.exists():
            print("⚠️ 检测到上次更新残留的备份（可能上次更新中断），"
                  "已清理后重新更新；本地独有文件不受影响")
            if not _discard_backup(backup):
                return {**result, "error":
                        "残留备份 _update_backup 清理失败（文件被占用/沙箱限制），"
                        "请手动删除该目录后重试 `skill update`"}
        backup.mkdir(parents=True, exist_ok=True)
        for name in sorted(pkg_names):
            item = root / name
            if _is_preserved(name) or not item.exists():
                continue
            shutil.move(str(item), str(backup / name))

        # 5. 覆盖新包（同样跳过保留条目）
        for item in pkg_root.iterdir():
            if _is_preserved(item.name):
                continue
            target = root / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

        # 5.5 自检：包内每个文件必须落地（覆盖被静默截断 → 回滚，不带病宣告成功）
        missing_after = [str(p.relative_to(pkg_root))
                         for p in pkg_root.rglob("*")
                         if p.is_file() and not _is_preserved(p.parts[0])
                         and not (root / p.relative_to(pkg_root)).exists()]
        if missing_after:
            raise RuntimeError(f"更新自检失败，包内条目未落地: "
                               f"{missing_after[:5]}")

        # 6. 清理备份（失败改名 .stale-<ts> 留待手动清理，绝不静默吞）
        _discard_backup(backup, quiet=True)
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
    """从备份恢复原文件（幂等，失败仅记日志不抛出）。

    ISSUE-1 后备份只含「包内同名条目」——回滚只替换包文件，本地独有条目
    （.workbuddy/.1688-AK/散文件）不在备份里，任何失败路径都碰不到它们。"""
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
            _discard_backup(backup, quiet=True)
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
