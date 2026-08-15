# -*- coding: utf-8 -*-
"""
v1.0 T2 — credential_cipher AES-256-GCM 列级凭证加密工具。

覆盖：
(a) round-trip: encrypt → decrypt 还原原文（同 key 同 aad）
(b) 随机 nonce: 同一明文两次加密产物不同
(c) 错 key → 抛异常（不返回静默垃圾）
(d) 篡改密文 → 抛异常（GCM 认证失败）
(e) 错 AAD → 抛异常（AAD 绑定 tenant:client，跨租户解密失败）
(f) 密文非法（非 bytes / 短于 nonce）→ 抛异常
(g) mask 格式: "sk-abc123XYZ9" → "****XYZ9"
(h) mask 短值(≤4) → "****"
(i) key 缺失/过短 → 明确错误
(j) caplog: 失败日志不含明文 value / key（无明文泄漏）

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_credential_cipher.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from utils.credential_cipher import (  # noqa: E402
    CredentialCipherError,
    decrypt,
    encrypt,
    mask,
)

AAD = "tenant_1:4718259"
VALUE = "sk-cd1d0a10-181a-42a1-8895-8508bb0513d7"
KEY = "a" * 32  # 恰好 32 字节 → 直接作为 AES key


# ── (a) round-trip ──
def test_round_trip(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", KEY)
    ct = encrypt(VALUE, AAD)
    assert decrypt(ct, AAD) == VALUE


# ── (b) 随机 nonce ──
def test_random_nonce_produces_distinct_ciphertext(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", KEY)
    ct1 = encrypt(VALUE, AAD)
    ct2 = encrypt(VALUE, AAD)
    assert ct1 != ct2


# ── (c) 错 key → 抛异常 ──
def test_wrong_key_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", KEY)
    ct = encrypt(VALUE, AAD)
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "b" * 32)
    with pytest.raises(ValueError):
        decrypt(ct, AAD)


# ── (d) 篡改密文 → 抛异常 ──
def test_tampered_ciphertext_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", KEY)
    ct = bytearray(encrypt(VALUE, AAD))
    ct[-1] ^= 0x01  # 翻转最后一个字节
    with pytest.raises(ValueError):
        decrypt(bytes(ct), AAD)


# ── (e) 错 AAD → 抛异常 ──
def test_wrong_aad_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", KEY)
    ct = encrypt(VALUE, AAD)
    with pytest.raises(ValueError):
        decrypt(ct, "tenant_2:4718259")  # 跨租户 → 认证失败


# ── (f) 密文非法 ──
def test_invalid_ciphertext_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", KEY)
    with pytest.raises(ValueError):
        decrypt(b"", AAD)
    with pytest.raises(ValueError):
        decrypt(b"short", AAD)


# ── (g) mask 格式 ──
def test_mask_keeps_last4():
    assert mask("sk-abc123XYZ9") == "****XYZ9"


# ── (h) mask 短值 ──
def test_mask_short_value():
    assert mask("abcd") == "****"
    assert mask("xyz") == "****"
    assert mask("") == "****"


# ── (i) key 缺失/过短 ──
def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("CREDENTIAL_MASTER_KEY", raising=False)
    with pytest.raises(ValueError, match="CREDENTIAL_MASTER_KEY"):
        encrypt(VALUE, AAD)


def test_short_key_raises(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "short")
    with pytest.raises(ValueError, match="过短"):
        encrypt(VALUE, AAD)


# ── (j) 日志无明文泄漏 ──
def test_logs_do_not_leak_plaintext_or_key(monkeypatch, caplog):
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", KEY)
    ct = encrypt(VALUE, AAD)
    # 换错 key 触发解密失败 → 模块记 error 日志
    monkeypatch.setenv("CREDENTIAL_MASTER_KEY", "c" * 32)
    with caplog.at_level("ERROR"), pytest.raises(ValueError):
        decrypt(ct, AAD)
    assert caplog.records, "解密失败应产生 error 日志"
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert VALUE not in joined  # 明文 value 不进日志
    assert "c" * 32 not in joined  # key 不进日志
    assert KEY not in joined
