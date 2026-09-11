"""测试级防回潮闸（repo-gov b5）：已知真实密钥指纹不得存在于任何 git 跟踪文件。

背景：仓库 PUBLIC，用户政策「密钥不进源码库」（docs/CONVENTIONS.md「密钥纪律」节）。
CI 双闸 = ①gitleaks-action PR 增量扫描 ②全树 `gitleaks detect --no-git --exit-code=2`
（ci.yml secret-scan）。本测试是第三层：用「前缀指纹」逐个扫描 `git ls-files` 清单，
断言已知真实密钥零命中，并锁定两份放行登记「只减不增」（ratchet）：

- 指纹只含前 8~13 位，本文件绝不写完整密钥；
- ``KNOWN_REMAINING`` 精确登记已知残留（路径→指纹集合）：新泄漏 → 命中超出登记 → 红；
  清欠后残留减少 → 需同步收窄登记（红条目会提示，属预期）；
- ``docs/audit/`` 豁免：审计文档按设计允许 ≤8 字符级指纹引用（事件存证），绝不允许完整密钥；
- ``skill/scripts/lib/config_store.py`` 的 DEFAULT_SENTRY_DSN 为在库设计决策
  （DSN 公开标识符非机密，注释在案），显式豁免，非欠账。

运行（无需 PG）::

    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_leak_guard_in_tree.py -q
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

# ── 扫描范围常量 ──────────────────────────────────────────────────────────
SELF_REL = "worker/tests/test_leak_guard_in_tree.py"  # 本文件含指纹常量，跳过自扫
AUDIT_EXEMPT_PREFIX = "docs/audit/"  # 审计文档：允许指纹引用（见模块 docstring）
MAX_FILE_BYTES = 20 * 1024 * 1024  # 单文件上限（防病态大文件拖垮测试）

# ── 密钥指纹（只写前缀；完整密钥绝不进本文件）────────────────────────────
F_STORE_4718259 = "cd1d0a10-181a"  # Ozon 店铺 4718259 api_key
F_STORE_ALT = "0b4d15cf-70a2"  # 另一 Ozon 店铺 api_key
F_STORE_REPAIR = "db64d282-"  # 已清欠的 repair_cards 泄漏 key（余指纹引用）
F_STORE_16D = "16d3650e-"  # pipeline.json（已删除）内嵌店铺 key，防再引入
F_STORE_4BC = "4bc7a919-"
F_STORE_BCF = "bcfd9c1c-"
F_STORE_BD0 = "bd01d353-"
F_MXOU_MAIN = "Ccpo3ziB"  # MXOU token（sk- 前缀 48 位）
F_MXOU_ALT = "ODyGgd9"  # 真实格式 48 位 MXOU key（balance 夹具曾用）
F_MXOU_2C9 = "sk-2C9"  # pipeline.json（已删除）内嵌 MXOU token 变体
F_GRSAI = "sk-fbee"  # GRSAI 生图 key
F_AIBUY = "6499814d"  # 1688 mtop _m_h5_tk cookie
F_SENTRY = "a2491a43"  # Sentry ingest key
F_SUPABASE = "eyJpc3MiOiJzdXBhYmFzZ"  # Supabase JWT issuer 段——泛 JWT 头
# （eyJhbGci…）不命中，只有真 Supabase 签发的 service_role 长串才含此段

STORE_KEY_FPS = (F_STORE_4718259, F_STORE_ALT, F_STORE_REPAIR, F_STORE_16D, F_STORE_4BC, F_STORE_BCF, F_STORE_BD0)
TOKEN_FPS = (F_MXOU_MAIN, F_MXOU_ALT, F_MXOU_2C9, F_GRSAI, F_AIBUY)
SENTRY_SUPABASE_FPS = (F_SENTRY, F_SUPABASE)
ALL_FPS = STORE_KEY_FPS + TOKEN_FPS + SENTRY_SUPABASE_FPS

# ── 已知残留登记（ratchet：只减不增；清欠一批就删对应条目）────────────────
# 每个 value 必须与该文件当前实际命中的指纹集合精确一致（少=登记未收窄，多=新泄漏）。
# 2026-09-11 清欠完毕：真实密钥全部脱敏/untrack/删除，仅剩 config_store 的
# Sentry DSN（公开标识符，在库设计决策，注释在 config_store.py）。
KNOWN_REMAINING: dict[str, set[str]] = {
    "skill/scripts/lib/config_store.py": {F_SENTRY},
}

# .gitleaks.toml [allowlist].paths 放行段的期望精确集合（仅 2 条误报——真实残留已清欠）。
EXPECTED_GITLEAKS_TOML_PATHS = frozenset({
    r"^worker/tests/test_mxou_login_api\.py$",
    r"^worker/assets/supabase_tables_creation\.sql$",
})


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=True
    )
    return Path(out.stdout.decode().strip())


def _tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    )
    return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def _scan(root: Path, fingerprints, files=None) -> dict[str, set[str]]:
    """返回 {相对路径: 命中指纹集合}。files=None 时扫全部跟踪文件。"""
    hits: dict[str, set[str]] = {}
    for rel in files if files is not None else _tracked_files(root):
        if rel == SELF_REL or rel.startswith(AUDIT_EXEMPT_PREFIX):
            continue
        f = root / rel
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        found = {fp for fp in fingerprints if fp in text}
        if found:
            hits[rel] = found
    return hits


def _fmt(hits: dict[str, set[str]]) -> str:
    return "\n".join(f"  {k}: {sorted(v)}" for k, v in sorted(hits.items())) or "  (无)"


# ── 用例 ──────────────────────────────────────────────────────────────────

def _family_expected(family) -> dict[str, set[str]]:
    """KNOWN_REMAINING 中该指纹族的期望子集（值也按族收窄，与 _scan 口径一致）。"""
    fam = set(family)
    return {k: (v & fam) for k, v in KNOWN_REMAINING.items() if v & fam}


def test_store_api_key_fingerprints_absent():
    root = _repo_root()
    hits = _scan(root, STORE_KEY_FPS)
    expected = _family_expected(STORE_KEY_FPS)
    assert hits == expected, (
        "店铺 api_key 指纹命中偏离登记（新泄漏不得入库；清欠后请收窄 KNOWN_REMAINING）:\n"
        f"实际:\n{_fmt(hits)}\n登记:\n{_fmt(expected)}"
    )


def test_token_fingerprints_absent():
    root = _repo_root()
    hits = _scan(root, TOKEN_FPS)
    expected = _family_expected(TOKEN_FPS)
    assert hits == expected, (
        "token/key 指纹命中偏离登记:\n"
        f"实际:\n{_fmt(hits)}\n登记:\n{_fmt(expected)}"
    )


def test_sentry_supabase_fingerprints_absent():
    root = _repo_root()
    hits = _scan(root, SENTRY_SUPABASE_FPS)
    expected = _family_expected(SENTRY_SUPABASE_FPS)
    assert hits == expected, (
        "Sentry/Supabase 指纹命中偏离登记:\n"
        f"实际:\n{_fmt(hits)}\n登记:\n{_fmt(expected)}"
    )


def test_ratchet_register_consistent_and_scanner_alive(tmp_path):
    """①全量扫描 == KNOWN_REMAINING 精确集合（双向锁定：新泄漏红/清欠提醒收窄）；
    ②.gitleaks.toml 放行段 == 期望精确集合（防静默扩债）；
    ③扫描器自检（临时文件植入假指纹必须被检出，防 git ls-files 失败时静默空转）。"""
    root = _repo_root()

    # ① ratchet 双向精确匹配
    hits = _scan(root, ALL_FPS)
    assert hits == KNOWN_REMAINING, (
        "指纹命中与已知残留登记不一致:\n"
        f"实际:\n{_fmt(hits)}\n登记:\n{_fmt(KNOWN_REMAINING)}\n"
        "若为新泄漏 → 立即轮换密钥并脱敏（勿进登记）；若为清欠 → 同步收窄登记。"
    )

    # ② toml 放行段锁定
    toml_path = root / ".gitleaks.toml"
    allow_paths = set(tomllib.loads(toml_path.read_text("utf-8"))["allowlist"]["paths"])
    assert allow_paths == EXPECTED_GITLEAKS_TOML_PATHS, (
        ".gitleaks.toml [allowlist].paths 偏离登记（放行只能收窄不能新增）:\n"
        f"实际: {sorted(allow_paths)}\n期望: {sorted(EXPECTED_GITLEAKS_TOML_PATHS)}"
    )

    # ③ 扫描器自检：植入假指纹必须检出（证明非静默空扫）
    plant = tmp_path / "planted.txt"
    plant.write_text(
        f'{{"ozon_api_key": "{F_STORE_4718259}-0000-0000-0000-000000000000"}}',
        encoding="utf-8",
    )
    planted = _scan(root, ALL_FPS, files=[str(plant)])
    assert planted == {str(plant): {F_STORE_4718259}}, f"扫描器自检失败: {planted}"
