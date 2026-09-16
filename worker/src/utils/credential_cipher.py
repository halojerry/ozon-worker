"""v1.0 T2: AES-256-GCM 列级凭证加密工具。

设计:
- 随机 12-byte nonce 前置 + AES-256-GCM 加密 (cryptography.AESGCM)
- 密钥来自环境变量 CREDENTIAL_MASTER_KEY:
  - 恰好 32 字节(裸 32B / 64 hex / 44 base64 解码后恰 32B) → 直接作为 AES-256
    key, 不进 KDF (v1/v2 同规)
  - 否则视为口令(仅当 ≥8 字符, 防弱口令):
    - v2 信封(新加密一律): PBKDF2-HMAC-SHA256 派生 32 字节
      (盐 = sha256(b"ozon-worker-credential-kdf-v2"), 600_000 轮;
       Task24 crypto-M2: 根治 v1 口令模式「单次无盐 SHA-256」可被离线
       字典爆破主密钥 → 解全部租户凭证的缺陷)
    - v1/legacy 信封: SHA-256 单次派生(仅解密兼容, 原逻辑不动)
- 信封版本前缀: 新密文 b"v2:"; 解密按前缀路由(v2: → v2 派生,
  v1:/无前缀 → v1 派生), 存量密文零迁移继续可解
- AAD = tenant_id:ozon_client_id → 密文绑定租户+店铺, 跨租户解密必然 GCM 认证失败
- decrypt 认证失败(错 key/篡改/错 AAD)一律 raise, 绝不返回静默垃圾
- mask 仅返回 ****{last4}, 不暴露明文

安全约定:
- 日志/异常信息绝不包含明文 key 或 value
- 每次加密生成随机 nonce (同一明文两次加密产物不同)
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_NONCE_LEN = 12
_MIN_PASSPHRASE_LEN = 8
_ENV_KEY = "CREDENTIAL_MASTER_KEY"
# PRD M3: 密文版本前缀(轮换兼容):v1 密文 b"v1:" + nonce+ct;更旧密文无前缀(解密兼容)
_VERSION_PREFIX = b"v1:"
# Task24 (crypto-M2): v2 信封前缀。新加密一律产出 v2:;解密按前缀路由派生方式。
_VERSION_PREFIX_V2 = b"v2:"
_KDF_V2_ITERATIONS = 600_000
# 固定盐: 域分离常量的 SHA-256 摘要(公开值;抗爆破强度来自迭代次数而非盐保密;
# 主密钥是每部署单值, 无独立存储可供放随机盐, 固定域分离盐是本设计的既定取舍)
_KDF_V2_SALT = hashlib.sha256(b"ozon-worker-credential-kdf-v2").digest()


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
    """(v1 legacy) 口令/32 字节 key → AES-256 key。

    Task24 起仅供 legacy 信封(无前缀/v1: 前缀)解密使用——原逻辑逐字不动
    (向后兼容红线);新加密走 derive_key_v2。轮换脚本等外部调用方请统一走
    encrypt_with_key/decrypt_with_key(内部按信封前缀自动路由 v1/v2 派生)。
    """
    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) == 32:
        return raw_bytes
    if len(raw) < _MIN_PASSPHRASE_LEN:
        raise CredentialCipherError(
            f"{_ENV_KEY} 过短(仅 {len(raw)} 字符): 需 32 字节 key 或 ≥{_MIN_PASSPHRASE_LEN} 字符口令"
        )
    return hashlib.sha256(raw_bytes).digest()  # 口令 → SHA-256 派生


def _raw_key_material_32b(raw: str) -> bytes | None:
    """32 字节 key 形态检测(v1/v2 同规): 命中 → 原始 key 直接用, 不进 KDF。

    三种互斥形态(按字符串长度天然区分, 派生确定可复现):
    - utf-8 编码恰 32 字节(v1 原规则, 保持)
    - 64 hex 字符 → 解码恰 32 字节(openssl rand -hex 32 形态)
    - 44 base64 字符(标准/urlsafe, 恰一个 '=' padding) → 解码恰 32 字节
      (openssl rand -base64 32 形态, DEPLOY.md 推荐形态)
    其余 → None(视为口令, 走 KDF)。
    ⚠️ 固有歧义: 恰为上述形态的「口令」会被当作裸 key——加解密两侧用同一
    检测故自洽;仅与 v1 时代的口令派生不同, 由信封前缀路由隔离。
    """
    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) == 32:
        return raw_bytes
    if len(raw) == 64:
        try:
            decoded = bytes.fromhex(raw)
        except ValueError:
            decoded = b""
        if len(decoded) == 32:
            return decoded
    if len(raw) == 44 and raw.endswith("="):
        decoded = b""
        try:
            decoded = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            try:
                decoded = base64.urlsafe_b64decode(raw)
            except (binascii.Error, ValueError):
                pass
        if len(decoded) == 32:
            return decoded
    return None


def derive_key_v2(raw: str) -> bytes:
    """v2 主密钥 → AES-256 key: 32 字节形态直接用;口令走 PBKDF2-HMAC-SHA256。

    Task24 (crypto-M2): 修复 v1 口令模式「单次无盐 SHA-256」可被离线字典
    爆破的缺陷——600_000 轮使每个口令猜测代价放大 ~60 万倍。
    """
    material = _raw_key_material_32b(raw)
    if material is not None:
        return material
    if len(raw) < _MIN_PASSPHRASE_LEN:
        raise CredentialCipherError(
            f"{_ENV_KEY} 过短(仅 {len(raw)} 字符): 需 32 字节 key 或 ≥{_MIN_PASSPHRASE_LEN} 字符口令"
        )
    return hashlib.pbkdf2_hmac(
        "sha256", raw.encode("utf-8"), _KDF_V2_SALT, _KDF_V2_ITERATIONS, dklen=32
    )


def encrypt(value: str, aad: str) -> bytes:
    """加密 value, 返回 b"v2:" + nonce(12B) + 密文+tag(Task24 起新密文一律 v2 信封)。"""
    return encrypt_with_key(value, aad, os.environ.get(_ENV_KEY, ""))


def encrypt_with_key(value: str, aad: str, key_raw: str) -> bytes:
    """显式 key 加密(轮换脚本用)。新密文一律 b"v2:" 前缀(v2 KDF 信封)。"""
    key = derive_key_v2(key_raw)
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
    return _VERSION_PREFIX_V2 + nonce + ct


def decrypt(ciphertext: bytes, aad: str) -> str:
    """解密(按前缀路由: v2: → v2 派生; v1:/旧无前缀 → v1 原派生); GCM 认证失败 → 抛异常。"""
    return decrypt_with_key(ciphertext, aad, os.environ.get(_ENV_KEY, ""))


def decrypt_with_key(ciphertext: bytes, aad: str, key_raw: str) -> str:
    """显式 key 解密(轮换脚本用)。按信封前缀路由 v1/v2 密钥派生, 原逻辑不动。"""
    raw = ciphertext
    if raw.startswith(_VERSION_PREFIX_V2):
        key = derive_key_v2(key_raw)
        raw = raw[len(_VERSION_PREFIX_V2):]
    else:
        key = derive_key(key_raw)  # legacy: v1 原派生(向后兼容解密红线)
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
