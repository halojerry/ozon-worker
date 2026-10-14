"""skill 整体体检（doctor）——一条命令端到端红绿清单。

动因（2026-10-04 Sentry 实录）：两台病机（WORKER_URL 配成 ``http://x``、
1688 AK 过期）提交失败各灌 80+ 事件——``check`` 本可查出但用户没跑。根因是
检查面分散（check=环境 / check_doc_sync=文档 / parity=worker CI / 版本=打包），
没有「装完/升级完跑一次全查」的单一入口。

本模块是 doctor 的核心（cli.py 只做渲染接线）。六个体检区：

1. **配置语义**：WORKER_URL 语义校验（scheme/host 合法性——``http://x`` 这类
   手滑当场红）+ 凭证在位快览（零网络，纯 config_store 读）
2. **版本一致性**：skill/VERSION ↔ SKILL.md frontmatter ↔ ``_const.SKILL_VERSION``
   三源对账（版本病是 updater 类事故的高发根因）
3. **文档同步**：复用 ``check_doc_sync`` 的提取器（CLI ↔ SKILL.md 命令表）
4. **契约 parity**：若机器上有 sibling 仓库（../api-integration/envelope-keys.json）
   → skill 实发 extensions 键 ⊆ worker 模型键集（升级错配当场现形）；
   无 worker checkout → SKIP（CI 侧有同款闸兜底）
5. **MCP 工具对账**：sibling ../pounding-mcp 存在 → server.py @mcp.tool 注册数
   对账 docs/MCP-SERVER.md；无 → SKIP
6. **CDP 轻探**：127.0.0.1:9222 TCP 三秒探（只探测、绝不拉起 Chrome）——
   通了提示健康、不通 WARN 引导跑 ``check``（完整诊断在那边）

设计约定：
- 每项返回 ``DoctorResult(name, status, detail, next_hint)``；
  status ∈ {PASS, FAIL, WARN, SKIP}；出口码 0=全 PASS/SKIP，1=存在 FAIL。
- ``--json`` 输出机器可读全量（agent 消费），人读模式对齐 CLI 的 NEXT 行纪律。
- **零副作用**：不写文件、不拉 Chrome、不打生产（Worker /health 只在
  WORKER_URL 语义合法且非本地时轻探一次，3s 超时）。
"""
from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Optional

from urllib.parse import urlparse

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


@dataclass
class DoctorResult:
    name: str
    status: str
    detail: str = ""
    next_hint: str = ""
    section: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _skill_root() -> str:
    """skill/ 根（本文件位于 skill/scripts/lib/）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ────────────────────── 1. 配置语义 ──────────────────────


def check_worker_url() -> DoctorResult:
    """WORKER_URL 语义校验——``http://x`` 这类手滑在语义层就红，不等网络层。

    规则：scheme ∈ {http, https}；host 非空；host 为 localhost/127.x 或
    含 ``.`` 的域名或纯 IP（单字母/单词 host 在云默认之外几乎必为手滑）。
    语义合法且非常规默认时再轻探一次 /health（3s），探不通 WARN 不 FAIL
    （网络抖动≠配置错——EY 那台是语义就错）。

    ⚠️ 直读 env 而非 import ``_const.CLOUD_API_BASE``——后者是模块导入时
    冻结的常量，本函数需要「调用时」的最新值（测试 monkeypatch env 语义）。
    """
    raw = os.environ.get("WORKER_URL", "").strip()
    src = f"env WORKER_URL={raw!r}" if raw else f"默认 {_CLOUD_DEFAULT_MASKED}"
    url = raw or "https://worker.mxou.cn"
    try:
        p = urlparse(url)
    except Exception as exc:  # pragma: no cover - urlparse 极少抛
        return DoctorResult("WORKER_URL", FAIL, f"{src} → 解析失败: {exc}", "改成 http(s)://host[:port] 形态")
    host = (p.hostname or "").strip()
    if p.scheme not in ("http", "https"):
        return DoctorResult("WORKER_URL", FAIL, f"{src} → scheme 非法: {p.scheme!r}", "以 http:// 或 https:// 开头")
    if not host:
        return DoctorResult("WORKER_URL", FAIL, f"{src} → host 为空", "检查 URL 是否只写了 scheme")
    is_local = host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")
    is_domain = "." in host and len(host) > 3
    if not (is_local or is_domain):
        return DoctorResult(
            "WORKER_URL", FAIL,
            f"{src} → host={host!r} 不是 localhost/域名/IP（疑似手滑或占位符）",
            "本地=http://localhost:8080；生产=https://worker.mxou.cn",
            data={"url": url},
        )
    # 语义合法 → 轻探 /health（3s；失败只 WARN）
    try:
        import requests as _req
        r = _req.get(f"{url}/health", timeout=3)
        ok = r.status_code == 200
        return DoctorResult(
            "WORKER_URL", PASS if ok else WARN,
            f"{src} → 语义合法；/health HTTP {r.status_code}",
            "" if ok else "Worker 暂不可达（网络/服务状态）——恢复后重跑 doctor",
            data={"url": url, "http": r.status_code},
        )
    except Exception as exc:
        return DoctorResult(
            "WORKER_URL", WARN, f"{src} → 语义合法；/health 探测失败: {str(exc)[:60]}",
            "检查本机到 Worker 的网络；本地栈用 deploy/docker compose up -d",
        )


_CLOUD_DEFAULT_MASKED = "https://worker.mxou.cn（_const 内置）"


def check_credentials_present() -> DoctorResult:
    """凭证在位快览（零网络）：MXOU token / 1688 AK / 店铺数。只报『缺』，不验有效性。"""
    try:
        from scripts.lib import config_store as cs
        has_token = bool((cs.get_mxou_token() or "").strip())
        has_ak = bool((cs.get_ali_1688_ak() or "").strip())
        stores = cs.list_stores() if hasattr(cs, "list_stores") else []
        n_stores = len(stores) if isinstance(stores, (list, tuple)) else 0
    except Exception as exc:
        return DoctorResult("凭证在位", WARN, f"config_store 读取失败: {str(exc)[:60]}", "跑 `check` 看配置面详情")
    missing = [n for n, ok in (("MXOU_TOKEN", has_token), ("1688 AK", has_ak)) if not ok]
    if missing:
        return DoctorResult(
            "凭证在位", FAIL, f"缺: {', '.join(missing)}；店铺 {n_stores} 个",
            "`set_token` / `set_ak`（或 get_ak 自动获取）",
        )
    return DoctorResult("凭证在位", PASS, f"MXOU ✓ AK ✓ 店铺 {n_stores} 个（有效性请跑 check 验证）")


# ────────────────────── 2. 版本一致性 ──────────────────────


def check_version_consistency() -> DoctorResult:
    """两源对账：skill/VERSION ↔ SKILL.md frontmatter。

    （历史第三源 ``_const.SKILL_VERSION`` 已随死代码清扫退役；版本病是
    updater 类事故的高发根因——v0.62 实录：skill 二进制打包校验依赖
    skill/VERSION 而非 frontmatter，错位即包版本谎报。）
    """
    root = _skill_root()
    v_file = (open(os.path.join(root, "VERSION")).read().strip()
              if os.path.exists(os.path.join(root, "VERSION")) else "")
    v_front = ""
    md = os.path.join(root, "SKILL.md")
    if os.path.exists(md):
        m = re.search(r'^version:\s*"?([0-9][0-9A-Za-z.\-]*)"?', open(md).read(), re.M)
        v_front = m.group(1) if m else ""
    versions = {"VERSION 文件": v_file, "SKILL.md frontmatter": v_front}
    if v_file and v_file == v_front:
        return DoctorResult("版本一致性", PASS, f"两源一致 {v_file}", data=versions)
    bad = {k: v or "（空/缺失）" for k, v in versions.items()}
    return DoctorResult(
        "版本一致性", FAIL, f"两源不一致: {bad}",
        "以 skill/VERSION 为准改齐 SKILL.md frontmatter",
        data=versions,
    )


# ────────────────────── 3. 文档同步 ──────────────────────


def check_doc_sync() -> DoctorResult:
    """复用 check_doc_sync 提取器：CLI 子命令 ↔ SKILL.md 命令表。

    提取器（check_doc_sync.py）从 **doctor 自身所在的真实 scripts/ 目录**
    导入（tmp 根里没有它）；被检的 cli.py / SKILL.md 走 ``_skill_root()``
    （可注入，测试语义）。
    """
    import sys as _sys
    real_scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if real_scripts not in _sys.path:
        _sys.path.insert(0, real_scripts)
    try:
        from check_doc_sync import extract_cli_commands, extract_doc_commands, standalone_scripts
    except Exception as exc:
        return DoctorResult("文档同步", SKIP, f"提取器不可用: {str(exc)[:50]}", "")
    root = _skill_root()
    cli_cmds = extract_cli_commands(os.path.join(root, "scripts", "cli.py"))
    doc_cmds = extract_doc_commands(os.path.join(root, "SKILL.md"))
    standalone = standalone_scripts(os.path.join(root, "scripts"))
    missing = sorted(cli_cmds - doc_cmds)
    ghost = sorted((doc_cmds - cli_cmds) - standalone)
    if not missing and not ghost:
        return DoctorResult("文档同步", PASS, f"CLI {len(cli_cmds)} 命令全部在 SKILL.md", data={"n": len(cli_cmds)})
    detail = []
    if missing:
        detail.append(f"SKILL.md 缺 {len(missing)}: {', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}")
    if ghost:
        detail.append(f"文档有 CLI 无 {len(ghost)}: {', '.join(ghost[:6])}")
    return DoctorResult(
        "文档同步", FAIL, "；".join(detail),
        "python3 scripts/check_doc_sync.py --fix（只补不删）后补说明",
        data={"missing": missing, "ghost": ghost},
    )


# ────────────────────── 4. 契约 parity ──────────────────────


def check_envelope_parity() -> DoctorResult:
    """skill 实发 extensions 键 ⊆ worker 模型键集（envelope-keys.json）。

    仅当机器上有 sibling 仓库布局（../api-integration/envelope-keys.json，即
    dev checkout）才跑；独立安装的 skill 包没有该文件 → SKIP（worker CI 的
    同款闸兜底）。提取器与 worker tests/test_envelope_contract.py 同口径
    （resolved_extensions.setdefault + 惰性 dict 形态）。
    """
    repo = os.path.dirname(_skill_root())
    keys_path = os.path.join(repo, "api-integration", "envelope-keys.json")
    if not os.path.exists(keys_path):
        return DoctorResult("契约 parity", SKIP, "无 ../api-integration/envelope-keys.json（独立安装）", "")
    try:
        model_keys = set(json.load(open(keys_path)).get("extensions", {}).keys())
    except Exception as exc:
        return DoctorResult("契约 parity", WARN, f"键文件解析失败: {str(exc)[:50]}", "")
    if not model_keys:
        return DoctorResult("契约 parity", WARN, "键文件为空", "worker 侧重跑 gen_contract_docs.py")
    written: set[str] = set()
    src_root = os.path.join(_skill_root(), "scripts")
    # 提取器与 worker tests/test_envelope_contract.py 同口径（三形态）：
    # ① resolved_extensions.setdefault("key", …)
    # ② extensions["key"] = …（负向断言防 dict 容器名碰撞，如 _opts["extensions"]）
    # ③ 直接 setdefault("extensions", …)["key"] 链式形态
    pat_setdefault = re.compile(r'resolved_extensions\.setdefault\(\s*["\']([a-z_0-9]+)["\']')
    pat_subscript = re.compile(r'(?<![a-z_0-9])extensions\[\s*["\']([a-z_0-9]+)["\']\s*\]')
    pat_chained = re.compile(r'setdefault\(\s*["\']extensions["\'][^)]*\)\[\s*["\']([a-z_0-9]+)["\']\s*\]')
    for dirpath, _dirs, files in os.walk(src_root):
        if "__pycache__" in dirpath or os.sep + "data" + os.sep in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py") or fn == "doctor.py":
                continue  # doctor.py 是检查器自身（注释里的示例形态会自污染提取）
            try:
                src = open(os.path.join(dirpath, fn), encoding="utf-8").read()
            except Exception:
                continue
            written |= set(pat_setdefault.findall(src))
            written |= set(pat_subscript.findall(src))
            written |= set(pat_chained.findall(src))
    unknown = sorted(written - model_keys)
    if unknown:
        return DoctorResult(
            "契约 parity", FAIL,
            f"skill 实发 {len(written)} 键中 {len(unknown)} 个未登记: {', '.join(unknown[:8])}",
            "worker utils/envelope_contract.py 补字段+来源 → gen_contract_docs.py → 升级 worker",
            data={"unknown": unknown},
        )
    return DoctorResult("契约 parity", PASS, f"实发 {len(written)} 键 ⊆ 模型 {len(model_keys)} 键", data={"written": len(written)})


# ────────────────────── 5. MCP 工具对账 ──────────────────────


def check_mcp_tools() -> DoctorResult:
    """../pounding-mcp 在位时：server.py @mcp.tool 注册数 ↔ docs/MCP-SERVER.md 口径。"""
    repo = os.path.dirname(_skill_root())
    server_py = os.path.join(repo, "pounding-mcp", "pounding_mcp", "server.py")
    if not os.path.exists(server_py):
        return DoctorResult("MCP 工具对账", SKIP, "无 ../pounding-mcp（独立安装）", "")
    src = open(server_py, encoding="utf-8").read()
    registered = len(re.findall(r"@mcp\.tool", src))
    if registered == 0:
        return DoctorResult("MCP 工具对账", WARN, "server.py 未检出 @mcp.tool 注册", "检查 pounding-mcp 版本")
    # 文档口径：docs/MCP-SERVER.md 首个「N 个工具/N 工具」数字
    doc_n: Optional[int] = None
    doc_path = os.path.join(repo, "docs", "MCP-SERVER.md")
    if os.path.exists(doc_path):
        m = re.search(r"(\d+)\s*(?:个工具|工具)", open(doc_path, encoding="utf-8").read())
        doc_n = int(m.group(1)) if m else None
    if doc_n is not None and doc_n != registered:
        return DoctorResult(
            "MCP 工具对账", FAIL,
            f"server.py 注册 {registered} vs docs/MCP-SERVER.md 口径 {doc_n}",
            "以 server.py 为准更新 MCP-SERVER.md 与 expert-tool-map",
        )
    return DoctorResult("MCP 工具对账", PASS, f"注册 {registered} 个工具，文档口径一致" if doc_n else f"注册 {registered} 个工具（文档无计数口径）")


# ────────────────────── 6. CDP 轻探 ──────────────────────


def check_cdp_light() -> DoctorResult:
    """127.0.0.1:9222 TCP 三秒探——只探测不拉起；不通只 WARN（check 才负责完整诊断）。"""
    try:
        with socket.create_connection(("127.0.0.1", 9222), timeout=3):
            return DoctorResult("CDP 轻探", PASS, "127.0.0.1:9222 可达（Chrome 调试口在位）")
    except Exception:
        return DoctorResult(
            "CDP 轻探", WARN, "9222 不可达（Chrome 未以调试模式运行）",
            "需要抓取时跑 `check`（会自动拉起 Chrome）；仅提交/查询类命令不受影响",
        )


# ────────────────────── 聚合出口 ──────────────────────

ALL_CHECKS = (
    ("配置语义", (check_worker_url, check_credentials_present)),
    ("包与版本", (check_version_consistency, check_doc_sync)),
    ("契约与生态", (check_envelope_parity, check_mcp_tools)),
    ("运行时", (check_cdp_light,)),
)


def run_doctor() -> tuple[list[DoctorResult], int]:
    """跑全部体检区。返回 (结果列表, 出口码)——0=全 PASS/SKIP，1=存在 FAIL。"""
    results: list[DoctorResult] = []
    exit_code = 0
    for section, fns in ALL_CHECKS:
        for fn in fns:
            try:
                res = fn()
            except Exception as exc:  # 单项崩不拖垮整体体检
                res = DoctorResult(fn.__name__, WARN, f"检查器异常: {str(exc)[:70]}", "")
            res.section = section
            results.append(res)
            if res.status == FAIL:
                exit_code = 1
    return results, exit_code


def render_text(results: list[DoctorResult]) -> str:
    """人读渲染：分区 + [PASS]/[FAIL]/[WARN]/[SKIP] + FAIL 带 NEXT。"""
    icon = {PASS: "✅", FAIL: "❌", WARN: "⚠️ ", SKIP: "⏭️ "}
    lines: list[str] = []
    cur_section = ""
    for r in results:
        if r.section != cur_section:
            cur_section = r.section
            lines.append(f"\n═══ {cur_section} ═══")
        lines.append(f"{icon.get(r.status, '·')} {r.name}: {r.detail}")
        if r.status == FAIL and r.next_hint:
            lines.append(f"   👉 NEXT: {r.next_hint}")
    n_fail = sum(1 for r in results if r.status == FAIL)
    n_warn = sum(1 for r in results if r.status == WARN)
    n_pass = sum(1 for r in results if r.status == PASS)
    lines.append(f"\n{'─' * 40}")
    lines.append(f"体检结论: {n_pass} PASS / {n_fail} FAIL / {n_warn} WARN / "
                 f"{len(results) - n_pass - n_fail - n_warn} SKIP"
                 + ("——按上方 NEXT 逐项修复后重跑" if n_fail else "——无阻塞项"))
    return "\n".join(lines)


def render_json(results: list[DoctorResult], exit_code: int) -> str:
    return json.dumps(
        {"exit_code": exit_code,
         "summary": {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, WARN, SKIP)},
         "results": [{"section": r.section, "name": r.name, "status": r.status,
                      "detail": r.detail, "next": r.next_hint} for r in results]},
        ensure_ascii=False, indent=2,
    )
