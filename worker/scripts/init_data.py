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


def register_schema_migration(engine, version, note=""):
    """BL-09（repo-gov B2-β）: 结构性迁移登记（幂等）——schema_migrations 一行。

    每个迁移（migrate_webui_v1 / migrate_sync_erp_v1 / migrate_drafts_batch_v1 /
    migrate_repo_gov_b2b）执行成功后调一次；ON CONFLICT (version) DO NOTHING →
    重复初始化 no-op。登记失败仅 warning 不阻断初始化（登记是观测面不是闸门——
    迁移本身全部幂等 DDL，漏登记只会让下次初始化重跑一遍 no-op）。
    """
    from sqlalchemy import text as sql_text

    try:
        with engine.begin() as conn:
            conn.execute(sql_text(
                "INSERT INTO schema_migrations (version, note) VALUES (:version, :note) "
                "ON CONFLICT (version) DO NOTHING"
            ), {"version": version, "note": (note or "")[:500]})
        logger.info(f"✅ 迁移登记: {version}")
    except Exception as exc:
        logger.warning(
            "⚠️ schema_migrations 登记失败 version=%s（不阻断初始化）: %s",
            version, str(exc)[:200],
        )


def migrate_repo_gov_b2b(engine):
    """BL-16（repo-gov B2-β）: draft_submissions 补 tenant_id 列（幂等，二次运行 no-op）。

    新建库 create_all 已带列（model.py DraftSubmission.tenant_id），此处兜底存量库：
    ADD COLUMN IF NOT EXISTS + 同名索引（SQLAlchemy index=True 生成的默认名
    ix_draft_submissions_tenant_id）。可空列——存量行保持 NULL，不回填不阻塞。
    纯 DDL 无绑定参数（text() 裸 cast 坑不适用，见 AGENTS 记忆 sqlalchemy-jsonb-cast-trap）。
    """
    from sqlalchemy import text as sql_text

    with engine.connect() as conn:
        conn.execute(sql_text(
            "ALTER TABLE draft_submissions ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)"
        ))
        conn.execute(sql_text(
            "CREATE INDEX IF NOT EXISTS ix_draft_submissions_tenant_id "
            "ON draft_submissions (tenant_id)"
        ))
        conn.commit()
    register_schema_migration(
        engine, "2026-09-repo-gov-b2b",
        "BL-16 draft_submissions.tenant_id 加列（提交行租户归属；存量行 NULL 不回填）",
    )


def migrate_repo_gov_v075(engine):
    """v0.75 C3（repo-gov audit tenant）: category_match_log / attr_match_log 补 tenant_id。

    新建库 create_all 已带列（model.py tenant_id, index=True → 默认名
    ix_<table>_tenant_id），此处兜底存量库 ADD COLUMN IF NOT EXISTS + 同名索引 +
    历史回填（审计行 task_id == thread_id == 任务 uuid → 任务行 tenant_id）。
    ⚠️ v0.67 前审计行 task_id 是 ingest 随机 uuid、任务行已被 30 天归档删除的
    审计行——join 不上保持 NULL（SELECT count 如实输出，P1-6 断层不掩盖）。
    纯 DDL/UPDATE 无绑定参数（text() 裸 cast 坑不适用——::text 是列 cast）。
    """
    from sqlalchemy import text as sql_text

    _TABLES = ("category_match_log", "attr_match_log")
    # 结构性 DDL（加列+索引）：**响失败**——列缺失会让写侧 ORM（带 tenant_id 的
    # INSERT）运行时 500，正是 H9 fail-fast 要暴露的「schema 半就绪」；对齐
    # migrate_repo_gov_b2b/migrate_token_fp 的既有模式（结构性迁移 raise）。
    with engine.connect() as conn:
        for _table in _TABLES:
            conn.execute(sql_text(
                f"ALTER TABLE {_table} ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)"
            ))
            conn.execute(sql_text(
                f"CREATE INDEX IF NOT EXISTS ix_{_table}_tenant_id ON {_table} (tenant_id)"
            ))
        conn.commit()
    # 历史回填（数据面）：软失败——join 不上/锁竞争只影响存量行补租户，
    # 双写已保证新行带租户；失败留待下次 init_data 重跑，不阻断升级。
    try:
        _backfilled = 0
        with engine.connect() as conn:
            for _table in _TABLES:
                res = conn.execute(sql_text(
                    f"UPDATE {_table} m SET tenant_id = t.tenant_id "
                    "FROM ozon_product_tasks t "
                    "WHERE m.task_id::text = t.id::text "
                    "AND t.tenant_id IS NOT NULL AND m.tenant_id IS NULL"
                ))
                _backfilled += int(res.rowcount or 0)
            _still_null = 0
            for _table in _TABLES:
                _still_null += int(conn.execute(sql_text(
                    f"SELECT count(*) FROM {_table} WHERE tenant_id IS NULL"
                )).scalar_one() or 0)
            conn.commit()
        logger.info(
            "✅ 审计表 tenant_id 历史回填: %d 行；join 不上保持 NULL %d 行"
            "（v0.67 前 ingest 随机 uuid / 任务行已归档删除——已知断层如实保留）",
            _backfilled, _still_null,
        )
    except Exception as exc:
        logger.warning(
            "⚠️ 审计表 tenant_id 历史回填失败（列/索引已就绪，不阻断初始化，下次 init_data 重跑）: %s",
            str(exc)[:200],
        )
    register_schema_migration(
        engine, "repo_gov_v075_audit_tenant",
        "v0.75 C3 category_match_log/attr_match_log 补 tenant_id 列+索引+历史回填（join 任务表）",
    )


# A8 F6/BL-06（repo-gov B5）：MXOU key 明文落库的五张贡献表 → token_fp 指纹列。
# (表名, 明文来源列)：discovery_runs 的明文在 tenant_id（_handle_discovery_run_report
# 写 clean token，probe_assets S5 同结论），其余四表在 contributed_by_token_id。
_TOKEN_FP_TABLES = (
    ("blue_ocean_queries", "contributed_by_token_id"),
    ("ozon_bestsellers", "contributed_by_token_id"),
    ("market_bestsellers", "contributed_by_token_id"),
    ("selection_insights", "contributed_by_token_id"),
    ("discovery_runs", "tenant_id"),
)

TOKEN_FP_MIGRATION_VERSION = "2026-09-repo-gov-b5-tokenfp"


def migrate_token_fp(engine):
    """A8 F6（repo-gov B5）: 五贡献表加 token_fp 指纹列（幂等，二次运行 no-op）。

    新建库 create_all 已带列（model.py token_fp, index=True → 默认名
    ix_<table>_token_fp），此处兜底存量库 ADD COLUMN IF NOT EXISTS + 同名索引。
    可空列——存量行 NULL 由 :func:`migrate_token_fp_backfill` 分页回填。
    纯 DDL 无绑定参数（text() 裸 cast 坑不适用）。
    """
    from sqlalchemy import text as sql_text

    with engine.connect() as conn:
        for table, _src_col in _TOKEN_FP_TABLES:
            conn.execute(sql_text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS token_fp VARCHAR(16)"
            ))
            conn.execute(sql_text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_token_fp ON {table} (token_fp)"
            ))
        conn.commit()
    register_schema_migration(
        engine, TOKEN_FP_MIGRATION_VERSION,
        "A8 F6/BL-06 五贡献表 token_fp 指纹加列（MXOU key 明文落库脱敏双写；存量行回填）",
    )


def migrate_token_fp_backfill(engine, batch_size=500):
    """A8 F6 第 4 步: 存量行 token_fp 分页回填（Python 侧算指纹，幂等可重跑）。

    为什么不用纯 SQL：PG 内置 sha256 需 pgcrypto 扩展（生产未必可装），指纹算法
    必须与 services.tenant_service.token_fingerprint 单一实现逐字一致——纯 SQL
    双实现会漂移。故 SELECT id,明文（仅 token_fp IS NULL 行）→ Python 逐批算
    sha256 前 16 → 批量 UPDATE，每批一提交（对齐 import_attribute_cache 分批纪律，
    防大表单事务抬高锁/内存窗口）。
    幂等：已回填行不再命中 WHERE token_fp IS NULL → 重跑 no-op；空明文行
    （历史脏数据）跳过不回填（保持 NULL，指纹语义上无 key 可指）。
    返回本轮回填行数（含跨表累计；重跑应得 0）。
    """
    from sqlalchemy import text as sql_text

    from services.tenant_service import token_fingerprint

    total = 0
    for table, src_col in _TOKEN_FP_TABLES:
        while True:
            with engine.connect() as conn:
                rows = conn.execute(sql_text(
                    f"SELECT id, {src_col} FROM {table} "
                    f"WHERE token_fp IS NULL AND {src_col} IS NOT NULL AND {src_col} != '' "
                    f"ORDER BY id LIMIT :batch"
                ), {"batch": int(batch_size)}).fetchall()
            if not rows:
                break
            updates = [
                {"id": r[0], "fp": token_fingerprint(str(r[1]))}
                for r in rows
            ]
            with engine.begin() as conn:
                conn.execute(
                    sql_text(f"UPDATE {table} SET token_fp = :fp WHERE id = :id"),
                    updates,
                )
            total += len(updates)
            if len(rows) < int(batch_size):
                break
        logger.info("token_fp 回填 %s 完成（累计 %d 行）", table, total)
    return total


def create_tables(engine):
    """创建所有表（幂等）。"""
    from storage.database.shared.model import Base
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(text("SET pg_trgm.similarity_threshold = 0.05"))
        conn.commit()

    Base.metadata.create_all(bind=engine)
    # ✅ v0.25 T1: category_mapping 追加 1688 类目数字 ID 列（幂等）
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE category_mapping ADD COLUMN IF NOT EXISTS source_category_id BIGINT"
        ))
        # ✅ v0.37 P0-1: ozon_product_tasks 追加 SKU 去重列 + 部分唯一索引（幂等）
        conn.execute(text(
            "ALTER TABLE ozon_product_tasks ADD COLUMN IF NOT EXISTS sku_key TEXT"
        ))
        # ⚠️ v0.38.1: 索引谓词加状态过滤（只对 pending/running 唯一）——修复 resubmit
        # 以相同 sku_key 重插新行撞唯一索引 → 500。旧谓词索引必须 DROP 重建才能生效。
        conn.execute(text(
            "DROP INDEX IF EXISTS uq_ozon_product_tasks_tenant_sku"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ozon_product_tasks_tenant_sku "
            "ON ozon_product_tasks(tenant_id, sku_key) "
            "WHERE sku_key IS NOT NULL AND status IN ('pending', 'running')"
        ))
        # ✅ v0.42 M0.1: WebUI 运营工作台数据模型迁移（幂等，二次运行 no-op）
        # 存量行 draft_id/error_message 保持 NULL（无可信反向链接，不回填）
        conn.execute(text(
            "ALTER TABLE draft_submissions ADD COLUMN IF NOT EXISTS error_message TEXT"
        ))
        # ✅ 任务状态写回依赖 updated_at 列（status_writeback UPDATE ... updated_at=NOW()）
        conn.execute(text(
            "ALTER TABLE draft_submissions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL"
        ))
        # 存量库 draft_id 仍为 NOT NULL → 必须解除约束，直连任务（draft_id=NULL）行才能插入
        conn.execute(text(
            "ALTER TABLE draft_submissions ALTER COLUMN draft_id DROP NOT NULL"
        ))
        conn.execute(text(
            "ALTER TABLE product_task_index ADD COLUMN IF NOT EXISTS draft_id UUID"
        ))
        # ✅ v0.52 P1b: listing_templates 追加店铺级覆盖列（幂等）
        conn.execute(text(
            "ALTER TABLE listing_templates ADD COLUMN IF NOT EXISTS store_overrides JSONB NOT NULL DEFAULT '{}'::jsonb"
        ))
        # ✅ v0.56.7: ozon_product_tasks 关键列默认值补齐（幂等）
        # v0.56.2 只在 model.py 加 server_default（对新建表生效），存量旧表缺默认值 →
        # 升级后 INSERT 不显式传这些列违反 NOT NULL（生产实测 37 测试失败）。
        # 双保险：ADD COLUMN 覆盖缺列场景；SET DEFAULT 覆盖"列在但无默认值"场景。
        _task_col_defaults = [
            ("status", "VARCHAR(20) NOT NULL DEFAULT 'pending'", "'pending'"),
            ("priority", "INTEGER NOT NULL DEFAULT 0", "0"),
            ("retry_count", "INTEGER NOT NULL DEFAULT 0", "0"),
            ("max_retries", "INTEGER NOT NULL DEFAULT 3", "3"),
            ("timeout_seconds", "INTEGER NOT NULL DEFAULT 1800", "1800"),
        ]
        for _col, _decl, _default in _task_col_defaults:
            conn.execute(text(
                f"ALTER TABLE ozon_product_tasks ADD COLUMN IF NOT EXISTS {_col} {_decl}"
            ))
            conn.execute(text(
                f"ALTER TABLE ozon_product_tasks ALTER COLUMN {_col} SET DEFAULT {_default}"
            ))
        # ✅ v0.67: 上架结果留存分析表 + P1-6 审计关联修复（幂等，二次运行 no-op）
        # listing_result_log 由 Base.metadata.create_all 建表（新表）；此处兜底：
        # 存量半迁移库缺索引 → CREATE INDEX IF NOT EXISTS 补齐（对齐 __table_args__）。
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_listing_result_tenant ON listing_result_log (tenant_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_listing_result_client ON listing_result_log (ozon_client_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_listing_result_status ON listing_result_log (final_status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_listing_result_created ON listing_result_log (created_at)"))
        # ✅ v0.67 P1-6: category_match_log 追加 1688 货源链接列（审计行可溯源到货源卡）
        conn.execute(text(
            "ALTER TABLE category_match_log ADD COLUMN IF NOT EXISTS source_url TEXT"
        ))
        # ✅ v0.67.1 wave①: listing_result_log 追加审核拒绝原文累积列（decline_errors 全链透传落点）
        conn.execute(text(
            "ALTER TABLE listing_result_log ADD COLUMN IF NOT EXISTS moderation_texts JSONB"
        ))
        # ✅ v0.70 A 批次: 采集箱运营备注列（skill --note / webui 编辑抽屉写入；不进信封 payload）
        conn.execute(text(
            "ALTER TABLE product_drafts ADD COLUMN IF NOT EXISTS notes TEXT"
        ))
        # ✅ v0.70: 表达式索引——uuid 主键与文本 ID 全链路混用的提速补丁（DB-SCHEMA-AUDIT #3）。
        # error_report_service._task_snapshots / task_service / forensics 均按
        # `id::text = :x` 查询，裸 PK btree 索引走不上；表达式索引让取证/状态查询
        # 在任务表变大后不至于全表扫。draft_submissions.submitted_task_id 同理加普通索引
        # （Text 无 FK，审计文档记为待观察，不加 FK 防存量脏引用阻塞建索引）。
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_ozon_product_tasks_id_text"
            " ON ozon_product_tasks ((id::text))"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_draft_submissions_submitted_task"
            " ON draft_submissions (submitted_task_id)"
        ))
        conn.commit()
    # ✅ v0.41 WebUI T1: task_generated_images ALTER + 新表索引（幂等，二次运行 no-op）
    # ✅ BL-09（repo-gov B2-β）: 每个结构性迁移执行成功后登记版本（幂等；version 从函数名推）
    from migrate_webui_v1 import run_migrations
    run_migrations(engine)
    register_schema_migration(
        engine, "2026-09-webui-v1",
        "WebUI v1 数据层迁移（product_drafts/draft_submissions/credentials/product_task_index/task_generated_images）",
    )
    # ✅ PRD store-sync-ERP v1: 同步任务/日聚合/成本货源/退货/进度事件等新表与扩列（幂等）
    from migrate_sync_erp_v1 import run_migrations as run_sync_migrations
    run_sync_migrations(engine)
    register_schema_migration(
        engine, "2026-09-sync-erp-v1",
        "store-sync-ERP v1 迁移（同步任务/日聚合/成本货源/退货/进度事件等新表与扩列）",
    )
    # ✅ T-P3.1 批次契约: product_drafts.source_batch 加列（幂等；新建库 create_all 已带列，此处兜底存量/半迁移库）
    from migrate_drafts_batch_v1 import run_migrations as run_drafts_batch_migrations
    run_drafts_batch_migrations(engine)
    register_schema_migration(
        engine, "2026-09-drafts-batch-v1",
        "T-P3.1 product_drafts.source_batch 加列（采集批次契约）",
    )
    # ✅ BL-16（repo-gov B2-β）: draft_submissions.tenant_id 加列 + 版本登记（幂等）
    migrate_repo_gov_b2b(engine)
    # ✅ A8 F6/BL-06（repo-gov B5）: 五贡献表 token_fp 指纹加列 + 存量回填（幂等）。
    # 回填失败不阻断初始化（双写已保证新行有指纹；失败行留待下次 init_data 重跑）。
    migrate_token_fp(engine)
    try:
        _backfilled = migrate_token_fp_backfill(engine)
        logger.info("✅ token_fp 存量回填完成: %d 行", _backfilled)
    except Exception as exc:
        logger.warning("⚠️ token_fp 回填失败（不阻断初始化，下次 init_data 重跑）: %s", str(exc)[:200])
    # ✅ v0.75 C3（repo-gov audit tenant）: 双审计表补 tenant_id 列+索引（结构性，
    # **响失败**——H9 fail-fast 语义，对齐 b2b/token_fp 模式）+ 历史回填（函数内软失败）。
    migrate_repo_gov_v075(engine)
    # ✅ v0.75 C4（audit A4 F-P1-1）: 数值 bounds 拒单学习表 attr_bounds_learned——
    # 新建库 create_all 已建表（model.AttrBoundLearned），无 ALTER 语句，仅登记
    # 迁移版本供观测（幂等）
    register_schema_migration(
        engine, "repo_gov_v075_bounds",
        "attr_bounds_learned 表（create_all 建表，无 ALTER）",
    )
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
    excel_path = os.path.join(ASSETS_DIR, "china_scoring_freight.xlsx")
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
    from sqlalchemy import text as sql_text

    assets_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
    schemas_file = os.path.join(assets_dir, "attribute_schemas_zh.json")
    dict_values_file = os.path.join(assets_dir, "dictionary_values_zh.json")

    if not os.path.exists(schemas_file) and not os.path.exists(dict_values_file):
        # ⚠️ v0.70: warning 级提示——部署即全量依赖 COS 下载的缓存 JSON
        # （docs/CACHE-WARM-RUNBOOK.md）；缺失意味着该部署走懒加载（首次上架偏慢）
        logger.warning(
            "⚠️ 属性缓存 JSON 未就绪（worker/assets/attribute_schemas_zh.json / "
            "dictionary_values_zh.json 均缺失）。本次部署运行时将从 Ozon API 懒加载"
            "（首次上架偏慢）。全量缓存获取方式见 docs/CACHE-WARM-RUNBOOK.md")
        return

    # ✅ v0.72: 单大事务改分批——原实现整个导入包在一个 engine.begin() 里
    # （--force 还先 DELETE 再同事务重灌），数千行 × 数百 KB JSONB 一次性提交
    # 抬高 PG 内存/锁窗口（warm v1.1 同款事故形态）。改每 _BATCH 行一提交。
    _BATCH = 200

    # 检查已有数据（独立短事务）
    with engine.begin() as conn:
        count = conn.execute(sql_text(
            "SELECT COUNT(*) FROM attribute_cache WHERE language = 'ZH_HANS'"
        )).scalar()

    if count > 0 and not force:
        logger.info(f"⏭️  属性缓存已有 {count} 条记录，跳过导入（用 --force 强制覆盖）")
        return

    now = int(_time.time())
    # ✅ v0.70: 30 天 TTL（与 warm_category_cache / local_db_manager 三处一致）——
    # schema/字典值低频变化，1 天字典 TTL 曾使全量预热一周内衰减回懒加载
    expires_schema = now + 30 * 86400
    expires_dict = now + 30 * 86400

    # 导入 attribute schemas（分批事务）
    if os.path.exists(schemas_file):
        with open(schemas_file, "r", encoding="utf-8") as f:
            schemas = _json.load(f)

        conn = engine.connect()
        trans = conn.begin()
        schema_count = 0
        try:
            if force and count > 0:
                conn.execute(sql_text("DELETE FROM attribute_cache WHERE language = 'ZH_HANS'"))
                logger.info(f"🗑️  已清空旧属性 schema ({count} 条)")
            for key, val in schemas.items():
                dc_str, type_str = key.split(":", 1)
                dc, tid = int(dc_str), int(type_str)
                conn.execute(sql_text("""
                    INSERT INTO attribute_cache (description_category_id, type_id, language, attributes_schema, expires_at, created_at)
                    VALUES (:dc, :tid, 'ZH_HANS', CAST(:schema AS jsonb), :expires, :now)
                    ON CONFLICT (description_category_id, type_id, language)
                    DO UPDATE SET attributes_schema = EXCLUDED.attributes_schema,
                                  expires_at = EXCLUDED.expires_at
                """), {"dc": dc, "tid": tid, "schema": _json.dumps(val, ensure_ascii=False),
                       "expires": expires_schema, "now": now})
                schema_count += 1
                if schema_count % _BATCH == 0:
                    trans.commit()
                    trans = conn.begin()
            trans.commit()
            logger.info(f"✅ 导入属性 schema: {schema_count} 个类目")
        except Exception:
            trans.rollback()
            raise
        finally:
            conn.close()

    # 导入 dictionary values（分批事务）
    if os.path.exists(dict_values_file):
        with open(dict_values_file, "r", encoding="utf-8") as f:
            dict_values = _json.load(f)

        conn = engine.connect()
        trans = conn.begin()
        dict_count = 0
        try:
            if force and count > 0:
                conn.execute(sql_text("DELETE FROM dictionary_value_cache WHERE language = 'ZH_HANS'"))
                logger.info("🗑️  已清空旧字典值缓存")
            for key, val in dict_values.items():
                parts = key.split(":", 2)
                attr_id, dc, tid = int(parts[0]), int(parts[1]), int(parts[2])
                # ✅ v0.72 三桶：全局桶行以 "aid:0:0" 键形状原样过账（零 DDL）
                conn.execute(sql_text("""
                    INSERT INTO dictionary_value_cache (attribute_id, description_category_id, type_id, language, values_data, expires_at, created_at)
                    VALUES (:aid, :dc, :tid, 'ZH_HANS', CAST(:vals AS jsonb), :expires, :now)
                    ON CONFLICT (attribute_id, description_category_id, type_id, language)
                    DO UPDATE SET values_data = EXCLUDED.values_data,
                                  expires_at = EXCLUDED.expires_at
                """), {"aid": attr_id, "dc": dc, "tid": tid, "vals": _json.dumps(val, ensure_ascii=False),
                       "expires": expires_dict, "now": now})
                dict_count += 1
                if dict_count % _BATCH == 0:
                    trans.commit()
                    trans = conn.begin()
            trans.commit()
            logger.info(f"✅ 导入字典值: {dict_count} 个条目")
        except Exception:
            trans.rollback()
            raise
        finally:
            conn.close()


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

    # 6. v0.63: curated 1688→Ozon 类目映射种子（幂等）
    from utils.category_mapping_learn import seed_curated_mapping
    os.environ["PGDATABASE_URL"] = os.environ.get("PGDATABASE_URL") or args.db_url
    seed_curated_mapping()

    logger.info("═══ 初始化完成 ═══")


if __name__ == "__main__":
    main()
