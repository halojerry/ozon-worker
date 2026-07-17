"""
物流费率表导入脚本
从 China_scoring Excel 文件导入 142 条 Ozon rFBS 物流费率到 PG logistics_rates 表。

用法:
    python scripts/import_logistics.py [--excel PATH]

默认读取 assets/ 下的 Excel 文件。
"""

import os
import re
import sys
import logging

sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "src"))

import openpyxl
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from storage.database.shared.model import LogisticsRate, Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 默认 Excel 路径
DEFAULT_EXCEL = os.path.join(
    os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))),
    "docs",
    "China_scoring_ENG_CN_21_04_26_1776754052 (1).xlsx",
)

SHEET_NAME = "中国 rFBS"
HEADER_ROW = 5  # 0-indexed (Excel row 5 = header)
DATA_START_ROW = 6  # 0-indexed (Excel row 6 = first data)


def parse_rate_string(rate_str: str) -> tuple[float, float]:
    """解析费率字符串，返回 (base_cost, per_gram_rate)

    支持格式:
        "¥3.12 + ¥0.0468/1 g"
        "￥3.12 + ￥0.0364/1 g"
        "¥3 + ¥0,045/1g"
        "¥3.12 + ¥.0.0468/1 g"  (typo in Excel)
    """
    if not rate_str or not isinstance(rate_str, str):
        return (0.0, 0.0)

    # 统一符号
    s = rate_str.replace("￥", "¥").replace(",", ".").replace(" ", "")
    # 修复 "¥.0." typo → "¥0."
    s = re.sub(r"¥\.0\.", "¥0.", s)

    # 匹配: ¥base + ¥per_g/1g
    m = re.search(r"¥([\d.]+)\+¥([\d.]+)/?1g", s)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    # 只有 base（无 per-gram）
    m = re.search(r"¥([\d.]+)", s)
    if m:
        return (float(m.group(1)), 0.0)

    return (0.0, 0.0)


def parse_sum_limit(limit_str: str) -> int:
    """解析尺寸限制字符串，提取最大边长总和（cm）

    如 "边长总和 ≤ 90 cm, 长边 ≤ 60 cm" → 90
    """
    if not limit_str or not isinstance(limit_str, str):
        return 0
    m = re.search(r"≤\s*(\d+)\s*cm", limit_str)
    return int(m.group(1)) if m else 0


def parse_longest_limit(limit_str: str) -> int:
    """解析尺寸限制，提取最长边限制（cm）

    如 "边长总和 ≤ 90 cm, 长边 ≤ 60 cm" → 60
    """
    if not limit_str or not isinstance(limit_str, str):
        return 0
    # 匹配 "长边 ≤ 60 cm" 或第二个 ≤ 值
    parts = re.findall(r"≤\s*(\d+)\s*cm", limit_str)
    return int(parts[1]) if len(parts) > 1 else (int(parts[0]) if parts else 0)


def parse_vol_weight_divisor(charge_type: str, vol_str: str) -> int:
    """解析体积重量除数

    如 charge_type="体积重量", vol_str="/ 6000" → 6000
    """
    if not vol_str or not isinstance(vol_str, str):
        return 0
    m = re.search(r"(\d+)", vol_str)
    return int(m.group(1)) if m else 0


def read_excel(filepath: str) -> list[dict]:
    """读取 Excel 并解析为 LogisticsRate 记录列表"""
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb[SHEET_NAME]

    records = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i < DATA_START_ROW:
            continue

        scoring_group = str(row[0] or "").strip()
        service_level = str(row[1] or "").strip()
        tpl_provider = str(row[2] or "").strip()
        delivery_method = str(row[3] or "").strip()

        # 跳过分隔行和空行
        if not scoring_group or scoring_group in ("переход", "None", "评分组"):
            continue
        if not tpl_provider or not service_level:
            continue

        weight_min = int(row[10]) if row[10] else 0
        weight_max = int(row[11]) if row[11] else 0
        base_cost, per_gram_rate = parse_rate_string(str(row[6] or ""))
        sum_limit = parse_sum_limit(str(row[9] or ""))
        longest_limit = parse_longest_limit(str(row[9] or ""))
        charge_type = "actual" if "实际" in str(row[16] or "") else "volumetric"
        vol_divisor = parse_vol_weight_divisor(str(row[16] or ""), str(row[17] or ""))

        records.append({
            "scoring_group": scoring_group,
            "service_level": service_level,
            "tpl_provider": tpl_provider,
            "delivery_method": delivery_method,
            "base_cost": base_cost,
            "per_gram_rate": per_gram_rate,
            "weight_min": weight_min,
            "weight_max": weight_max,
            "sum_limit_cm": sum_limit,
            "longest_limit_cm": longest_limit,
            "charge_type": charge_type,
            "vol_weight_divisor": vol_divisor,
        })

    wb.close()
    return records


def upsert_records(engine, records: list[dict]):
    """Upsert 到 logistics_rates 表"""
    with Session(engine) as session:
        # 清空旧数据
        session.execute(text("DELETE FROM logistics_rates"))
        session.commit()
        logger.info("已清空 logistics_rates 旧数据")

        # 批量插入
        for rec in records:
            session.add(LogisticsRate(**rec))
        session.commit()
        logger.info(f"✅ 已导入 {len(records)} 条物流费率")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="导入物流费率到 PG")
    parser.add_argument("--excel", default=DEFAULT_EXCEL, help="Excel 文件路径")
    parser.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""), help="PG 连接串")
    args = parser.parse_args()

    if not args.db_url:
        logger.error("请设置 PGDATABASE_URL 环境变量或通过 --db-url 传入")
        sys.exit(1)

    if not os.path.exists(args.excel):
        logger.error(f"Excel 文件不存在: {args.excel}")
        sys.exit(1)

    logger.info(f"读取 Excel: {args.excel}")
    records = read_excel(args.excel)
    logger.info(f"解析到 {len(records)} 条费率记录")

    if not records:
        logger.error("未解析到任何记录，请检查 Excel 格式")
        sys.exit(1)

    engine = create_engine(args.db_url)
    Base.metadata.create_all(engine, tables=[LogisticsRate.__table__])

    upsert_records(engine, records)
    logger.info("🎉 物流费率导入完成")


if __name__ == "__main__":
    main()
