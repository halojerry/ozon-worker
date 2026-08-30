#!/usr/bin/env python3
"""PRD M3: 主密钥轮换 — 用旧 key 解密全部凭证密文,再用新 key 重加密(v1: 前缀)。

用法:
    OLD_CREDENTIAL_MASTER_KEY=<旧> NEW_CREDENTIAL_MASTER_KEY=<新> \\
        python scripts/rotate_master_key.py --db-url $PGDATABASE_URL --dry-run
    确认后去掉 --dry-run(加 --apply)。

安全:解密失败(错旧 key/篡改)即中断,绝不部分轮换(事务内)。轮换后旧 key 不可再解密。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "src"))

from sqlalchemy import create_engine, text  # noqa: E402

from utils.credential_cipher import CredentialCipherError, decrypt_with_key, encrypt_with_key  # noqa: E402

logger = logging.getLogger(__name__)


def rotate(engine, old_key: str, new_key: str, apply: bool) -> int:
    rows = []
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id::text, tenant_id, ozon_client_id, ozon_api_key_enc FROM credentials"
        )).fetchall()
    if not rows:
        print("无凭证行,无需轮换")
        return 0
    prepared = []
    for rid, tenant, client, enc in rows:
        aad = f"{tenant}:{client}"
        try:
            plain = decrypt_with_key(bytes(enc), aad, old_key)
            new_ct = encrypt_with_key(plain, aad, new_key)
        except CredentialCipherError as exc:
            raise SystemExit(f"❌ 解密失败(检查旧 key) id={rid}: {exc}")
        prepared.append((new_ct, rid))
    if not apply:
        print(f"dry-run:将重加密 {len(prepared)} 条凭证;确认后加 --apply")
        return len(prepared)
    with engine.begin() as conn:
        for new_ct, rid in prepared:
            conn.execute(text(
                "UPDATE credentials SET ozon_api_key_enc=:ct, updated_at=NOW() WHERE id::text=:id"
            ), {"ct": new_ct, "id": rid})
    print(f"✅ 已重加密 {len(prepared)} 条凭证(新格式 v1: 前缀)")
    return len(prepared)


def main() -> int:
    ap = argparse.ArgumentParser(description="主密钥轮换(重加密全部凭证)")
    ap.add_argument("--old-key", default=os.getenv("OLD_CREDENTIAL_MASTER_KEY", ""))
    ap.add_argument("--new-key", default=os.getenv("NEW_CREDENTIAL_MASTER_KEY", ""))
    ap.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""))
    ap.add_argument("--apply", action="store_true", help="默认 dry-run;加此参数才写库")
    args = ap.parse_args()
    if not (args.old_key and args.new_key and args.db_url):
        logger.error("必须提供 --old-key / --new-key / --db-url")
        return 1
    rotate(create_engine(args.db_url), args.old_key, args.new_key, args.apply)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
