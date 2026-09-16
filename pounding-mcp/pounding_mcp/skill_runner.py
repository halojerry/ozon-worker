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

# MCP 工具名（下划线）→ skill CLI 命令名的显式映射。CLI 子命令混合命名：
# 多数用下划线（set_store/image_search/get_ak…），少数用连字符（discover-task/
# discover-multi/import-cookies）——不能盲目 replace("_","-")，只映射例外。
# v0.70 实测抓出：discover_task 直接传 CLI 会 invalid choice 秒退（v0.69 起
# MCP 同步路径一直没真机跑通过，单测 mock subprocess 没暴露）。
_CLI_COMMAND_ALIASES: dict[str, str] = {
    "discover_task": "discover-task",
    "discover_multi": "discover-multi",
    "import_cookies": "import-cookies",
    "session_sync": "session-sync",
}


class SkillError(RuntimeError):
    """skill CLI 调用失败（非零退出码 / 非 JSON 输出 / 进程异常）。"""


# ── 参数白名单（v0.76 T18 cicd-H1）─────────────────────────────────────
# 8902 tasks_server 与 /ask 是「任意 JSON → CLI flag」的直接通道，浏览器 drive-by
# 可注入任意 flag（如 --auto-submit 真实下单烧余额）。此处按 skill/scripts/cli.py
# 各子命令 add_argument 的**真实 flag 集**（下划线形式）逐 kind 声明白名单；
# 键 = COLLECT_KINDS / MCP 工具的 kind 名（下划线），另含 /ask 直达的 check/category。
# ⚠️ skill CLI 新增 flag 且网关/MCP 面要透出时，必须同步本表——白名单外的键会被丢弃。
# 放置本模块（而非 tasks.py）的原因：tasks.py import 本模块，反向引用会循环导入。
ALLOWED_PARAM_KEYS: dict[str, frozenset[str]] = {
    "check": frozenset(),  # 无任何参数
    "category": frozenset({"query", "lang", "max", "store"}),
    "search": frozenset({
        "query", "page_size", "sort", "export", "rules", "store",
        "auto_submit", "to_box", "threads",
    }),
    "probe": frozenset({"url", "timeout"}),
    "graph": frozenset({
        "item_id", "url", "category_query", "category_id", "type_id",
        "retries", "store", "no_submit", "min_density", "to_box",
        "ozon_ref_url", "template_id", "notify", "wait", "force",
    }),
    "image_search": frozenset({
        "image", "limit", "sort", "source", "ozon_product_id",
    }),
    "get_ak": frozenset({"timeout"}),
    "follow": frozenset({
        "ozon_url", "auto_submit", "to_box", "store", "review", "notify",
        "wait", "force",
    }),
    "discover": frozenset({
        "url", "keyword", "local", "china", "max_products", "min_margin",
        "max_sellers", "fx_rate", "store", "no_analytics", "min_price",
        "max_price", "brand_filter", "rules", "filter_profile", "base_filter",
        "export", "output", "auto_submit", "to_box", "note", "fission",
        "max_depth", "allow_depth_3", "max_total_products", "time_budget",
        "max_sellers_per_product", "max_products_per_seller",
        "non_interactive", "blue_ocean_source", "blue_ocean_csv", "review",
        "compare_sources", "notify", "wait", "force",
    }),
    "discover_multi": frozenset({
        "keywords", "max_each", "local", "china", "min_margin", "fx_rate",
        "store", "no_analytics", "min_price", "max_price", "brand_filter",
        "rules", "filter_profile", "base_filter", "export", "output",
        "auto_submit", "to_box", "blue_ocean_source", "blue_ocean_csv",
        "review", "notify", "wait", "force",
    }),
    "discover_task": frozenset({
        "url", "keyword", "target_count", "max_scan", "filter_profile",
        "base_filter", "min_price", "max_price", "brand_filter", "filters",
        "min_margin", "fx_rate", "match_limit", "match_concurrency",
        "no_match_streak_stop", "store", "to_box", "auto_submit", "dry_run",
        "resume", "expend_shop", "max_depth", "allow_depth_3",
        "max_total_products", "time_budget", "no_analytics", "export",
        "wait", "force",
    }),
    "seller": frozenset({
        "seller_id", "max_products", "max_skus", "wait", "force",
    }),
    "queries": frozenset({
        "type", "keyword", "sku", "category_id", "price_min", "price_max",
        "export", "output",
    }),
}


def _filter_params(params: dict, allowed) -> dict:
    """按白名单过滤 params（纯函数）：只保留 allowed 内的键，未知键静默丢弃。

    allowed 是 frozenset/set（通常取 ALLOWED_PARAM_KEYS[kind]）；
    值原样保留（含 None/False，后续 _build_argv 的跳过语义不变）。
    """
    if not isinstance(params, dict):
        return {}
    return {k: v for k, v in params.items() if k in allowed}


# 浏览器宿主唤醒/静默：skill 需要浏览器时 POST 唤醒展开窗口；命令完成后 POST 完成让宿主自动静默。
# 未配置/宿主未启动时静默忽略（skill 会照常走自启 Chrome 或纯 API 模式）。
_BROWSER_WAKE_URL = os.environ.get("POUNDING_BROWSER_WAKE_URL", "http://127.0.0.1:9224/show")
_BROWSER_DONE_URL = os.environ.get("POUNDING_BROWSER_DONE_URL", "http://127.0.0.1:9224/done")


def _wake_browser() -> None:
    """调用 skill 前唤醒浏览器宿主（展开窗口）。失败静默——不影响 skill 执行。"""
    try:
        req = urllib.request.Request(_BROWSER_WAKE_URL, method="POST")
        urllib.request.urlopen(req, timeout=0.5)
    except Exception:
        pass


def _done_browser() -> None:
    """命令完成后通知浏览器宿主延迟静默。失败静默。"""
    try:
        req = urllib.request.Request(_BROWSER_DONE_URL, method="POST")
        urllib.request.urlopen(req, timeout=0.5)
    except Exception:
        pass


def _build_argv(cmd: str, positional: tuple = (), flags: dict | None = None) -> list[str]:
    """构造 skill CLI argv（位置参数 + flags 映射；工具名→CLI 命令名走别名表）。

    v0.76 T18（cicd-H1）：白名单内命令（ALLOWED_PARAM_KEYS）只放行声明过的参数键，
    白名单外键丢弃并 stderr 提示一行——防 8902 网关/信封外通道注入任意 CLI flag。
    白名单字典外的命令（MCP 既有面 set_store/query/session_sync 等）不过滤，行为不变。
    """
    allowed = ALLOWED_PARAM_KEYS.get(cmd)
    if allowed is not None:
        unknown = sorted(set(flags or {}) - allowed)
        if unknown:
            print(f"[skill_runner] {cmd}: 丢弃白名单外参数: {', '.join(unknown)}",
                  file=sys.stderr)
    cmd = _CLI_COMMAND_ALIASES.get(cmd, cmd)
    argv = [SKILL_PYTHON, str(_CLI), cmd]
    argv += [str(p) for p in positional if p is not None and p != ""]
    for key, val in (flags or {}).items():
        if allowed is not None and key not in allowed:
            continue
        if val is None or val is False or val == "":
            continue
        flag = f"--{key.replace('_', '-')}"
        if val is True:
            argv.append(flag)
        else:
            argv += [flag, str(val)]
    return argv


def run_skill_command_capture(cmd: str, *positional, **flags) -> tuple[dict, subprocess.CompletedProcess]:
    """run_skill_command 的非 raise 版：返回 (解析后的输出, subprocess 结果)。

    退出码承载命令语义的命令（如 session-sync：2=核心 cookie sc_company_id
    缺失拒传）用它按码分支，而不是被通用 SkillError 吞成一团报错文本。
    CLI 不存在仍 raise SkillError（那是配置错误，与命令语义无关）。
    """
    if not _CLI.exists():
        raise SkillError(f"skill CLI 不存在：{_CLI}（请设置 OZON_SKILL_DIR）")

    _wake_browser()

    argv = _build_argv(cmd, positional, flags)

    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=str(SKILL_DIR),
    )
    # 无论成败，通知浏览器宿主「命令完成」→ 延迟自动静默（期间新调用会先 /show 取消）
    _done_browser()

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    return _parse_output(stdout, stderr), proc


def run_skill_command(cmd: str, *positional, **flags) -> dict:
    """调用 skill CLI 的一个命令，返回解析后的 JSON dict。

    位置参数对应 CLI 的位置参数（如 search 的 query、query 的 task_id）。
    关键字参数映射为 `--flag value`；布尔 True 映射为 `--flag`（store_true）；
    None / False / "" 跳过。下划线自动转连字符（page_size → --page-size）。

    例：
        run_skill_command("search", "关键词", page_size=5, sort="sold_desc")
        →  search 关键词 --page-size 5 --sort sold_desc

        run_skill_command("graph", url="...", store="3号店", no_submit=True)
        →  graph --url ... --store 3号店 --no-submit
    """
    parsed, proc = run_skill_command_capture(cmd, *positional, **flags)

    if proc.returncode != 0:
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        raise SkillError(
            f"skill `{cmd}` 退出码 {proc.returncode}\nstdout: {stdout[:500]}\nstderr: {stderr[:500]}"
        )

    return parsed


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
