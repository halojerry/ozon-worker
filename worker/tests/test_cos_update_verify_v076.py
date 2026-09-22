"""T32(cicd-H2): COS 升级链 manifest 签名校验 — deploy/verify_manifest.sh 矩阵测试。

四层（PLAN-security-remediation-v1 Task 32 + 评审 fix round 1）：
1. 结构锁定（无需 minisign）：bash -n 语法 + 三处接线断言 + verify 调用 3 参
   断言（评审 Major-1：不传 expected_version）。
2. verify_manifest.sh 行为矩阵（需 minisign，缺失跳过）：临时 keypair 只落
   pytest tmp_path，**绝不提交任何私钥材料进仓库**（本任务红线）。
3. cos-update.sh 接线级测试（stub curl/minisign/docker，无需 minisign）：
   锁「非最新表内版本请求通过验签段、正确取版本表 sha256 并发起下载」——
   评审 Major-1 指出的 wiring 洞正是没有接线测试才漏的。整脚本跑到 §3 下载
   断点（stub curl 对包 URL 控制性失败 rc=22），绝不触及 §4 备份等变更性操作。
4. sign_cache_hashes.sh（评审 Medium-2：cache_sha256 生产者）stub 测试 +
   minisign-gated 端到端。

运行：
    cd worker && PYTHONPATH=src python -m pytest tests/test_cos_update_verify_v076.py -q
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
VERIFY_SCRIPT = DEPLOY_DIR / "verify_manifest.sh"
COS_UPDATE_SCRIPT = DEPLOY_DIR / "cos-update.sh"
SIGN_CACHE_SCRIPT = DEPLOY_DIR / "sign_cache_hashes.sh"
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


def _flip_last_hex_byte(text: str) -> str:
    """翻转 sha256 字段末位 hex——真·一字节改动（评审 Low-4：原写法实改 32 字节）。"""
    flipped = "0" if text[-3] != "0" else "1"  # text 尾部为 <hex>"}
    out = text[:-3] + flipped + text[-2:]
    assert out != text and len(out) == len(text)
    return out


# ═══ 第 1 层：结构锁定（无需 minisign）═══

def test_verify_and_cos_update_scripts_bash_syntax():
    for script in (VERIFY_SCRIPT, COS_UPDATE_SCRIPT, SIGN_CACHE_SCRIPT):
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


def test_cos_update_verify_invocation_takes_exactly_three_args():
    """评审 Major-1 接线锁: verify 调用不得传 expected_version——其语义是等于
    manifest 顶层 version（恒最新版），传指定版本会 rc=4 拒绝一切非最新回滚，
    签名版本表全体不可达。版本绑定由「签名覆盖版本表 + 表内 sha256 相等」承担。"""
    text = COS_UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert 'bash "$VERIFY_SCRIPT" "$PUBKEY_FILE" "$MANIFEST_FILE" "$MANIFEST_SIG_FILE"' in text, \
        "verify 调用必须恰好 3 参"
    assert "_expect_v" not in text, "expected_version 接线残留（会被顶层恒最新版语义拒绝回滚）"


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


def test_sign_cache_hashes_tool_wiring():
    """评审 Medium-2: cache_sha256 生产者工具必须存在且具备整表替换+重签语义。"""
    text = SIGN_CACHE_SCRIPT.read_text(encoding="utf-8")
    assert "cache_sha256" in text
    assert "minisign -S" in text, "必须重签"
    assert "verify_manifest.sh" in text, "必须支持公钥回验自检"


# ═══ 第 2 层：verify_manifest.sh 可机测路径（无需 minisign——文件/参数检查
# 先于 command -v minisign 判定；评审 Low-3：从 gated 类移出）═══

def _rc2_case(pub: Path, manifest: Path, sig: Path, *extra: str):
    cmd = ["bash", str(VERIFY_SCRIPT), str(pub), str(manifest), str(sig), *extra]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_verify_missing_sig_file_exit_2(tmp_path):
    pub = tmp_path / "d.pub"
    pub.write_text("stub")
    manifest = tmp_path / "m.json"
    manifest.write_text(MANIFEST_TEXT)
    proc = _rc2_case(pub, manifest, tmp_path / "nope.sig")
    assert proc.returncode == 2


def test_verify_missing_pub_file_exit_2(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(MANIFEST_TEXT)
    sig = tmp_path / "m.sig"
    sig.write_text("stub")
    proc = _rc2_case(tmp_path / "nope.pub", manifest, sig)
    assert proc.returncode == 2


def test_verify_wrong_argc_exit_2(tmp_path):
    pub = tmp_path / "d.pub"
    pub.write_text("stub")
    manifest = tmp_path / "m.json"
    manifest.write_text(MANIFEST_TEXT)
    proc = subprocess.run(["bash", str(VERIFY_SCRIPT), str(pub), str(manifest)],
                          capture_output=True, text=True)
    assert proc.returncode == 2


# ═══ 第 2b 层：verify_manifest.sh 行为矩阵（需 minisign）═══

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
        tampered.write_text(_flip_last_hex_byte(MANIFEST_TEXT), encoding="utf-8")  # 真·一字节
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


# ═══ 第 3 层：cos-update.sh 接线级测试（stub curl/minisign/docker，无需 minisign）═══
# 整脚本从 §0 跑到 §3 下载断点：stub curl 对包 URL 控制性失败(rc=22) → 脚本在
# §3 fail 退出——绝不触及 §4 备份/§5 覆盖等变更性操作，宿主文件零改动。

def _write_stub_exec(path: Path, content: str):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


STUB_CURL = r"""#!/usr/bin/env bash
# 测试 stub: 截获 cos-update.sh 的 curl——manifest/sig 回 fixture，包 URL rc=22 断点
out=""; url=""
args=("$@")
for i in "${!args[@]}"; do
  if [ "${args[$i]}" = "-o" ]; then out="${args[$((i + 1))]}"; fi
done
url="${args[${#args[@]} - 1]}"
echo "curl $url" >> "${STUB_LOG:?}"
case "$url" in
  */manifest.json) cp "${STUB_FIXTURE_DIR}/manifest.json" "$out"; exit 0 ;;
  *.sig)           cp "${STUB_FIXTURE_DIR}/manifest.sig" "$out"; exit 0 ;;
  *)               echo "stub: 拒绝下载 $url（测试断点）" >> "${STUB_LOG:?}"; exit 22 ;;
esac
"""

STUB_MINISIGN = r"""#!/usr/bin/env bash
echo "minisign $*" >> "${STUB_LOG:?}"
if [ -n "${STUB_MINISIGN_RC:-}" ]; then exit "$STUB_MINISIGN_RC"; fi
case "$1" in
  -V|-S) exit 0 ;;
  *)     exit 0 ;;
esac
"""

STUB_NOOP = r"""#!/usr/bin/env bash
exit 0
"""


def _wiring_sandbox(tmp_path: Path) -> Path:
    """搭 cos-update.sh 沙箱：deploy/(脚本+pub) + stub/ + fixtures/，返回根目录。"""
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copy2(COS_UPDATE_SCRIPT, deploy / "cos-update.sh")
    shutil.copy2(VERIFY_SCRIPT, deploy / "verify_manifest.sh")
    (deploy / "cos-update.pub").write_text("untrusted comment: stub\nAAAA\n", encoding="utf-8")
    stub = tmp_path / "stub"
    stub.mkdir()
    _write_stub_exec(stub / "curl", STUB_CURL)
    _write_stub_exec(stub / "minisign", STUB_MINISIGN)
    _write_stub_exec(stub / "docker", STUB_NOOP)
    (tmp_path / "fixtures").mkdir()
    return tmp_path


def _run_cos_update(root: Path, *args: str, extra_env: dict | None = None):
    env = {
        "PATH": f"{root / 'stub'}:/usr/bin:/bin",
        "COS_BUCKET": "stubbucket",
        "COS_REGION": "stubregion",
        "STUB_LOG": str(root / "stub.log"),
        "STUB_FIXTURE_DIR": str(root / "fixtures"),
        "HOME": str(root),
    }
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", "deploy/cos-update.sh", *args],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=60,
    )


def _wiring_manifest() -> str:
    """顶层 v0.99.0（恒最新语义），版本表含历史 v0.74.0——回滚目标。"""
    return json.dumps({
        "version": "v0.99.0",
        "package": "ozon-worker-deploy-v0.99.0.tar.gz",
        "sha256": _HEX_A + _HEX_A.replace("a", "b"),
        "versions": {
            "v0.99.0": {"package": "ozon-worker-deploy-v0.99.0.tar.gz",
                        "sha256": _HEX_A + _HEX_A.replace("a", "b"),
                        "released_at": "x"},
            "v0.74.0": {"package": "ozon-worker-deploy-v0.74.0.tar.gz",
                        "sha256": _HEX_A.replace("a", "c") + _HEX_A.replace("a", "d"),
                        "released_at": "y"},
        },
        "cache_sha256": {},
    })


class TestCosUpdateWiring:
    @pytest.fixture(autouse=True)
    def _sandbox(self, tmp_path):
        self.root = _wiring_sandbox(tmp_path)
        (self.root / "fixtures" / "manifest.json").write_text(_wiring_manifest(), encoding="utf-8")
        (self.root / "fixtures" / "manifest.sig").write_text("stub signature", encoding="utf-8")
        self.stub_log = self.root / "stub.log"

    def _log(self) -> str:
        return self.stub_log.read_text(encoding="utf-8") if self.stub_log.exists() else ""

    def test_nonlatest_rollback_passes_verify_and_downloads_table_package(self):
        """评审 Major-1 回归锁: 非最新表内版本必须通过验签段、按版本表 sha256
        前缀包名发起下载——修复前此场景在 verify 段即 rc=4→exit 3，回滚全废。"""
        proc = _run_cos_update(self.root, "v0.74.0")
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1, f"应在 §3 下载断点 fail(rc=1): {out}"
        assert "✅ manifest 签名校验通过" in out, "指定版本必须过验签段（不传 expected_version）"
        assert "指定版本: v0.74.0" in out
        assert "指定版本走签名 manifest 版本表: v0.74.0 → ozon-worker-deploy-v0.74.0.tar.gz" in out
        log = self._log()
        assert "ozon-worker/ozon-worker-deploy-v0.74.0.tar.gz" in log, \
            f"必须按版本表包名发起下载: {log}"
        assert "ozon-worker-deploy-v0.99.0.tar.gz" not in log, "不得误下最新包"

    def test_latest_path_still_works(self):
        proc = _run_cos_update(self.root)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1  # 同样止于 §3 下载断点
        assert "✅ manifest 签名校验通过" in out
        assert "最新版本: v0.99.0 (ozon-worker-deploy-v0.99.0.tar.gz)" in out
        assert "ozon-worker/ozon-worker-deploy-v0.99.0.tar.gz" in self._log()

    def test_verify_failure_propagates_exit_3_and_no_download(self):
        proc = _run_cos_update(self.root, "v0.74.0", extra_env={"STUB_MINISIGN_RC": "3"})
        out = proc.stdout + proc.stderr
        assert proc.returncode == 3, f"验签失败必须 exit 3: {out}"
        assert "manifest 签名校验未通过" in out
        assert "ozon-worker-deploy" not in self._log().split("manifest.json.sig")[-1], \
            "验签失败后不得发起包下载"

    def test_missing_pub_exit_3_before_any_download(self):
        (self.root / "deploy" / "cos-update.pub").unlink()
        proc = _run_cos_update(self.root, "v0.74.0")
        out = proc.stdout + proc.stderr
        assert proc.returncode == 3
        assert "公钥不存在" in out
        assert "ozon-worker-deploy" not in self._log()


# ═══ 第 4 层：sign_cache_hashes.sh（评审 Medium-2: cache_sha256 生产者）═══

def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestSignCacheHashes:
    @pytest.fixture(autouse=True)
    def _sandbox(self, tmp_path):
        self.root = tmp_path
        self.deploy = self.root / "deploy"
        self.deploy.mkdir()
        shutil.copy2(SIGN_CACHE_SCRIPT, self.deploy / "sign_cache_hashes.sh")
        shutil.copy2(VERIFY_SCRIPT, self.deploy / "verify_manifest.sh")
        self.stub = self.root / "stub"
        self.stub.mkdir()
        _write_stub_exec(self.stub / "minisign", STUB_MINISIGN)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        (self.cache / "attribute_schemas_zh.json").write_text('{"k": "v1"}', encoding="utf-8")
        (self.cache / "dictionary_values_zh.json").write_text('{"k": "v2"}', encoding="utf-8")
        (self.cache / "not_json.txt").write_text("stray", encoding="utf-8")
        base = {"version": "v0.99.0", "package": "p", "sha256": _HEX_A + _HEX_A.replace("a", "b")}
        base["cache_sha256"] = {"stale.json": "deadbeef"}  # 陈旧条目，应被整表替换清除
        self.manifest_in = self.root / "manifest_in.json"
        self.manifest_in.write_text(json.dumps(base), encoding="utf-8")
        (self.root / "stub.sec").write_text("untrusted comment: stub\nAAAA\n", encoding="utf-8")

    def _run(self, *args: str):
        env = {
            "PATH": f"{self.stub}:/usr/bin:/bin",
            "STUB_LOG": str(self.root / "stub.log"),
            "HOME": str(self.root),
        }
        return subprocess.run(
            ["bash", "deploy/sign_cache_hashes.sh", *args],
            cwd=str(self.root), env=env, capture_output=True, text=True, timeout=60,
        )

    def test_injects_hashes_replaces_stale_table_and_keeps_other_fields(self):
        out_dir = self.root / "out"
        proc = self._run(str(self.cache), str(self.manifest_in), "stub.sec", str(out_dir))
        assert proc.returncode == 0, f"应成功: {proc.stdout} {proc.stderr}"
        m = _read_manifest(out_dir / "manifest.json")
        assert set(m["cache_sha256"]) == {"attribute_schemas_zh.json", "dictionary_values_zh.json"}, \
            "只登记 .json（忽略 stray）且陈旧条目被整表替换"
        for name, digest in m["cache_sha256"].items():
            expect = hashlib.sha256((self.cache / name).read_bytes()).hexdigest()
            assert digest == expect, f"{name} 哈希不符"
        assert m["version"] == "v0.99.0" and m["package"] == "p", "其余字段必须原样保留"
        text = (out_dir / "manifest.json").read_text(encoding="utf-8")
        assert "\n" not in text, "输出必须单行紧凑 JSON（与 cd.yml 口径一致）"
        stub_log = (self.root / "stub.log").read_text(encoding="utf-8")
        assert "-S" in stub_log and str(out_dir / "manifest.sig") in stub_log, "必须发起 minisign -S 重签"

    def test_empty_cache_dir_exit_2(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        proc = self._run(str(empty), str(self.manifest_in), "stub.sec", str(tmp_path / "o"))
        assert proc.returncode == 2

    def test_missing_key_exit_2(self, tmp_path):
        proc = self._run(str(self.cache), str(self.manifest_in),
                         str(tmp_path / "nope.sec"), str(tmp_path / "o"))
        assert proc.returncode == 2

    def test_wrong_argc_exit_2(self):
        proc = self._run(str(self.cache))
        assert proc.returncode == 2

    @pytest.mark.skipif(MINISIGN_BIN is None, reason="minisign 未安装——端到端留待有 minisign 的环境")
    def test_end_to_end_with_real_minisign(self, tmp_path_factory):
        """真 minisign keypair 端到端：签出的 manifest 必须过 verify_manifest.sh。"""
        pair = _make_pair(tmp_path_factory.mktemp("t32signpair"), "s")
        out_dir = self.root / "out_e2e"
        proc = subprocess.run(
            ["bash", "deploy/sign_cache_hashes.sh", str(self.cache), str(self.manifest_in),
             str(pair[1]), str(out_dir), str(pair[0])],  # 第 5 参 pub → 脚本内回验自检
            cwd=str(self.root), capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, f"端到端应成功: {proc.stdout} {proc.stderr}"
        assert (out_dir / "manifest.sig").exists()
        # 独立回验（不经脚本自检路径）
        v = _verify(pair[0], out_dir / "manifest.json", out_dir / "manifest.sig")
        assert v.returncode == 0, f"产出 manifest 必须过验签: {v.stderr}"
