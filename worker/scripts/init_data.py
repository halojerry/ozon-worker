#!/usr/bin/env python3
"""
首次部署数据初始化脚本。
- 建表 (create_all)
- 导入类目树 → category_tree_nodes
- 导入物流费率 → logistics_rates

幂等设计：重复运行不会报错，已存在的数据会跳过或覆盖。

用法:
    python scripts/init_data.py [--db-url $PGDATABASE_URL]
"""

import json
import os
import re
import sys
import logging

sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ASSETS_DIR = os.path.join(os.getenv("APP_WORKSPACE_PATH", os.path.dirname(os.path.dirname(__file__))), "assets")


def create_tables(engine):
    """创建所有表（幂等）。"""
    from storage.database.shared.model import Base
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(text("SET pg_trgm.similarity_threshold = 0.05"))
        conn.commit()

    Base.metadata.create_all(bind=engine)
    logger.info("✅ 表结构已就绪")


def import_category_tree(engine, language="ZH_HANS", force=False, tree_file="category_tree.json"):
    """导入类目树到 category_tree_nodes。

    Args:
        engine: SQLAlchemy engine
        language: 语言代码 (ZH_HANS/RU)
        force: True 时清空旧数据重新导入
        tree_file: 类目树文件名（相对于 ASSETS_DIR）
    """
    from sqlalchemy import text as sql_text

    # 检查是否已有数据
    with engine.connect() as conn:
        count = conn.execute(
            sql_text("SELECT COUNT(*) FROM category_tree_nodes WHERE language = :lang"),
            {"lang": language}
        ).scalar()

    if count and count > 0 and not force:
        logger.info(f"⏭️  类目树已有 {count} 条记录，跳过导入（用 --force 强制覆盖）")
        return

    if force and count and count > 0:
        with engine.connect() as conn:
            conn.execute(sql_text("DELETE FROM category_tree_nodes WHERE language = :lang"), {"lang": language})
            conn.commit()
        logger.info(f"🗑️  已清空旧类目树数据 ({count} 条)")

    # 读取 JSON
    tree_path = os.path.join(ASSETS_DIR, tree_file)
    if not os.path.exists(tree_path):
        logger.warning(f"⚠️  类目树文件不存在: {tree_path}")
        return

    with open(tree_path, "r", encoding="utf-8") as f:
        tree_data = json.load(f)

    # 扁平化树结构
    nodes = []

    def walk(items, parent_path="", depth=0, current_desc_cat_id=0):
        for item in items:
            # 始终更新当前层级的 description_category_id（子节点继承父节点）
            desc_cat_id = item.get("description_category_id", current_desc_cat_id) or current_desc_cat_id
            
            if "type_id" in item and item.get("type_id"):
                # 叶子节点（type）
                type_id = item["type_id"]
                type_name = item.get("type_name", "")
                full_path = f"{parent_path} > {type_name}" if parent_path else type_name
                top_level = parent_path.split(" > ")[0] if parent_path else type_name
                nodes.append({
                    "description_category_id": desc_cat_id,
                    "type_id": type_id,
                    "node_name": type_name,
                    "node_type": "type",
                    "full_path": full_path,
                    "depth": depth,
                    "language": language,
                    "top_level_category_name": top_level,
                })
            elif "description_category_id" in item or "children" in item:
                # 中间节点（category）或有无children的节点
                cat_name = item.get("category_name", "")
                full_path = f"{parent_path} > {cat_name}" if parent_path else cat_name
                top_level = parent_path.split(" > ")[0] if parent_path else cat_name
                nodes.append({
                    "description_category_id": desc_cat_id,
                    "type_id": None,  # category 节点无 type_id（与 sync_category_tree_nodes 一致）
                    "node_name": cat_name,
                    "node_type": "category",
                    "full_path": full_path,
                    "depth": depth,
                    "language": language,
                    "top_level_category_name": top_level,
                })
                children = item.get("children", [])
                if children:
                    walk(children, full_path, depth + 1, desc_cat_id)

    walk(tree_data if isinstance(tree_data, list) else tree_data.get("result", []))
    logger.info(f"解析到 {len(nodes)} 个类目节点")

    # 批量插入
    with engine.connect() as conn:
        for node in nodes:
            conn.execute(sql_text("""
                INSERT INTO category_tree_nodes
                    (description_category_id, type_id, node_name, node_type, full_path, depth, language, top_level_category_name, disabled)
                VALUES
                    (:description_category_id, :type_id, :node_name, :node_type, :full_path, :depth, :language, :top_level_category_name, false)
                ON CONFLICT (description_category_id, type_id, language)
                DO UPDATE SET node_name = EXCLUDED.node_name, full_path = EXCLUDED.full_path,
                              depth = EXCLUDED.depth, top_level_category_name = EXCLUDED.top_level_category_name, disabled = false
            """), node)
        conn.commit()

    logger.info(f"✅ 类目树导入完成: {len(nodes)} 条")


def import_logistics_rates(engine, force=False):
    """导入物流费率到 logistics_rates。

    Args:
        engine: SQLAlchemy engine
        force: True 时清空旧数据重新导入
    """
    from sqlalchemy import text as sql_text

    # 检查是否已有数据
    with engine.connect() as conn:
        count = conn.execute(sql_text("SELECT COUNT(*) FROM logistics_rates")).scalar()

    if count and count > 0 and not force:
        logger.info(f"⏭️  物流费率已有 {count} 条记录，跳过导入（用 --force 强制覆盖）")
        return

    if force and count and count > 0:
        with engine.connect() as conn:
            conn.execute(sql_text("DELETE FROM logistics_rates"))
            conn.commit()
        logger.info(f"🗑️  已清空旧物流费率数据 ({count} 条)")

    # 读取 Excel
    excel_path = os.path.join(ASSETS_DIR, "China_scoring_ENG_CN_21_04_26_1776754052 (1).xlsx")
    if not os.path.exists(excel_path):
        logger.warning(f"⚠️  物流费率文件不存在: {excel_path}")
        return

    try:
        import openpyxl
    except ImportError:
        logger.warning("⚠️  openpyxl 未安装，跳过物流费率导入")
        return

    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    ws = wb["中国 rFBS"]

    records = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i < 5:  # 跳过前5行（标题行）
            continue

        scoring_group = str(row[0] or "").strip()
        service_level = str(row[1] or "").strip()
        tpl_provider = str(row[2] or "").strip()
        delivery_method = str(row[3] or "").strip()

        if not scoring_group or scoring_group in ("переход", "None", "评分组"):
            continue
        if not tpl_provider or not service_level:
            continue

        weight_min = int(row[10]) if row[10] else 0
        weight_max = int(row[11]) if row[11] else 0

        # 解析费率字符串
        rate_str = str(row[6] or "").replace("￥", "¥").replace(",", ".").replace(" ", "")
        rate_str = re.sub(r"¥\.0\.", "¥0.", rate_str)
        m = re.search(r"¥([\d.]+)\+¥([\d.]+)/?1g", rate_str)
        if m:
            base_cost, per_gram_rate = float(m.group(1)), float(m.group(2))
        else:
            m2 = re.search(r"¥([\d.]+)", rate_str)
            base_cost, per_gram_rate = (float(m2.group(1)), 0.0) if m2 else (0.0, 0.0)

        # 解析尺寸限制
        limit_str = str(row[9] or "")
        sum_parts = re.findall(r"≤\s*(\d+)\s*cm", limit_str)
        sum_limit = int(sum_parts[0]) if sum_parts else 0
        longest_limit = int(sum_parts[1]) if len(sum_parts) > 1 else (int(sum_parts[0]) if sum_parts else 0)

        charge_type = "actual" if "实际" in str(row[16] or "") else "volumetric"
        vol_str = str(row[17] or "")
        vm = re.search(r"(\d+)", vol_str)
        vol_divisor = int(vm.group(1)) if vm else 0

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

    if not records:
        logger.warning("⚠️  未解析到物流费率记录")
        return

    # 批量插入
    with engine.connect() as conn:
        for rec in records:
            conn.execute(sql_text("""
                INSERT INTO logistics_rates
                    (scoring_group, service_level, tpl_provider, delivery_method,
                     base_cost, per_gram_rate, weight_min, weight_max,
                     sum_limit_cm, longest_limit_cm, charge_type, vol_weight_divisor)
                VALUES
                    (:scoring_group, :service_level, :tpl_provider, :delivery_method,
                     :base_cost, :per_gram_rate, :weight_min, :weight_max,
                     :sum_limit_cm, :longest_limit_cm, :charge_type, :vol_weight_divisor)
            """), rec)
        conn.commit()

    logger.info(f"✅ 物流费率导入完成: {len(records)} 条")


def import_attribute_cache(engine, force=False):
    """
    从 JSON 文件导入属性 schema 和字典值缓存到 PG。
    
    JSON 文件由 warm_category_cache.py 生成，存放在 assets/ 目录。
    部署时 deploy.sh → init_data.py 自动导入。
    """
    import json as _json
    import time as _time

    assets_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
    schemas_file = os.path.join(assets_dir, "attribute_schemas_zh.json")
    dict_values_file = os.path.join(assets_dir, "dictionary_values_zh.json")

    if not os.path.exists(schemas_file) and not os.path.exists(dict_values_file):
        logger.info("⏭️  属性缓存 JSON 文件不存在，跳过导入（运行时将从 Ozon API 懒加载）")
        return

    with engine.begin() as conn:
        # 检查是否已有数据
        count = conn.execute(sql_text(
            "SELECT COUNT(*) FROM attribute_cache WHERE language = 'ZH_HANS'"
        )).scalar()

        if count > 0 and not force:
            logger.info(f"⏭️  属性缓存已有 {count} 条记录，跳过导入（用 --force 强制覆盖）")
            return

        if force and count > 0:
            conn.execute(sql_text("DELETE FROM attribute_cache WHERE language = 'ZH_HANS'"))
            conn.execute(sql_text("DELETE FROM dictionary_value_cache WHERE language = 'ZH_HANS'"))
            logger.info(f"🗑️  已清空旧属性缓存数据 ({count} 条 schema)")

        now = int(_time.time())
        expires_schema = now + 7 * 86400
        expires_dict = now + 86400

        # 导入 attribute schemas
        if os.path.exists(schemas_file):
            with open(schemas_file, "r", encoding="utf-8") as f:
                schemas = _json.load(f)

            schema_count = 0
            for key, val in schemas.items():
                dc_str, type_str = key.split(":", 1)
                dc, tid = int(dc_str), int(type_str)
                conn.execute(sql_text("""
                    INSERT INTO attribute_cache (description_category_id, type_id, language, attributes_schema, expires_at, created_at)
                    VALUES (:dc, :tid, 'ZH_HANS', :schema::jsonb, :expires, :now)
                    ON CONFLICT (description_category_id, type_id, language)
                    DO UPDATE SET attributes_schema = EXCLUDED.attributes_schema,
                                  expires_at = EXCLUDED.expires_at
                """), {"dc": dc, "tid": tid, "schema": _json.dumps(val, ensure_ascii=False),
                       "expires": expires_schema, "now": now})
                schema_count += 1
            logger.info(f"✅ 导入属性 schema: {schema_count} 个类目")

        # 导入 dictionary values
        if os.path.exists(dict_values_file):
            with open(dict_values_file, "r", encoding="utf-8") as f:
                dict_values = _json.load(f)

            dict_count = 0
            for key, val in dict_values.items():
                parts = key.split(":", 2)
                attr_id, dc, tid = int(parts[0]), int(parts[1]), int(parts[2])
                conn.execute(sql_text("""
                    INSERT INTO dictionary_value_cache (attribute_id, description_category_id, type_id, language, values_data, expires_at, created_at)
                    VALUES (:aid, :dc, :tid, 'ZH_HANS', :vals::jsonb, :expires, :now)
                    ON CONFLICT (attribute_id, description_category_id, type_id, language)
                    DO UPDATE SET values_data = EXCLUDED.values_data,
                                  expires_at = EXCLUDED.expires_at
                """), {"aid": attr_id, "dc": dc, "tid": tid, "vals": _json.dumps(val, ensure_ascii=False),
                       "expires": expires_dict, "now": now})
                dict_count += 1
            logger.info(f"✅ 导入字典值: {dict_count} 个条目")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="首次部署数据初始化")
    parser.add_argument("--db-url", default=os.getenv("PGDATABASE_URL", ""), help="PG 连接串")
    parser.add_argument("--force", action="store_true", help="强制重新导入（清空旧数据）")
    args = parser.parse_args()

    if not args.db_url:
        logger.error("请设置 PGDATABASE_URL 环境变量或通过 --db-url 传入")
        sys.exit(1)

    from sqlalchemy import create_engine
    engine = create_engine(args.db_url)

    logger.info("═══ 首次部署数据初始化 ═══")

    # 1. 建表
    create_tables(engine)

    # 2. 导入类目树（中俄双语）
    import_category_tree(engine, language="ZH_HANS", force=args.force, tree_file="category_tree.json")
    import_category_tree(engine, language="RU", force=args.force, tree_file="category_tree_ru.json")

    # 3. 导入物流费率
    import_logistics_rates(engine, force=args.force)

    # 4. 导入属性 schema 和字典值缓存（从 JSON 文件，与类目树同级）
    import_attribute_cache(engine, force=args.force)

    # 5. 导入尺码表（服装尺码 → 俄罗斯尺码，供 size_mapper 查询）
    from import_size_tables import import_size_tables
    import_size_tables(engine, force=args.force)

    logger.info("═══ 初始化完成 ═══")


if __name__ == "__main__":
    main()
