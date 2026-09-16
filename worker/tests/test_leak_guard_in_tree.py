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

crypto-M3 扩展（2026-09-16）：.gitleaks.toml 自定义规则组（密码赋值 + 无关键字
高熵 blob，补默认规则盲区）同样在此锁定——规则存在性/entropy 阈值/合成样本实扫
命中/规则级 allowlist 精确集合，见文件尾部「crypto-M3」节。

运行（无需 PG）::

    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_leak_guard_in_tree.py -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

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


# ── crypto-M3：gitleaks 自定义规则组（非标形态补漏）────────────────────────
# 审计实测：`password = "..."` 赋值与无关键字高熵 blob 均能漏过 gitleaks 默认规则；
# 上方指纹 ratchet 只锁已知泄漏，不防新形态。对策：.gitleaks.toml 追加两条自定义
# 规则（密码赋值 + 无关键字高熵长串启发式），此处三层锁定：
#   ① 规则存在且 entropy 阈值正确（纯 toml 解析，无需 gitleaks 二进制）；
#   ② 合成样本必须被对应规则命中（gitleaks 子进程实扫；缺二进制 skipif 跳过）；
#   ③ 规则级 allowlist 逐条登记进期望精确集合（只减不增，防豁免段静默扩债）。

EXPECTED_CUSTOM_RULE_ENTROPY = {
    "ozon-custom-password-assign": 3.0,
    "ozon-custom-high-entropy-blob": 4.2,
}

# 合成样本运行时拼接（字面量故意拆段）——否则完整形态落在本文件里，CI 全树
# gitleaks 扫描会命中测试文件自身，形成自触发泄漏。
_SAMPLE_PWD_TAIL = "high-entropy-value-123"
_SAMPLE_BLOB_TAIL = "Hx9pQ_w3rTy7Km2vBn5c" + "Zd8aSf4gJl6eUo0i1y2"
SYNTHETIC_SAMPLES = {
    "ozon-custom-password-assign": 'password = "' + _SAMPLE_PWD_TAIL + '"',
    "ozon-custom-high-entropy-blob": 'token_blob = "' + _SAMPLE_BLOB_TAIL + '"',
}

GITLEAKS_BIN = shutil.which("gitleaks")

# ③ 规则级 allowlist 期望精确集合（只减不增）。逐条豁免原因注释在 .gitleaks.toml
# 对应条目旁；此处锁「集合恒等」，静默扩债（新增放行）即红，清欠收窄需同步删登记。
# 2026-09-16 全仓实扫基线：password 规则 7 命中 / blob 规则 848 命中，全为
# 假夹具/占位符/vendored 公开参考快照/lockfile 校验和，零真实凭证。
EXPECTED_CUSTOM_RULE_ALLOWLIST_REGEXES: dict[str, frozenset[str]] = {
    "ozon-custom-password-assign": frozenset({
        "SUPERSECRET(VALUE123|COOKIEVALUE)",
        "password123",
        "s3cr3t-密码-@!xYz",
        "your-perf-secret",
    }),
    "ozon-custom-high-entropy-blob": frozenset({
        "sha512-[A-Za-z0-9+/=]{16,}",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "REPORT_seller_",
        "/category/mini-trenazhery-101029485/",
        "gAOImrvbD3dwTYuK2kuZ1ilQWQCS0Vl4yz",
        "BKys-7El6gMgJv4_rpqToqBTfYzeZVAPkoirMwbtuNf6",
        # ak_callback AK 字符合法集常量（跨版本熵漂移误报，见 .gitleaks.toml 同条注释）
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    }),
}
EXPECTED_CUSTOM_RULE_ALLOWLIST_PATHS: dict[str, frozenset[str]] = {
    "ozon-custom-password-assign": frozenset({
        r"^docs/PLAN-security-remediation-v1\.md$",
    }),
    "ozon-custom-high-entropy-blob": frozenset({
        r"^docs/refs/ozon-mcp/data/(seller|perf)_swagger\.json$",
        r"^docs/data/ozon-api-docs-2026-07-05\.json$",
    }),
}


def _load_gitleaks_rules() -> dict:
    cfg = tomllib.loads((_repo_root() / ".gitleaks.toml").read_text("utf-8"))
    return {r["id"]: r for r in cfg.get("rules", [])}


def test_custom_rules_defined_in_config():
    """①自定义规则组存在且 entropy 阈值正确（无需 gitleaks 二进制即可锁）。"""
    by_id = _load_gitleaks_rules()
    missing = set(EXPECTED_CUSTOM_RULE_ENTROPY) - set(by_id)
    assert not missing, f".gitleaks.toml 缺自定义规则: {sorted(missing)}"
    for rid, ent in EXPECTED_CUSTOM_RULE_ENTROPY.items():
        assert float(by_id[rid]["entropy"]) == ent, f"{rid} entropy 阈值漂移"


def test_custom_rule_allowlists_ratcheted():
    """③规则级 allowlist == 期望精确集合（豁免只能收窄不能新增，防静默扩债）。"""
    by_id = _load_gitleaks_rules()
    for rid in EXPECTED_CUSTOM_RULE_ENTROPY:
        rule = by_id.get(rid, {})
        actual_regexes: set[str] = set()
        actual_paths: set[str] = set()
        for al in rule.get("allowlists", []):
            actual_regexes |= set(al.get("regexes", []))
            actual_paths |= set(al.get("paths", []))
        exp_regexes = set(EXPECTED_CUSTOM_RULE_ALLOWLIST_REGEXES.get(rid, frozenset()))
        exp_paths = set(EXPECTED_CUSTOM_RULE_ALLOWLIST_PATHS.get(rid, frozenset()))
        assert actual_regexes == exp_regexes, (
            f"{rid} allowlist regexes 偏离登记（只能收窄）:\n"
            f"实际: {sorted(actual_regexes)}\n期望: {sorted(exp_regexes)}"
        )
        assert actual_paths == exp_paths, (
            f"{rid} allowlist paths 偏离登记（只能收窄）:\n"
            f"实际: {sorted(actual_paths)}\n期望: {sorted(exp_paths)}"
        )


@pytest.mark.skipif(GITLEAKS_BIN is None, reason="gitleaks 未安装（合成样本实扫需二进制）")
def test_custom_rules_fire_on_synthetic_samples(tmp_path):
    """②合成非标形态必须被对应自定义规则命中（gitleaks 实扫临时样本）。

    两个样本分别对应审计实测的两种漏网形态：
    - `password = "..."` 密码赋值（默认规则 0 命中，/tmp 探针取证在案）；
    - 无关键字高熵长串（无 padding、无 API 关键字，默认规则 0 命中）。
    """
    src = tmp_path / "src"
    src.mkdir()
    for i, sample in enumerate(SYNTHETIC_SAMPLES.values()):
        (src / f"planted_{i}.py").write_text(sample + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    subprocess.run(
        [
            GITLEAKS_BIN, "detect", "--no-git",
            "--source", str(src),
            "--config", str(_repo_root() / ".gitleaks.toml"),
            "--report-format", "json", "--report-path", str(report),
            "--exit-code", "0",
        ],
        capture_output=True, check=True,
    )
    findings = json.loads(report.read_text("utf-8")) if report.exists() else []
    rule_ids = {f["RuleID"] for f in findings}
    missing = set(SYNTHETIC_SAMPLES) - rule_ids
    assert not missing, (
        "合成样本未被自定义规则命中（规则失效/entropy 阈值漂移/被误豁免）: "
        f"缺 {sorted(missing)}，实得 {sorted(rule_ids)}"
    )
