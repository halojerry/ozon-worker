# -*- coding: utf-8 -*-
"""
Task24 (crypto-M2) — 主密钥 KDF 升级 v2: PBKDF2-HMAC-SHA256(600k), 向后兼容解密 v1。

背景: v1 口令模式 = 单次无盐 SHA-256(passphrase)——同库泄漏可离线字典爆破主密钥,
解全部租户凭证。v2 信封 = PBKDF2-HMAC-SHA256(固定域分离盐, 600_000 轮, dklen=32);
32 字节随机 key(base64/hex/裸 32B 形态)直接用作 AES key 不进 KDF(v1/v2 同规);
解密按信封前缀路由(v2: → v2 派生; v1:/无前缀 → v1 原逻辑, 向后兼容红线);
新加密一律产出 v2: 信封。

覆盖：
(a) derive_key_v2(口令) != sha256(口令) → KDF 真换了(非无盐单次 SHA-256)
(b) v2 加密 → 解密 roundtrip(口令形态 + hex/base64 32B 形态)
(c) v1 旧密文仍可解(前缀路由向后兼容: v1: 前缀/无前缀, 含 DEPLOY.md 推荐的
    openssl rand -base64 32 形态 key 在 v1 时代按口令 sha256 派生的存量密文)
(d) 跨租户 aad 语义不变(错 aad → GCM 认证失败, v2 信封同规)
(e) 32 字节随机 key 路径不进 KDF(hex/base64 → 解码字节直接作 AES key;
    同明文两次加密 nonce 不同仍成立)
(f) 新加密一律产出 v2: 前缀(两种 key 形态)
(g) 篡改 v2 密文 → 抛异常(GCM 认证仍在)
(h) 口令过短仍拒绝(≥8 字符纪律保持)

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_kdf_v2_v076.py -q
"""
import base64
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from utils.credential_cipher import (  # noqa: E402
    CredentialCipherError,
    decrypt,
    decrypt_with_key,
    derive_key,
    derive_key_v2,
    encrypt,
    encrypt_with_key,
)

AAD = "tenant_1:4718259"
VALUE = "sk-TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT"
PASSPHRASE = "password123"  # 探针实证 v1 下 == sha256 本身的弱口令


def _make_v1_envelope(value: str, aad: str, key_raw: str, prefix: bytes = b"") -> bytes:
    """按 v1 原逻辑手工构造旧信封(32B 直用 / 口令 sha256 派生), 用于向后兼容验证。"""
    raw_bytes = key_raw.encode("utf-8")
    key = raw_bytes if len(raw_bytes) == 32 else hashlib.sha256(raw_bytes).digest()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
    return prefix + nonce + ct


# ── (a) KDF 真换了 ──
def test_kdf_v2_not_plain_sha256():
    weak_digest = hashlib.sha256(PASSPHRASE.encode()).digest()
    assert derive_key_v2(PASSPHRASE) != weak_digest, (
        "v2 口令派生绝不能等于单次无盐 SHA-256(可离线字典爆破)"
    )
    assert derive_key_v2(PASSPHRASE) != derive_key(PASSPHRASE), "v2 派生须与 v1 不同"


def test_kdf_v2_deterministic():
    # 同口令派生确定(解密侧可复现); ≠ v1 派生
    assert derive_key_v2(PASSPHRASE) == derive_key_v2(PASSPHRASE)


# ── (b) v2 roundtrip ──
def test_v2_roundtrip_passphrase(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "correct-horse-battery-staple")
    ct = encrypt(VALUE, AAD)
    assert decrypt(ct, AAD) == VALUE


def test_v2_roundtrip_explicit_key_forms():
    aad = "tenant_9:4718259"
    hex_key = os.urandom(32).hex()
    b64_key = base64.b64encode(os.urandom(32)).decode()
    for key in ("another-passphrase-99", hex_key, b64_key):
        blob = encrypt_with_key(VALUE, aad, key)
        assert decrypt_with_key(blob, aad, key) == VALUE, f"key 形态 roundtrip 失败: {key[:4]}…"


# ── (c) v1 旧密文仍可解(向后兼容红线) ──
def test_v1_prefixed_envelope_still_decryptable():
    ct = _make_v1_envelope(VALUE, AAD, PASSPHRASE, prefix=b"v1:")
    assert decrypt_with_key(ct, AAD, PASSPHRASE) == VALUE


def test_legacy_unprefixed_envelope_still_decryptable():
    ct = _make_v1_envelope(VALUE, AAD, PASSPHRASE)  # 无前缀旧格式
    assert decrypt_with_key(ct, AAD, PASSPHRASE) == VALUE


def test_v1_envelope_with_32b_key_still_decryptable():
    key32 = "a" * 32  # v1: utf-8 恰 32 字节 → 直用; v2 下同规
    ct = _make_v1_envelope(VALUE, AAD, key32, prefix=b"v1:")
    assert decrypt_with_key(ct, AAD, key32) == VALUE


def test_v1_envelope_base64_key_sha256_era_still_decryptable():
    # DEPLOY.md 曾推荐 openssl rand -base64 32: 该 44 字符串在 v1 时代按「口令」
    # sha256 派生(非解码直用)。存量密文必须仍可解(前缀路由 → v1 原派生)。
    b64_key = base64.b64encode(os.urandom(32)).decode()
    assert len(b64_key) == 44 and b64_key.endswith("=")
    ct = _make_v1_envelope(VALUE, AAD, b64_key, prefix=b"v1:")
    assert decrypt_with_key(ct, AAD, b64_key) == VALUE


def test_mixed_v1_v2_envelopes_coexist():
    # 轮换前常态: 库里同时有 v1 存量与 v2 新密文, 同 key 均可解
    old_ct = _make_v1_envelope(VALUE, AAD, PASSPHRASE, prefix=b"v1:")
    new_ct = encrypt_with_key(VALUE, AAD, PASSPHRASE)
    assert decrypt_with_key(old_ct, AAD, PASSPHRASE) == VALUE
    assert decrypt_with_key(new_ct, AAD, PASSPHRASE) == VALUE


# ── (d) 跨租户 aad 语义不变 ──
def test_v2_wrong_aad_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "correct-horse-battery-staple")
    ct = encrypt(VALUE, AAD)
    with pytest.raises(CredentialCipherError):
        decrypt(ct, "tenant_2:4718259")  # 跨租户 → GCM 认证失败


def test_v2_wrong_aad_raises_32b_key():
    b64_key = base64.b64encode(os.urandom(32)).decode()
    blob = encrypt_with_key(VALUE, AAD, b64_key)
    with pytest.raises(CredentialCipherError):
        decrypt_with_key(blob, "tenant_2:4718259", b64_key)


def test_v2_wrong_key_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "correct-horse-battery-staple")
    ct = encrypt(VALUE, AAD)
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "another-passphrase-99")
    with pytest.raises(CredentialCipherError):
        decrypt(ct, AAD)


# ── (e) 32 字节 key 不进 KDF ──
def test_hex_key_decodes_to_raw_bytes_not_kdf():
    raw32 = os.urandom(32)
    hex_key = raw32.hex()
    assert derive_key_v2(hex_key) == raw32, "hex 32B key 必须解码直用, 不进 KDF"
    # v1 派生(sha256 口令)下同一 key 得到不同 key——证明 v2 行为确已分流
    assert derive_key(hex_key) != raw32


def test_base64_key_decodes_to_raw_bytes_not_kdf():
    raw32 = os.urandom(32)
    b64_key = base64.b64encode(raw32).decode()
    assert derive_key_v2(b64_key) == raw32, "base64 32B key 必须解码直用, 不进 KDF"


def test_raw_32b_key_direct_use():
    assert derive_key_v2("k" * 32) == b"k" * 32  # v1 规则保持: utf-8 恰 32B 直用


def test_32b_key_ciphertext_decryptable_with_raw_bytes():
    # 端到端证明: hex key 加密产物可直接用解码后的 32 字节作 AES key 解密(未过 KDF)
    raw32 = os.urandom(32)
    blob = encrypt_with_key(VALUE, AAD, raw32.hex())
    nonce, body = blob[len(b"v2:"):][:12], blob[len(b"v2:") + 12:]
    plain = AESGCM(raw32).decrypt(nonce, body, AAD.encode("utf-8"))
    assert plain.decode("utf-8") == VALUE


def test_random_nonce_distinct_with_32b_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", os.urandom(32).hex())
    ct1 = encrypt(VALUE, AAD)
    ct2 = encrypt(VALUE, AAD)
    assert ct1 != ct2


# ── (f) 新加密一律 v2: 前缀 ──
def test_new_envelopes_always_v2_prefix(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", PASSPHRASE)
    assert encrypt(VALUE, AAD).startswith(b"v2:")
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", os.urandom(32).hex())
    assert encrypt(VALUE, AAD).startswith(b"v2:")
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
    assert encrypt(VALUE, AAD).startswith(b"v2:")


# ── (g) 篡改 v2 密文 → 抛异常 ──
def test_v2_tampered_ciphertext_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", PASSPHRASE)
    ct = bytearray(encrypt(VALUE, AAD))
    ct[-1] ^= 0x01
    with pytest.raises(CredentialCipherError):
        decrypt(bytes(ct), AAD)


# ── (h) 口令过短仍拒绝 ──
def test_v2_short_passphrase_raises():
    with pytest.raises(CredentialCipherError, match="过短"):
        derive_key_v2("short")
