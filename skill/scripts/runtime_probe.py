"""Runtime Python 自动检测与免安装（v0.31.0 PR-A）— 纯 stdlib，绝不编译。

背景：skill 核心库是 Cython 编译的 .so（cpython-312 ABI tag），**只有 Python 3.12
能加载**（stub loader 按 sys.version_info 精确匹配）。用户通过 agent
（Claude Code/Codex/opencode 等）用 bash 子进程调用 skill 时，PATH 里的 python3
可能撞到 3.9/3.10/3.11 → 直接崩。

本模块解决「用哪个解释器跑」：
- resolve_python()：当前解释器 ≥3.12 直接用；否则扫描 PATH 找 3.12/3.13/3.14
  （双边界验证，绝不选 3.14+ —— ABI 不匹配会 import .so 崩，CF-2）
- re_exec_if_needed()：发现更合适的解释器 → os.execve 无感切换
  （env 白名单清洗 + SKILL_RUNTIME_OK 哨兵防递归，CF-3: SKILL_NO_VENV 短路回现状）

⚠️ 本文件必须进 compile.py 的 COPY_FILES 明文清单，绝不能编译成 .so：
编译产物本身是 py312 ABI，在 3.11 下自己就崩，L2 检测失去意义。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# .so ABI 硬绑定：cpython-312（compile.py 用 Python 3.12 编译，stub loader 按
# sys.version_info 精确匹配）。精确门 3.12 <= ver < 3.13 —— 只接受 3.12.x，
# 3.13/3.14 的 cpython-31x tag 与 cpython-312 .so 不匹配（CF-2）
MIN_PY = (3, 12)
MAX_PY = (3, 13)

# 候选解释器扫描：只扫 python3.12（ABI 目标唯一；3.13 不匹配不扫）
_CANDIDATE_CMDS = ("python3.12",)

# 哨兵环境变量：re-exec 后置 1，防止递归 exec 死循环
_SENTINEL = "SKILL_RUNTIME_OK"

# 逃生舱：置 1 时跳过 L2/L3 全部，完全回退 v0.30 现状（仅当前解释器检查 + pip 提示，CF-3）
_ESCAPE = "SKILL_NO_VENV"

# re-exec 时必须从 env 里清掉的变量（宿主环境污染源，race-risk R3）
_STRIP_PREFIXES = ("PYTHONPATH", "PYTHONHOME", "PIP_", "VIRTUAL_ENV")

# re-exec 后必须保留的变量（用户配置透传）
_KEEP_VARS = ("WORKER_URL", "SKILL_AUTO_UPDATE", "SKILL_MANIFEST_URL",
              "SKILL_NO_VENV", "SKILL_PYTHON", "OZON_CLIENT_ID", "OZON_API_KEY")


def _parse_version(text: str) -> tuple | None:
    """解析 'Python 3.12.1' 或 '3.12.1' → (3, 12, 1)；失败返回 None。"""
    import re
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text))
    if not m:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    except (ValueError, TypeError):
        return None


def _version_ok(ver: tuple | None) -> bool:
    """双边界验证：MIN_PY <= ver < MAX_PY（CF-2：绝不接受 3.13+/3.11-）。"""
    if not ver:
        return False
    return MIN_PY <= (ver[0], ver[1]) < MAX_PY


def _probe_candidate(cmd: str, timeout: int = 10) -> str | None:
    """验证候选解释器版本。坏解释器可能挂起 → 必须有 timeout（agent 场景挂起=死等）。"""
    try:
        proc = subprocess.run(
            [cmd, "-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    ver = _parse_version(proc.stdout)
    if _version_ok(ver):
        return cmd
    return None


def _scan_candidates() -> str | None:
    """扫描 PATH 找可用的 3.12/3.13 解释器（确定性顺序，3.12 优先）。"""
    for name in _CANDIDATE_CMDS:
        found = shutil.which(name)
        if found:
            ok = _probe_candidate(found)
            if ok:
                return found
    # Windows py launcher（py -3.12 / py -3.13）
    if sys.platform == "win32":
        py_launcher = shutil.which("py")
        if py_launcher:
            for ver_tag in ("3.12", "3.13"):
                try:
                    proc = subprocess.run(
                        [py_launcher, f"-{ver_tag}", "-c",
                         "import sys;print('%d.%d'%sys.version_info[:2])"],
                        capture_output=True, text=True, timeout=10,
                    )
                except (subprocess.TimeoutExpired, OSError):
                    continue
                if proc.returncode == 0 and _version_ok(_parse_version(proc.stdout)):
                    return f"{py_launcher} -{ver_tag}"
    return None


def resolve_python() -> tuple[str, bool]:
    """决定用哪个解释器跑。返回 (python_cmd, is_current)。

    - SKILL_NO_VENV=1 → 短路：返回当前解释器 + is_current=True（完全回退现状，CF-3）
    - 当前 sys.executable 满足 → (sys.executable, True)（零开销，现状不变）
    - 扫描 PATH 找到 3.12/3.13 → (cmd, False)（需要 re-exec）
    - 都没有 → (sys.executable, True)（调用方自行报版本错误提示）
    """
    if os.getenv(_ESCAPE, "0") == "1":
        return (sys.executable, True)

    cur = (sys.version_info[0], sys.version_info[1])
    if MIN_PY <= cur < MAX_PY:
        return (sys.executable, True)

    found = _scan_candidates()
    if found:
        return (found, False)
    return (sys.executable, True)


def re_exec_if_needed(python_cmd: str, script: str, argv: list[str]) -> None:
    """os.execve 无感切换到目标解释器（永不返回，除非 exec 失败）。

    env 处理（race-risk R3）：
    - 清掉 PYTHONPATH/PYTHONHOME/PIP_*/VIRTUAL_ENV（宿主污染源）
    - 保留 _KEEP_VARS（用户配置透传）
    - 置 SKILL_RUNTIME_OK=1 哨兵防递归
    """
    if python_cmd == sys.executable:
        return
    clean_env = dict(os.environ)
    for key in list(clean_env):
        if any(key.startswith(p) for p in _STRIP_PREFIXES):
            del clean_env[key]
    clean_env[_SENTINEL] = "1"
    # 记录目标解释器（供后续 ensure_venv/诊断）
    clean_env["SKILL_RUNTIME_PY"] = python_cmd

    full_argv = [python_cmd, script, *argv]
    try:
        os.execve(python_cmd, full_argv, clean_env)
    except OSError as e:
        print(f"❌ 解释器切换失败（{python_cmd}）: {e}", flush=True)
        print(f"  → 请手动运行: {python_cmd} {script} {' '.join(argv)}", flush=True)
        sys.exit(1)


def script_dir() -> Path:
    """返回 scripts/ 目录（runtime_probe.py 所在）。"""
    return Path(__file__).resolve().parent


# ── PR-B: L3 自动 venv ──────────────────────────────────────────────

def skill_root() -> Path:
    """返回 skill 包根目录（含 data/）。"""
    return script_dir().parent


def _venv_dir() -> Path:
    return skill_root() / "data" / ".venv"


def _venv_python(venv_dir: Path) -> Path:
    """venv 内解释器路径（跨平台）。"""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _requirements_path() -> Path:
    return skill_root() / "requirements.txt"


def _ready_marker(venv_dir: Path) -> Path:
    return venv_dir / ".ready"


def _sha256_of_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _venv_ready(venv_dir: Path) -> bool:
    """.ready 标记存在且 sha256 匹配 requirements.txt → venv 可用（短路）。"""
    ready = _ready_marker(venv_dir)
    if not ready.exists():
        return False
    try:
        stamp = ready.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stamp == _sha256_of_file(_requirements_path())


def _lock_acquire(lock_path: Path, timeout_sec: float = 60.0):
    """跨进程锁（复用 updater 模板）。⚠️ 语义=阻塞轮询等待（venv 必须等到建好，
    不能像 update 那样跳过——agent 并行命令拿不到锁直接失败=用户可见故障）。

    返回：已持有锁的 fd（调用方负责 _lock_release(fd)）；超时返回 None。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    import time as _t
    deadline = _t.time() + timeout_sec
    while True:
        try:
            import fcntl
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (ImportError, OSError):
            try:
                import msvcrt
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
                return fd
            except (ImportError, OSError):
                pass
        if _t.time() >= deadline:
            fd.close()
            return None
        _t.sleep(0.5)


def _lock_release(fd) -> None:
    try:
        fd.close()
    except Exception:
        pass


def ensure_venv(base_python: str) -> tuple[str, str]:
    """确保 data/.venv 存在且依赖齐全，返回 (venv_python, status)。

    status: "ok" 直接可用 / "created" 本次创建 / "failed" 创建失败（调用方报错）。

    CF-1 修复：pip install 返回码非零 → **不写 .ready**，写 .failed 标记，
    返回 failed（绝不缓存坏 venv → 绝不 re-exec 死循环）。
    MP-3 修复：requirements 变化时旧 venv 先 rename 成 .venv.stale-{ts} 再新建，
    运行中进程不受影响。
    MP-4 修复：pip 失败重试 2 次（退避 5s/15s）→ 仍失败写 .failed。
    """
    venv_dir = _venv_dir()
    venv_py = _venv_python(venv_dir)
    req_path = _requirements_path()

    # 0. 短路：.ready 存在且 sha256 匹配 → 直接可用（零开销）
    if _venv_ready(venv_dir) and venv_py.exists():
        return (str(venv_py), "ok")

    # 1. 加锁（阻塞轮询等待 60s，防 agent 并行命令失败）
    lock_path = skill_root() / "data" / ".venv.lock"
    lock_fd = _lock_acquire(lock_path)
    if lock_fd is None:
        return (str(base_python), "failed")  # 锁超时（罕见）→ 回退当前解释器
    try:
        # DCL：拿锁后再查一次 .ready（别的进程可能刚建好）
        if _venv_ready(venv_dir) and venv_py.exists():
            return (str(venv_py), "ok")

        # 2. 清理半成品（上次崩/requirements 变化）：rename 不删（运行中进程安全，MP-3）
        if venv_dir.exists():
            import time as _t
            stale = skill_root() / "data" / f".venv.stale-{int(_t.time())}"
            try:
                venv_dir.rename(stale)
            except OSError:
                import shutil
                shutil.rmtree(venv_dir, ignore_errors=True)

        # 3. 建 venv
        import subprocess as _sp
        try:
            proc = _sp.run(
                [base_python, "-m", "venv", str(venv_dir)],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                return (str(base_python), "failed")
        except (OSError, _sp.TimeoutExpired):
            return (str(base_python), "failed")

        # 4. pip install（重试 2 次退避；进度回显防 agent 超时误判 hang）
        if req_path.exists():
            attempts = 0
            while attempts < 3:
                try:
                    proc = _sp.run(
                        [str(venv_py), "-m", "pip", "install", "-q", "-r", str(req_path)],
                        capture_output=True, text=True, timeout=300,
                    )
                    if proc.returncode == 0:
                        break
                except (OSError, _sp.TimeoutExpired):
                    proc = None
                attempts += 1
                print(f"  ⏳ pip 安装依赖失败（第 {attempts}/3 次），重试中...", flush=True)
                import time as _t
                _t.sleep(5 if attempts == 1 else 15)
            if proc is None or proc.returncode != 0:
                # CF-1: 不写 .ready！写 .failed 标记，返回失败（绝不缓存坏 venv）
                try:
                    _ready_marker(venv_dir).write_text("failed", encoding="utf-8")
                except OSError:
                    pass
                return (str(base_python), "failed")

        # 5. 写 .ready（sha256 标记）
        try:
            _ready_marker(venv_dir).write_text(_sha256_of_file(req_path), encoding="utf-8")
        except OSError:
            pass
        return (str(venv_py), "created")
    finally:
        if lock_fd:
            _lock_release(lock_fd)


def deps_ok() -> list[str]:
    """探测当前解释器的核心依赖，返回缺失列表（空=齐全）。"""
    missing = []
    for _mod, _pkg in (("requests", "requests"), ("websocket", "websocket-client"), ("PIL", "Pillow")):
        try:
            __import__(_mod)
        except ImportError:
            missing.append(_pkg)
    return missing
