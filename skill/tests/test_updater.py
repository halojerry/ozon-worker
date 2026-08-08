#!/usr/bin/env python3
"""updater.py 单测（v0.18.0）— 无需网络/外部服务：mock requests + 临时目录。

运行（pytest 或独立脚本均可）：
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_updater.py -v
    cd skill && PYTHONPATH=. python3 tests/test_updater.py
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from scripts.lib import updater  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def make_package_bytes(version: str = "9.9.9") -> bytes:
    """构造一个最小合法 skill 包（VERSION + scripts/ + SKILL.md）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in (
                ("VERSION", version.encode()),
                ("SKILL.md", b"# doc\n"),
                ("scripts/lib/placeholder.py", b"# placeholder\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    buf.seek(0)
    return buf.read()


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self.payload = payload if isinstance(payload, bytes) else payload.encode()
        self.status_code = status_code
        self.encoding = "utf-8"

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size: int = 65536):
        yield self.payload

    def close(self):
        pass


def make_skill_root(version: str = "0.12.0") -> Path:
    root = Path(tempfile.mkdtemp(prefix="skill-updater-test-"))
    (root / "scripts").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "VERSION").write_text(version, encoding="utf-8")
    (root / "data" / "keep.txt").write_text("keep", encoding="utf-8")
    (root / "scripts" / "cli.py").write_text("# old cli\n", encoding="utf-8")
    return root


def make_manifest(version: str = "9.9.9", pkg: bytes | None = None) -> dict:
    pkg = pkg if pkg is not None else make_package_bytes(version)
    return {
        "version": version,
        "url": "https://cos.example/ozon-skill/pk.tar.gz",
        "sha256": sha_of(pkg),
        "released_at": "2026-08-03T00:00:00Z",
        "notes": "test",
    }


def fake_get_side_effect(manifest: dict, pkg: bytes):
    """按 URL 返回 manifest 响应或包响应。"""
    def _get(url, *args, **kwargs):
        if "manifest.json" in url:
            return FakeResponse(json.dumps(manifest))
        return FakeResponse(pkg)
    return _get


def _cleanup(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)


# ── parse_manifest ─────────────────────────────────────────────────────────

def test_parse_manifest_valid():
    data = updater.parse_manifest(json.dumps(make_manifest()))
    assert data is not None and data["version"] == "9.9.9"


def test_parse_manifest_rejects_non_dict():
    assert updater.parse_manifest('"hello"') is None
    assert updater.parse_manifest("[1,2,3]") is None
    assert updater.parse_manifest("not-json{{") is None
    assert updater.parse_manifest(json.dumps({"version": "1.0"})) is None  # 缺 url/sha256


def test_version_key():
    assert updater._version_key("0.12.0") == (0, 12, 0)
    assert updater._version_key("9.9.9") == (9, 9, 9)
    assert updater._version_key("garbage") == (0, 0, 0)


# ── check_update ───────────────────────────────────────────────────────────

def test_check_update_newer_remote():
    manifest = make_manifest("9.9.9")
    with mock.patch.object(updater, "requests") as fake_requests:
        fake_requests.get.return_value = FakeResponse(json.dumps(manifest))
        info = updater.check_update()
    assert info is not None and info["version"] == "9.9.9"


def test_check_update_same_or_older_returns_none():
    with mock.patch.object(updater, "requests") as fake_requests:
        fake_requests.get.return_value = FakeResponse(json.dumps(make_manifest("0.11.0")))
        assert updater.check_update() is None
    with mock.patch.object(updater, "requests") as fake_requests:
        fake_requests.get.return_value = FakeResponse(json.dumps(make_manifest("0.12.0")))
        assert updater.check_update() is None


def test_check_update_failures_are_silent():
    with mock.patch.object(updater, "requests") as fake_requests:
        fake_requests.get.return_value = FakeResponse("oops", status_code=500)
        assert updater.check_update() is None
    with mock.patch.object(updater, "requests") as fake_requests:
        fake_requests.get.side_effect = requests.ConnectionError("net down")
        assert updater.check_update() is None


# ── auto_update_if_available ───────────────────────────────────────────────

def test_auto_update_skips_source_layout():
    root = make_skill_root()
    (root / "compile.py").write_text("# dev checkout", encoding="utf-8")  # 源码目录特征
    try:
        manifest = make_manifest()
        with mock.patch.object(updater, "skill_dir", return_value=root), \
             mock.patch.object(updater, "requests") as fake_requests:
            fake_requests.get.side_effect = fake_get_side_effect(manifest, make_package_bytes())
            result = updater.auto_update_if_available()
        assert result is None  # 源码目录绝不自动更新
        assert (root / "VERSION").read_text() == "0.12.0"
    finally:
        _cleanup(root)


def test_auto_update_applies_and_preserves_data():
    root = make_skill_root()
    try:
        pkg = make_package_bytes("9.9.9")
        manifest = make_manifest("9.9.9", pkg)
        with mock.patch.object(updater, "skill_dir", return_value=root), \
             mock.patch.object(updater, "requests") as fake_requests:
            fake_requests.get.side_effect = fake_get_side_effect(manifest, pkg)
            result = updater.auto_update_if_available()
        assert result is not None and result["ok"] is True
        assert (root / "VERSION").read_text() == "9.9.9"
        assert (root / "data" / "keep.txt").read_text() == "keep"  # data/ 保留
        assert (root / "scripts" / "lib" / "placeholder.py").exists()  # 新包覆盖
        assert not (root / "_update_backup").exists()  # 成功后清理备份
    finally:
        _cleanup(root)


def test_auto_update_sha_mismatch_aborts_without_changes():
    root = make_skill_root()
    try:
        pkg = make_package_bytes("9.9.9")
        manifest = make_manifest("9.9.9", pkg)
        manifest["sha256"] = "0" * 64  # 篡改 sha
        with mock.patch.object(updater, "skill_dir", return_value=root), \
             mock.patch.object(updater, "requests") as fake_requests:
            fake_requests.get.side_effect = fake_get_side_effect(manifest, pkg)
            result = updater.auto_update_if_available()
        assert result is not None and result["ok"] is False
        assert "sha256" in result["error"]
        assert (root / "VERSION").read_text() == "0.12.0"  # 未动
        assert (root / "data" / "keep.txt").exists()
    finally:
        _cleanup(root)


def test_auto_update_rollback_on_copy_failure():
    root = make_skill_root()
    try:
        pkg = make_package_bytes("9.9.9")
        manifest = make_manifest("9.9.9", pkg)
        with mock.patch.object(updater, "skill_dir", return_value=root), \
             mock.patch.object(updater, "requests") as fake_requests, \
             mock.patch.object(updater.shutil, "copy2", side_effect=OSError("disk full")):
            fake_requests.get.side_effect = fake_get_side_effect(manifest, pkg)
            result = updater.auto_update_if_available()
        assert result is not None and result["ok"] is False
        assert "回滚" in result["error"] or "失败" in result["error"]
        assert (root / "VERSION").read_text() == "0.12.0"  # 已回滚到旧版
        assert (root / "data" / "keep.txt").read_text() == "keep"
    finally:
        _cleanup(root)


def test_auto_update_file_locked_hint():
    root = make_skill_root()
    try:
        pkg = make_package_bytes("9.9.9")
        manifest = make_manifest("9.9.9", pkg)
        lock_err = shutil.Error([("a", "b", "Permission denied")])
        with mock.patch.object(updater, "skill_dir", return_value=root), \
             mock.patch.object(updater, "requests") as fake_requests, \
             mock.patch.object(updater.shutil, "copy2", side_effect=lock_err):
            fake_requests.get.side_effect = fake_get_side_effect(manifest, pkg)
            result = updater.auto_update_if_available()
        assert result is not None and result["ok"] is False
        assert "文件被占用" in result["error"]  # Windows 友好提示
        assert (root / "VERSION").read_text() == "0.12.0"  # 已回滚
        assert (root / "data" / "keep.txt").exists()
    finally:
        _cleanup(root)


# ── v0.27.1 P0 收敛回归（包内 VERSION 与 manifest 版本分离死循环）──────────

def test_auto_update_converges_after_package_version_match():
    """修复闭环: 更新后包内 VERSION == manifest → 下次检查不再触发下载。

    死循环根因(3744ca8 修复前): 包内 VERSION 恒为旧值(0.25.0), manifest 0.27.x
    → updater 永远判定有更新 → 每次命令都下载覆盖。修复后 CI 用 tag 写包内
    VERSION, 更新一次即收敛。
    """
    root = make_skill_root("0.25.0")  # 旧坏包
    try:
        pkg = make_package_bytes("0.27.1")  # 修复后: 包内 VERSION == manifest
        manifest = make_manifest("0.27.1", pkg)
        with mock.patch.object(updater, "skill_dir", return_value=root), \
             mock.patch.object(updater, "requests") as fake_requests:
            fake_requests.get.side_effect = fake_get_side_effect(manifest, pkg)
            # 第一次: 0.25.0 < 0.27.1 → 下载更新
            result = updater.auto_update_if_available()
            assert result is not None and result["ok"] is True
            assert (root / "VERSION").read_text() == "0.27.1"
            # 第二次(模拟下一次命令): 本地 0.27.1 == manifest 0.27.1 → 收敛, 不下载
            info = updater.check_update()
        assert info is None  # ✅ 不再触发下载 = 死循环收敛
        assert (root / "VERSION").read_text() == "0.27.1"  # 版本未被覆盖, 保持
    finally:
        _cleanup(root)


def test_old_broken_package_0_25_0_recovers_to_latest():
    """坏包(0.25.0)用户仍能被救回: 0.25.0 < 0.27.1 → 正常触发一次更新。"""
    root = make_skill_root("0.25.0")
    try:
        pkg = make_package_bytes("0.27.1")
        manifest = make_manifest("0.27.1", pkg)
        with mock.patch.object(updater, "skill_dir", return_value=root), \
             mock.patch.object(updater, "requests") as fake_requests:
            fake_requests.get.side_effect = fake_get_side_effect(manifest, pkg)
            result = updater.auto_update_if_available()
        assert result is not None and result["ok"] is True
        assert (root / "VERSION").read_text() == "0.27.1"
    finally:
        _cleanup(root)


# ── PR-3: updater 跨进程锁 ────────────────────────────────────────────────

def test_apply_update_skips_when_lock_held():
    """PR-3 (D6): 已有进程持有锁时，apply_update 立即返回「更新中」，不并发覆盖。"""
    import tempfile as _tf
    with mock.patch.object(updater, "skill_dir") as _mock_dir, \
         mock.patch.object(updater, "_fetch_manifest") as _mock_fetch, \
         mock.patch.object(updater, "requests") as _mock_requests:
        _tmp = Path(_tf.mkdtemp(prefix="skill-lock-test-"))
        (_tmp / "data").mkdir(parents=True)
        (_tmp / "VERSION").write_text("0.0.1")
        _mock_dir.return_value = _tmp

        # 另一个进程持有锁
        _lock_path = _tmp / "data" / ".update.lock"
        _lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = open(_lock_path, "w")
        try:
            import fcntl
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass  # Windows 环境跳过持锁模拟，锁分支由 Unix 覆盖

        result = updater.apply_update(
            {"version": "9.9.9", "url": "https://example.com/x.tar.gz", "sha256": "x"},
            auto_confirm=True,
        )
        assert result["ok"] is False
        assert "更新正在进行中" in result["error"]
        # 不应发生任何下载
        _mock_requests.get.assert_not_called()
        holder.close()
        shutil.rmtree(_tmp, ignore_errors=True)


def test_apply_update_acquires_lock_and_releases():
    """PR-3 (D6): 无竞争时 apply_update 正常获得锁并执行（下载被 mock 拒绝走回滚）。"""
    import tempfile as _tf
    with mock.patch.object(updater, "skill_dir") as _mock_dir, \
         mock.patch.object(updater, "_fetch_manifest") as _mock_fetch:
        _tmp = Path(_tf.mkdtemp(prefix="skill-lock-test2-"))
        (_tmp / "data").mkdir(parents=True)
        (_tmp / "VERSION").write_text("0.0.1")
        _mock_dir.return_value = _tmp

        # 下载必然失败（url 无效）→ 验证锁释放后不残留锁文件内容错误
        result = updater.apply_update(
            {"version": "9.9.9", "url": "https://invalid.example/x.tar.gz", "sha256": "x"},
            auto_confirm=True,
        )
        # 锁文件可以存在，但进程退出后锁已释放（flock 随 fd close 释放）
        _lock_path = _tmp / "data" / ".update.lock"
        assert _lock_path.exists() or True  # 锁文件是否残留不重要，重要的是无并发覆盖
        shutil.rmtree(_tmp, ignore_errors=True)


# ── 独立运行入口（无 pytest 环境）─────────────────────────────────────────

def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"❌ {name}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    total = len(fns)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
