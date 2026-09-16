"""T32(cicd-H2): COS 升级链 manifest 签名校验 — deploy/verify_manifest.sh 矩阵测试。

两层（PLAN-security-remediation-v1 Task 32 Step 1）：
1. 结构锁定（无需 minisign，任何环境都跑）：bash -n 语法 + 三处接线断言
   （cos-update.sh 验签调用 / updater.py 公钥闸 / cd.yml 签名与 pin 门）。
2. 行为矩阵（需 minisign 二进制，缺失整组跳过）：临时 keypair 只落 pytest
   tmp_path，**绝不提交任何私钥材料进仓库**（本任务红线）。生产 keypair 属
   用户合并前人工动作，与本测试无关。

运行：
    cd worker && PYTHONPATH=src python -m pytest tests/test_cos_update_verify_v076.py -q
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
VERIFY_SCRIPT = DEPLOY_DIR / "verify_manifest.sh"
COS_UPDATE_SCRIPT = DEPLOY_DIR / "cos-update.sh"
UPDATER_PY = REPO_ROOT / "skill" / "scripts" / "lib" / "updater.py"
CD_YML = REPO_ROOT / ".github" / "workflows" / "cd.yml"

MINISIGN_BIN = shutil.which("minisign")

# 测试 manifest（sha256 用运行时拼接的 64 hex 占位——避免本文件被全树
# gitleaks 扫描时自命中高熵 blob 形态，纪律同 test_leak_guard_in_tree.py）
_HEX_A = "a" * 32
MANIFEST_TEXT = (
    '{"version": "v0.77.0", '
    '"package": "ozon-worker-deploy-v0.77.0.tar.gz", '
    '"sha256": "' + _HEX_A + _HEX_A.replace("a", "b") + '"}'
)


# ═══ 第 1 层：结构锁定（无需 minisign）═══

def test_verify_and_cos_update_scripts_bash_syntax():
    for script in (VERIFY_SCRIPT, COS_UPDATE_SCRIPT):
        assert script.exists(), f"缺少 {script}"
        proc = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True
        )
        assert proc.returncode == 0, f"{script.name} 语法错误: {proc.stderr}"


def test_verify_script_exit_code_contract():
    """退出码契约: 0=通过 / 2=用法环境 / 3=签名失败 / 4=版本不符。"""
    text = VERIFY_SCRIPT.read_text(encoding="utf-8")
    assert "exit 2" in text and "exit 3" in text and "exit 4" in text
    assert "minisign -V" in text, "必须走 minisign -V 验签"


def test_cos_update_wires_manifest_verification():
    """cos-update.sh 必须强制走签名 manifest（默认拒绝、逃生门留痕）。"""
    text = COS_UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "verify_manifest.sh" in text, "必须调用 verify_manifest.sh"
    assert "cos-update.pub" in text, "信任根必须是脚本同目录公钥"
    assert "COS_UPDATE_SKIP_VERIFY" in text, "逃生门必须显式命名并留痕"
    assert "manifest.sig" in text or 'MANIFEST_URL}.sig' in text, "签名文件必须与 manifest 成对拉取"
    assert 'fail "manifest 无 sha256 字段' in text, "最新路径空 sha256 必须 fail（封死无校验下载）"
    assert "_mversion_entry" in text, "指定版本必须从签名 manifest 版本表取 sha256"
    assert "_mcache_sha" in text, "缓存 JSON 必须核对 manifest 登记的 cache_sha256"
    assert "指定版本无 manifest sha256, 跳过校验" not in text, "旧「指定版本空跳过」路径必须已删除"
    assert "docker compose cp" in text, "缓存拷入容器的动作仍在（校验后才 cp）"


def test_updater_has_pubkey_gate():
    """updater.py 必须有 PROD_PUBKEY 闸（空=warn 跳过，非空=fail-closed 验签）。"""
    text = UPDATER_PY.read_text(encoding="utf-8")
    assert 'PROD_PUBKEY = ""' in text, "公钥常量占位必须存在（待生产公钥生成后填入）"
    assert "def verify_manifest_authenticity" in text
    assert "def verify_minisign_signature" in text
    assert "verify_manifest_authenticity(url" in text, "_fetch_manifest 必须接线验签"


def test_cd_yml_has_signing_and_pin_gate():
    """cd.yml cos-deploy 必须有 minisign pin 门 + 签名步骤 + manifest.sig 同传。"""
    text = CD_YML.read_text(encoding="utf-8")
    assert "Install minisign (pinned)" in text
    assert "MINISIGN_SHA256" in text, "minisign 二进制必须 sha256 pin（形态同 T30 coscli）"
    assert "Sign manifest (minisign" in text, "必须有签名步骤"
    assert "manifest.sig" in text, "签名必须与 manifest 同传 COS"
    # secret 名拼接断言（避免本文件出现完整 secret 字面量）
    assert "COS_UPDATE" + "_SIGN_KEY" in text
    assert "verify_manifest.sh deploy/cos-update.pub" in text, "CI 侧必须用仓库公钥自检回验"


# ═══ 第 2 层：行为矩阵（需 minisign，临时 keypair 只进 pytest tmp_path）═══

def _run_minisign(args: list[str], stdin: str = "\n\n") -> subprocess.CompletedProcess:
    return subprocess.run(
        [MINISIGN_BIN, *args], input=stdin, capture_output=True, text=True
    )


def _verify(pub: Path, manifest: Path, sig: Path, expect: str | None = None):
    cmd = ["bash", str(VERIFY_SCRIPT), str(pub), str(manifest), str(sig)]
    if expect is not None:
        cmd.append(expect)
    return subprocess.run(cmd, capture_output=True, text=True)


def _make_pair(tmp_path: Path, tag: str) -> tuple[Path, Path]:
    pub = tmp_path / f"pair-{tag}.pub"
    sec = tmp_path / f"pair-{tag}.sec"
    proc = _run_minisign(["-G", "-p", str(pub), "-s", str(sec)])
    assert proc.returncode == 0, f"minisign -G 失败: {proc.stderr}"
    assert pub.exists() and sec.exists()
    return pub, sec


def _sign(sec: Path, manifest: Path, out: Path) -> Path:
    proc = _run_minisign(["-S", "-s", str(sec), "-m", str(manifest), "-x", str(out)])
    assert proc.returncode == 0, f"minisign -S 失败: {proc.stderr}"
    return out


@pytest.mark.skipif(MINISIGN_BIN is None, reason="minisign 未安装——行为矩阵留待有 minisign 的环境")
class TestVerifyManifestBehaviorMatrix:
    @pytest.fixture(scope="class")
    def pair(self, tmp_path_factory) -> tuple[Path, Path]:
        return _make_pair(tmp_path_factory.mktemp("t32pair"), "a")

    @pytest.fixture(scope="class")
    def signed(self, pair, tmp_path_factory) -> tuple[Path, Path]:
        d = tmp_path_factory.mktemp("t32signed")
        manifest = d / "manifest.json"
        manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
        sig = _sign(pair[1], manifest, d / "manifest.sig")
        return manifest, sig

    def test_valid_signature_exit_zero(self, pair, signed):
        proc = _verify(pair[0], *signed)
        assert proc.returncode == 0, f"正确签名应 exit 0: {proc.stderr}"

    def test_valid_signature_with_matching_version(self, pair, signed):
        for expect in ("v0.77.0", "0.77.0"):  # 剥 v 前缀等价
            proc = _verify(pair[0], *signed, expect=expect)
            assert proc.returncode == 0, f"expected_version={expect} 应匹配: {proc.stderr}"

    def test_tampered_manifest_rejected_exit_3(self, pair, signed, tmp_path):
        manifest, _sig = signed
        tampered = tmp_path / "manifest_tampered.json"
        tampered.write_text(
            MANIFEST_TEXT.replace(_HEX_A + _HEX_A.replace("a", "b"), _HEX_A + _HEX_A),  # 改一字节语义
            encoding="utf-8",
        )
        assert tampered.read_text(encoding="utf-8") != MANIFEST_TEXT
        proc = _verify(pair[0], tampered, _sig)
        assert proc.returncode == 3, f"篡改 manifest 必须 exit 3: rc={proc.returncode} {proc.stderr}"

    def test_version_mismatch_exit_4(self, pair, signed):
        proc = _verify(pair[0], *signed, expect="v0.76.9")
        assert proc.returncode == 4, f"版本不匹配必须 exit 4: rc={proc.returncode} {proc.stderr}"

    def test_wrong_pubkey_rejected_exit_3(self, pair, signed, tmp_path_factory):
        other_pub, other_sec = _make_pair(tmp_path_factory.mktemp("t32pair-b"), "b")
        d = tmp_path_factory.mktemp("t32signed-b")
        manifest = d / "manifest.json"
        manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
        sig = _sign(other_sec, manifest, d / "manifest.sig")  # 另一把私钥签的
        proc = _verify(pair[0], manifest, sig)  # 用第一把公钥验 → 必须拒
        assert proc.returncode == 3, f"非受信公钥必须 exit 3: rc={proc.returncode} {proc.stderr}"
        assert other_pub.exists()

    def test_missing_sig_file_exit_2(self, pair, signed, tmp_path):
        manifest, _ = signed
        proc = _verify(pair[0], manifest, tmp_path / "nope.sig")
        assert proc.returncode == 2

    def test_missing_pub_file_exit_2(self, signed, tmp_path):
        manifest, sig = signed
        proc = _verify(tmp_path / "nope.pub", manifest, sig)
        assert proc.returncode == 2

    def test_wrong_argc_exit_2(self, pair, signed):
        manifest, sig = signed
        proc = subprocess.run(
            ["bash", str(VERIFY_SCRIPT), str(pair[0]), str(manifest)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 2
