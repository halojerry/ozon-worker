#!/usr/bin/env python3
"""T32(cicd-H2): updater.py manifest 签名校验 — 纯 Python Ed25519 / minisign 容器。

三层：
1. RFC 8032 官方测试向量锁定 _ed25519_verify 数学正确性（任何环境都跑）。
2. minisign 容器格式（pub/sig 文件解析、keynum 绑定）+ _fetch_manifest 验签
   集成（PROD_PUBKEY 空=warn 跳过 / 非空=fail-closed），mock requests 零出站。
3. 与真 minisign 二进制交叉验证（skipif 无 minisign；临时 keypair 只落
   pytest tmp_path，绝不进仓库——本任务红线）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_updater_manifest_sig_v076.py -q
"""
from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import updater  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "deploy" / "verify_manifest.sh"

MANIFEST_URL = "https://unit.test.invalid/ozon-skill/manifest.json"

# 测试 manifest（64 hex 占位运行时拼接——避免本文件被全树 gitleaks 自命中）
_HEX_A = "a" * 32
MANIFEST_TEXT = (
    '{"version": "99.0.0", "url": "' + MANIFEST_URL + '/pkg.tar.gz", '
    '"sha256": "' + _HEX_A + _HEX_A.replace("a", "b") + '"}'
)

# ── RFC 8032 官方 Ed25519 测试向量（公开常量，非任何真实私钥材料）──
_RFC8032_V1_PUB = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
_RFC8032_V1_MSG = b""
_RFC8032_V1_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
_RFC8032_V2_PUB = bytes.fromhex(
    "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
_RFC8032_V2_MSG = bytes.fromhex("72")
_RFC8032_V2_SIG = bytes.fromhex(
    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
    "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")

_UNIT_KEYNUM = b"TESTT32!"  # 8 字节单元测试 keynum


def _pub_text(pub: bytes, keynum: bytes = _UNIT_KEYNUM) -> str:
    blob = base64.b64encode(b"Ed" + keynum + pub).decode()
    return f"untrusted comment: minisign public key UNITTEST\n{blob}\n"


def _sig_text(sig: bytes, keynum: bytes = _UNIT_KEYNUM) -> str:
    blob = base64.b64encode(b"Ed" + keynum + sig).decode()
    return (
        "untrusted comment: signature from minisign UNITTEST\n"
        f"{blob}\n"
        "trusted comment: timestamp:0\tfile:manifest.json\n"
        + base64.b64encode(b"\x00" * 64).decode() + "\n"
    )


class _Resp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.encoding = ""


def _install_fetch_mock(monkeypatch, manifest_text: str, sig_text: str,
                        sig_status: int = 200):
    """mock requests.get：manifest 与 .sig 同址分发，零出站。记录调用 URL。"""
    calls: list[str] = []

    def fake_get(url, timeout=None, **kwargs):
        calls.append(url)
        if url == MANIFEST_URL:
            return _Resp(manifest_text)
        if url == MANIFEST_URL + ".sig":
            return _Resp(sig_text, status_code=sig_status)
        return _Resp("", status_code=404)

    monkeypatch.setattr(updater.requests, "get", fake_get)
    return calls


# ═══ 第 1 层：RFC 8032 向量（数学正确性，任何环境）═══

def test_rfc8032_vector1_verify():
    assert updater._ed25519_verify(_RFC8032_V1_SIG, _RFC8032_V1_MSG, _RFC8032_V1_PUB) is True


def test_rfc8032_vector2_verify():
    assert updater._ed25519_verify(_RFC8032_V2_SIG, _RFC8032_V2_MSG, _RFC8032_V2_PUB) is True


def test_rfc8032_tamper_rejected():
    assert updater._ed25519_verify(_RFC8032_V2_SIG, b"73", _RFC8032_V2_PUB) is False
    bad_sig = _RFC8032_V2_SIG[:-1] + bytes([_RFC8032_V2_SIG[-1] ^ 1])
    assert updater._ed25519_verify(bad_sig, _RFC8032_V2_MSG, _RFC8032_V2_PUB) is False
    # 跨向量（错公钥）
    assert updater._ed25519_verify(_RFC8032_V2_SIG, _RFC8032_V2_MSG, _RFC8032_V1_PUB) is False
    # 长度不符 / s>=L 严格性
    assert updater._ed25519_verify(_RFC8032_V2_SIG[:63], _RFC8032_V2_MSG, _RFC8032_V2_PUB) is False
    s_at_L = _RFC8032_V2_SIG[:32] + updater._ED_L.to_bytes(32, "little")
    assert updater._ed25519_verify(s_at_L, _RFC8032_V2_MSG, _RFC8032_V2_PUB) is False


# ═══ 第 2 层：minisign 容器格式 + 验签入口 ══

def test_minisign_container_accepts():
    assert updater.verify_minisign_signature(
        _pub_text(_RFC8032_V1_PUB), _sig_text(_RFC8032_V1_SIG), _RFC8032_V1_MSG
    ) is True


def test_minisign_container_rejects():
    # keynum 不一致（签名与公钥非同源 keypair）
    assert updater.verify_minisign_signature(
        _pub_text(_RFC8032_V1_PUB, b"OTHERKEY"),
        _sig_text(_RFC8032_V1_SIG),
        _RFC8032_V1_MSG,
    ) is False
    # 公钥格式坏（非 base64 / 长度错 / 算法错）
    assert updater.verify_minisign_signature(
        "not a pubkey file\n", _sig_text(_RFC8032_V1_SIG), _RFC8032_V1_MSG) is False
    assert updater.verify_minisign_signature(
        _pub_text(b"\x00" * 32), _sig_text(_RFC8032_V1_SIG), _RFC8032_V1_MSG) is False
    # 签名文件无 blob 行
    assert updater.verify_minisign_signature(
        _pub_text(_RFC8032_V1_PUB), "untrusted comment: x\n", _RFC8032_V1_MSG) is False
    # 消息被篡改
    assert updater.verify_minisign_signature(
        _pub_text(_RFC8032_V1_PUB), _sig_text(_RFC8032_V1_SIG),
        _RFC8032_V1_MSG + b"x") is False


def test_authenticity_empty_pubkey_skips(monkeypatch):
    """PROD_PUBKEY 为空 = 逃生门口径：跳过验签 + 放行（且不拉 .sig）。"""
    monkeypatch.setattr(updater, "PROD_PUBKEY", "")
    calls = _install_fetch_mock(monkeypatch, MANIFEST_TEXT, "")
    data, ok = updater._fetch_manifest(MANIFEST_URL)
    assert ok is True and data is not None and data["version"] == "99.0.0"
    assert calls == [MANIFEST_URL], "公钥为空时不应请求 .sig"


def test_authenticity_message_mismatch_blocked(monkeypatch):
    """公钥已配置 + .sig 可拉但消息与 manifest 不一致 → fail-closed 拦截。"""
    monkeypatch.setattr(updater, "PROD_PUBKEY", _pub_text(_RFC8032_V1_PUB))
    # 向量 1 私钥签的消息是空串, 与 manifest 文本不一致 → 验签必须失败
    # （验签通过的正向路径由第 3 层 minisign 二进制交叉验证覆盖）。
    calls = _install_fetch_mock(monkeypatch, MANIFEST_TEXT, _sig_text(_RFC8032_V1_SIG))
    data, ok = updater._fetch_manifest(MANIFEST_URL)
    assert (data, ok) == (None, False), "签名消息与 manifest 不一致必须拦截"
    assert calls == [MANIFEST_URL, MANIFEST_URL + ".sig"]


def test_authenticity_sig_http_error_blocked(monkeypatch):
    monkeypatch.setattr(updater, "PROD_PUBKEY", _pub_text(_RFC8032_V1_PUB))
    _install_fetch_mock(monkeypatch, MANIFEST_TEXT, "", sig_status=404)
    assert updater._fetch_manifest(MANIFEST_URL) == (None, False)


def test_authenticity_garbage_pubkey_blocked(monkeypatch):
    monkeypatch.setattr(updater, "PROD_PUBKEY", "complete garbage")
    _install_fetch_mock(monkeypatch, MANIFEST_TEXT, _sig_text(_RFC8032_V1_SIG))
    assert updater._fetch_manifest(MANIFEST_URL) == (None, False)


def test_authenticity_network_exception_blocked(monkeypatch):
    monkeypatch.setattr(updater, "PROD_PUBKEY", _pub_text(_RFC8032_V1_PUB))
    monkeypatch.setattr(
        updater.requests, "get",
        mock.Mock(side_effect=ConnectionError("boom")))
    assert updater._fetch_manifest(MANIFEST_URL) == (None, False)


# ═══ 第 2.5 层：与 OpenSSL 独立实现交叉验证（skipif 无 ed25519 能力）═══

def _openssl_has_ed25519() -> bool:
    if shutil.which("openssl") is None:
        return False
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.run(
                ["openssl", "genpkey", "-algorithm", "ed25519", "-out", f"{td}/k.pem"],
                capture_output=True)
            return proc.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _openssl_has_ed25519(),
                    reason="openssl 不可用或无 ed25519 能力")
def test_cross_validation_with_openssl(tmp_path):
    """OpenSSL（独立实现）签名 ↔ 纯 Python 验签一致（正向+篡改）。"""
    sec = tmp_path / "o.pem"
    pub_pem = tmp_path / "p.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(sec)],
                   capture_output=True, check=True)
    subprocess.run(["openssl", "pkey", "-in", str(sec), "-pubout", "-out", str(pub_pem)],
                   capture_output=True, check=True)
    msg = tmp_path / "m.bin"
    payload = b"cross-validation payload for T32 (openssl <-> pure python)"
    msg.write_bytes(payload)
    sig = tmp_path / "s.bin"
    subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-inkey", str(sec), "-rawin", "-in", str(msg),
         "-out", str(sig)],
        capture_output=True, check=True)
    # RFC 8410: SubjectPublicKeyInfo DER 尾部 32 字节即裸 ed25519 公钥
    der = subprocess.run(
        ["openssl", "pkey", "-pubin", "-in", str(pub_pem), "-outform", "DER"],
        capture_output=True, check=True).stdout
    pub = der[-32:]
    assert updater._ed25519_verify(sig.read_bytes(), payload, pub) is True
    assert updater._ed25519_verify(sig.read_bytes(), payload + b"x", pub) is False


# ═══ 第 3 层：与真 minisign 二进制交叉验证（skipif 无 minisign）═══

@pytest.mark.skipif(shutil.which("minisign") is None,
                    reason="minisign 未安装——交叉验证留待有 minisign 的环境")
def test_cross_validation_with_minisign_binary(tmp_path):
    """真 minisign keypair+签名 ↔ 纯 Python 验签 / verify_manifest.sh 三方一致。"""
    pub = tmp_path / "unit.pub"
    sec = tmp_path / "unit.sec"
    subprocess.run(["minisign", "-G", "-p", str(pub), "-s", str(sec)],
                   input="\n\n", capture_output=True, text=True, check=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
    sig = tmp_path / "manifest.sig"
    subprocess.run(["minisign", "-S", "-s", str(sec), "-m", str(manifest), "-x", str(sig)],
                   input="\n", capture_output=True, text=True, check=True)

    # ① 纯 Python 验签通过
    assert updater.verify_minisign_signature(
        pub.read_text(encoding="utf-8"), sig.read_text(encoding="utf-8"),
        MANIFEST_TEXT.encode("utf-8")) is True
    # ② shell verify_manifest.sh 同样通过（两实现同语义）
    proc = subprocess.run(
        ["bash", str(VERIFY_SCRIPT), str(pub), str(manifest), str(sig), "99.0.0"],
        capture_output=True, text=True)
    assert proc.returncode == 0, f"shell 侧应 exit 0: {proc.stderr}"
    # ③ 篡改一字节后两实现一致拒绝
    tampered = tmp_path / "manifest_tampered.json"
    tampered.write_text(MANIFEST_TEXT.replace('"99.0.0"', '"99.0.1"'), encoding="utf-8")
    assert updater.verify_minisign_signature(
        pub.read_text(encoding="utf-8"), sig.read_text(encoding="utf-8"),
        tampered.read_text(encoding="utf-8").encode("utf-8")) is False
    proc = subprocess.run(
        ["bash", str(VERIFY_SCRIPT), str(pub), str(tampered), str(sig)],
        capture_output=True, text=True)
    assert proc.returncode == 3


@pytest.mark.skipif(shutil.which("minisign") is None,
                    reason="minisign 未安装——交叉验证留待有 minisign 的环境")
def test_cross_validation_legacy_mode_minisign(tmp_path):
    """legacy（`-l` 纯 Ed25519，算法字 Ed）模式交叉验证（2026-09-23 补）。

    覆盖动机：`-S` 默认产出预哈希（ED）签名，Ed 纯模式路径此前只有 RFC 向量
    锁数学、无真机容器级覆盖——本用例防「只修预哈希、纯模式解析回退」。
    """
    pub = tmp_path / "unit_legacy.pub"
    sec = tmp_path / "unit_legacy.sec"
    subprocess.run(["minisign", "-G", "-p", str(pub), "-s", str(sec)],
                   input="\n\n", capture_output=True, text=True, check=True)
    manifest = tmp_path / "manifest_legacy.json"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
    sig = tmp_path / "manifest_legacy.sig"
    subprocess.run(["minisign", "-S", "-l", "-s", str(sec), "-m", str(manifest), "-x", str(sig)],
                   input="\n", capture_output=True, text=True, check=True)
    # 算法字必须是 Ed（legacy 模式自检）
    import base64 as _b64
    sig_b64 = [l for l in sig.read_text(encoding="utf-8").splitlines()
               if l and not l.startswith(("untrusted", "trusted"))][0].strip()
    assert _b64.b64decode(sig_b64)[:2] == b"Ed", "-l 应产出纯 Ed 模式签名"
    assert updater.verify_minisign_signature(
        pub.read_text(encoding="utf-8"), sig.read_text(encoding="utf-8"),
        MANIFEST_TEXT.encode("utf-8")) is True
    # 篡改拒绝
    assert updater.verify_minisign_signature(
        pub.read_text(encoding="utf-8"), sig.read_text(encoding="utf-8"),
        MANIFEST_TEXT.encode("utf-8") + b" ") is False
