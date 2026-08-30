"""v1.0 T2: AES-256-GCM 列级凭证加密工具。

设计:
- 随机 12-byte nonce 前置 + AES-256-GCM 加密 (cryptography.AESGCM)
- 密钥来自环境变量 CREDENTIAL_MASTER_KEY:
  - 恰好 32 字节 → 直接作为 AES-256 key
  - 否则视为口令 → SHA-256 派生 32 字节 (仅当 ≥8 字符, 防弱口令)
- AAD = tenant_id:ozon_client_id → 密文绑定租户+店铺, 跨租户解密必然 GCM 认证失败
- decrypt 认证失败(错 key/篡改/错 AAD)一律 raise, 绝不返回静默垃圾
- mask 仅返回 ****{last4}, 不暴露明文

安全约定:
- 日志/异常信息绝不包含明文 key 或 value
- 每次加密生成随机 nonce (同一明文两次加密产物不同)
"""
from __future__ import annotations

import hashlib
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_NONCE_LEN = 12
_MIN_PASSPHRASE_LEN = 8
_ENV_KEY = "CREDENTIAL_MASTER_KEY"
# PRD M3: 密文版本前缀(轮换兼容):新密文 b"v1:" + nonce+ct;旧密文无前缀(解密兼容)
_VERSION_PREFIX = b"v1:"


class CredentialCipherError(ValueError):
    """凭证加密错误(配置错误/GCM 认证失败共用, 不携带任何密文)。"""


def _load_key() -> bytes:
    raw = os.environ.get(_ENV_KEY, "")
    if not raw:
        raise CredentialCipherError(
            f"{_ENV_KEY} 环境变量未设置: 无法加密/解密凭证"
        )
    return derive_key(raw)


def derive_key(raw: str) -> bytes:
    """口令/32 字节 key → AES-256 key(供轮换脚本显式传 key)。"""
    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) == 32:
        return raw_bytes
    if len(raw) < _MIN_PASSPHRASE_LEN:
        raise CredentialCipherError(
            f"{_ENV_KEY} 过短(仅 {len(raw)} 字符): 需 32 字节 key 或 ≥{_MIN_PASSPHRASE_LEN} 字符口令"
        )
    return hashlib.sha256(raw_bytes).digest()  # 口令 → SHA-256 派生


def encrypt(value: str, aad: str) -> bytes:
    """加密 value, 返回 b"v1:" + nonce(12B) + 密文+tag(PRD M3 版本前缀)。"""
    return encrypt_with_key(value, aad, os.environ.get(_ENV_KEY, ""))


def encrypt_with_key(value: str, aad: str, key_raw: str) -> bytes:
    """显式 key 加密(轮换脚本用)。"""
    key = derive_key(key_raw)
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
    return _VERSION_PREFIX + nonce + ct


def decrypt(ciphertext: bytes, aad: str) -> str:
    """解密(兼容 v1: 前缀与旧无前缀); GCM 认证失败 → 抛异常, 绝不返回垃圾。"""
    return decrypt_with_key(ciphertext, aad, os.environ.get(_ENV_KEY, ""))


def decrypt_with_key(ciphertext: bytes, aad: str, key_raw: str) -> str:
    """显式 key 解密(轮换脚本用;兼容 v1: 前缀与旧格式)。"""
    key = derive_key(key_raw)
    raw = ciphertext
    if raw.startswith(_VERSION_PREFIX):
        raw = raw[len(_VERSION_PREFIX):]
    if not isinstance(raw, bytes) or len(raw) <= _NONCE_LEN:
        raise CredentialCipherError("密文格式非法: 缺少 nonce 或长度不足")
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, ct, aad.encode("utf-8"))
    except InvalidTag as exc:
        logger.error("凭证解密失败: GCM 认证失败(错 key/密文被篡改/AAD 不匹配)")
        raise CredentialCipherError("凭证解密失败: GCM 认证失败") from exc
    return plaintext.decode("utf-8")


def mask(value: str) -> str:
    """掩码: ****{last4}; 长度 ≤4 返回全部掩码。"""
    if not value or len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"
