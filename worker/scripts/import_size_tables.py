"""尺码表导入：assets/*.csv → PG size_mappings（部署时由 init_data.py 调用）。

幂等：已有数据跳过；--force 清空重导。解析规则：
  - 每个 CSV 行展开为多行 (table_type, input_value, ru_size)；
  - input_value 为各列值标准化（去空白/大写），俄罗斯尺码列优先取表头含 "(ru)" 的列；
  - 俄罗斯尺码自身也作为输入值入库（输入 "48" → RU "48"）。
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

ASSETS_DIR = os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "assets")
TABLE_FILES = {
    "children": "儿童服装尺码表.csv",
    "male": "男性服装尺码表.csv",
    "female": "女性服装尺码表.csv",
    "shoes": "鞋子尺码对应表.csv",
}


def normalize(v) -> str:
    return re.sub(r"\s+", "", str(v or "")).strip().upper()


def _pick_ru_col(headers: list[str]) -> str | None:
    ru_like = [h for h in headers if "俄罗斯" in h or "RU" in normalize(h)]
    if not ru_like:
        return None
    for h in ru_like:  # 优先 "(ru)" 列（真正的俄罗斯尺码，而非身高列）
        if "(ru)" in normalize(h):
            return h
    return ru_like[0]


def _norm_source_col(col: str) -> str:
    """列头 → 语义键：INT/US/UK/CN/RU；测量类列保留原始表头。"""
    n = normalize(col)
    if "国际" in col or n == "INT":
        return "INT"
    if "美国" in col or n == "US":
        return "US"
    if "英国" in col or n == "UK":
        return "UK"
    if "1688" in col or n == "CN":
        return "CN"
    if "RU" in n or "俄罗斯" in col:
        return "RU"
    return col.strip()


def parse_csv_rows(file_path: str, table_type: str) -> list[dict]:
    rows: list[dict] = []
    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return rows
        headers = [h.strip() for h in reader.fieldnames]
        ru_col = _pick_ru_col(headers)
        if not ru_col:
            return rows
        for row in reader:
            clean = {k.strip(): v.strip() for k, v in row.items() if k and v}
            ru_size = normalize(clean.get(ru_col, ""))
            if not ru_size:
                continue
            seen: set[str] = set()
            for col, val in clean.items():
                if "俄罗斯" in col:
                    continue  # 俄罗斯尺码列不作为输入源（由 RU 自插入覆盖）
                norm = normalize(val)
                if norm and norm not in seen and col != ru_col:
                    seen.add(norm)
                    rows.append({"table_type": table_type, "input_value": norm,
                                 "ru_size": ru_size, "source_col": _norm_source_col(col)})
            rows.append({"table_type": table_type, "input_value": ru_size,
                         "ru_size": ru_size, "source_col": "RU"})
    return rows


def import_size_tables(engine, force: bool = False) -> int:
    from sqlalchemy import text

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM size_mappings")).scalar()
    if count and count > 0 and not force:
        logger.info(f"⏭️  size_mappings 已有 {count} 条，跳过导入（--force 强制）")
        return int(count)
    if force and count and count > 0:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM size_mappings"))
            conn.commit()
        logger.info(f"🗑️  已清空旧尺码表数据 ({count} 条)")
    all_rows: list[dict] = []
    for table_type, fname in TABLE_FILES.items():
        path = os.path.join(ASSETS_DIR, fname)
        if not os.path.exists(path):
            logger.warning(f"⚠️  尺码表文件不存在: {path}")
            continue
        all_rows.extend(parse_csv_rows(path, table_type))
    # 全局去重（(table_type, input_value) 唯一键），保留首个
    seen_keys: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for r in all_rows:
        k = (r["table_type"], r["input_value"])
        if k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(r)
    all_rows = deduped
    with engine.begin() as conn:
        for r in all_rows:
            conn.execute(
                text("INSERT INTO size_mappings (table_type, input_value, ru_size, source_col) "
                     "VALUES (:t, :i, :r, :s)"),
                {"t": r["table_type"], "i": r["input_value"], "r": r["ru_size"], "s": r["source_col"]},
            )
    logger.info(f"✅ 尺码表导入完成: {len(all_rows)} 条")
    return len(all_rows)


def main():
    parser = argparse.ArgumentParser(description="导入尺码表到 PG")
    parser.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        logger.error("请设置 PGDATABASE_URL")
        sys.exit(1)
    from sqlalchemy import create_engine
    import_size_tables(create_engine(args.db_url), force=args.force)


if __name__ == "__main__":
    main()
