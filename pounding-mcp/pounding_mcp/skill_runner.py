"""调用 skill CLI 的薄封装 —— 黑盒命令即资产。

skill 的 CLI 入口是 `scripts/cli.py`（`pyproject.toml` 里 `pounding-probe = "scripts.cli:main"`），
输出 JSON（自动脱敏 api_key/token）。这里只做：参数映射 CLI flag + subprocess 调用 + 解析 JSON。
业务逻辑（CDP 采集 / 选品引擎 / 上架组装）全在 skill 里，本模块不重写。

配置（环境变量）：
- OZON_SKILL_DIR       skill 目录绝对路径（默认按项目根 ../skill 推导）
- OZON_SKILL_PYTHON    运行 skill 的 python 解释器（默认 sys.executable）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# skill 目录：优先环境变量，否则按「本文件在 pounding-mcp/pounding_mcp/ 下 → ../../skill」推导
_DEFAULT_SKILL_DIR = Path(__file__).resolve().parent.parent.parent / "skill"
SKILL_DIR = Path(os.environ.get("OZON_SKILL_DIR", str(_DEFAULT_SKILL_DIR)))


def _discover_skill_python() -> str:
    """确定运行 skill 的 python 解释器。

    优先级：
    1. 环境变量 OZON_SKILL_PYTHON（显式指定）
    2. skill 目录下自动发现 .venv*/bin/python3（skill 依赖 requests/websocket-client/Pillow 在其 venv 里）
    3. 当前解释器 sys.executable
    """
    env = os.environ.get("OZON_SKILL_PYTHON")
    if env:
        return env
    venvs = sorted(SKILL_DIR.glob(".venv*/bin/python3"))
    if venvs:
        return str(venvs[0])
    return sys.executable


SKILL_PYTHON = _discover_skill_python()

_CLI = SKILL_DIR / "scripts" / "cli.py"


class SkillError(RuntimeError):
    """skill CLI 调用失败（非零退出码 / 非 JSON 输出 / 进程异常）。"""


# 浏览器宿主唤醒：skill 需要浏览器时，POST 该端点让 Electron 窗口自动展开。
# 未配置/宿主未启动时静默忽略（skill 会照常走自启 Chrome 或纯 API 模式）。
_BROWSER_WAKE_URL = os.environ.get("POUNDING_BROWSER_WAKE_URL", "http://127.0.0.1:9224/show")


def _wake_browser() -> None:
    """调用 skill 前唤醒浏览器宿主（展开窗口）。失败静默——不影响 skill 执行。"""
    try:
        req = urllib.request.Request(_BROWSER_WAKE_URL, method="POST")
        urllib.request.urlopen(req, timeout=0.5)
    except Exception:
        pass


def run_skill_command(cmd: str, *positional, **flags) -> dict:
    """调用 skill CLI 的一个命令，返回解析后的 JSON dict。

    位置参数对应 CLI 的位置参数（如 search 的 query、query 的 task_id）。
    关键字参数映射为 `--flag value`；布尔 True 映射为 `--flag`（store_true）；
    None / False 跳过。下划线自动转连字符（page_size → --page-size）。

    例：
        run_skill_command("search", "关键词", page_size=5, sort="sold_desc")
        →  search 关键词 --page-size 5 --sort sold_desc

        run_skill_command("graph", url="...", store="3号店", no_submit=True)
        →  graph --url ... --store 3号店 --no-submit
    """
    if not _CLI.exists():
        raise SkillError(f"skill CLI 不存在：{_CLI}（请设置 OZON_SKILL_DIR）")

    _wake_browser()

    argv = [SKILL_PYTHON, str(_CLI), cmd]
    argv += [str(p) for p in positional if p is not None and p != ""]
    for key, val in flags.items():
        if val is None or val is False or val == "":
            continue
        flag = f"--{key.replace('_', '-')}"
        if val is True:
            argv.append(flag)
        else:
            argv += [flag, str(val)]

    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=str(SKILL_DIR),
    )

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        raise SkillError(
            f"skill `{cmd}` 退出码 {proc.returncode}\nstdout: {stdout[:500]}\nstderr: {stderr[:500]}"
        )

    return _parse_output(stdout, stderr)


def _parse_output(stdout: str, stderr: str) -> dict:
    """解析 skill CLI 的输出。

    skill 的输出是混合格式：进度文本（print）+ 尾部 JSON（`_out()` 的 json.dumps indent=2）。
    - 整体是 JSON → 直接返回
    - 尾部有 JSON 块（顶层 `{` 独占一行）→ 提取尾部 JSON
    - 纯文本（如 check 的诊断）→ 包装为 {"raw": ...}
    """
    text = stdout.strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    lines = text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "{":
            candidate = "\n".join(lines[i:])
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return {"raw": text, "_stderr": stderr[:2000] if stderr else ""}
