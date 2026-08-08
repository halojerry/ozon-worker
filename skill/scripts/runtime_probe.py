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
