"""
本地SQLite数据库管理器 - 用于缓存高频查询数据
用途：降低Supabase查询延迟（本地SQLite 1-5ms vs Supabase 50-200ms）
策略：本地优先查询 + Supabase Fallback + 双写机制
"""

import sqlite3
import json
import time
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalDBManager:
    """本地SQLite数据库管理器"""
    
    def __init__(self, db_path: str = "assets/local_cache.db"):
        """初始化本地数据库"""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 创建数据库连接
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # 返回字典格式
        
        # 初始化表结构
        self._init_tables()
        
        logger.info(f"✅ 本地SQLite数据库已初始化：{self.db_path}")
    
    def _init_tables(self):
        """创建本地缓存表"""
        cursor = self.conn.cursor()
        
        # ============================================================
        # 表1: attribute_cache（Ozon类目属性缓存）
        # ============================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS attribute_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description_category_id INTEGER NOT NULL,
                type_id INTEGER,
                language TEXT DEFAULT 'ZH_HANS',
                attributes_schema TEXT,  -- JSON格式存储
                expires_at INTEGER,  -- Unix时间戳（秒）
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                UNIQUE(description_category_id, type_id, language)
            )
        """)
        
        # 创建索引（加速查询）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_attribute_cache_composite 
            ON attribute_cache(description_category_id, type_id, language)
        """)
        
        # ============================================================
        # 表2: dictionary_value_cache（字典值缓存）
        # ============================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dictionary_value_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attribute_id INTEGER NOT NULL,
                description_category_id INTEGER NOT NULL,
                type_id INTEGER,
                language TEXT DEFAULT 'ZH_HANS',
                values_data TEXT,  -- JSON格式存储
                expires_at INTEGER,  -- Unix时间戳（秒）
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                UNIQUE(attribute_id, description_category_id, type_id, language)
            )
        """)
        
        # 创建索引（加速查询）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_dictionary_value_cache_composite 
            ON dictionary_value_cache(attribute_id, description_category_id, type_id, language)
        """)
        
        # ============================================================
        # 表3: category_cache（类目树缓存）
        # ============================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS category_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ozon_client_id TEXT NOT NULL,
                language TEXT DEFAULT 'ZH_HANS',
                tree_data TEXT,  -- JSON格式存储
                expires_at INTEGER,  -- Unix时间戳（秒）
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                UNIQUE(ozon_client_id, language)
            )
        """)
        
        # 创建索引（加速查询）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_category_cache_client 
            ON category_cache(ozon_client_id, language)
        """)
        
        # ============================================================
        # 表4: logistics_rates（物流费率）- ✅ 更新为12列新结构
        # ============================================================
        # 注意：如果表已存在（旧结构），CREATE TABLE IF NOT EXISTS不会重建
        # 索引创建需要兼容新结构（不含channel列）
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS logistics_rates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scoring_group TEXT NOT NULL,
                    service_level TEXT NOT NULL,
                    tpl_provider TEXT NOT NULL,
                    delivery_method TEXT,
                    base_cost REAL NOT NULL,
                    per_gram_rate REAL NOT NULL,
                    weight_min INTEGER NOT NULL,
                    weight_max INTEGER NOT NULL,
                    sum_limit_cm INTEGER NOT NULL,
                    longest_limit_cm INTEGER NOT NULL,
                    charge_type TEXT NOT NULL,
                    vol_weight_divisor INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                )
            """)
        except Exception:
            pass  # 表已存在（可能是旧结构），跳过创建
        
        # 创建索引（加速查询）- ✅ 兼容新结构（不含channel列）
        try:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_logistics_rates_weight 
                ON logistics_rates(weight_min, weight_max)
            """)
        except Exception as e:
            logger.warning(f"创建logistics_rates索引跳过（可能列不存在）: {e}")
        
        # ============================================================
        # 表5: exchange_rates（汇率）
        # ============================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS exchange_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_currency TEXT NOT NULL,
                to_currency TEXT NOT NULL,
                rate REAL NOT NULL,
                updated_at INTEGER DEFAULT (strftime('%s', 'now')),
                UNIQUE(from_currency, to_currency)
            )
        """)
        
        # 创建索引（加速查询）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_exchange_rates_currency 
            ON exchange_rates(from_currency, to_currency)
        """)
        
        # ============================================================
        # 表6: ozon_attribute_mappings（属性映射学习记录）
        # ============================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ozon_attribute_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                attribute_id INTEGER NOT NULL,
                attribute_name TEXT,
                source_value TEXT,
                target_value TEXT,
                dictionary_value_id INTEGER,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                last_used_at INTEGER DEFAULT (strftime('%s', 'now')),
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                UNIQUE(category_id, attribute_id, source_value)
            )
        """)
        
        # 创建索引（加速查询）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ozon_attribute_mappings_category 
            ON ozon_attribute_mappings(category_id, attribute_id)
        """)
        
        # ============================================================
        # 表7: gateway_tasks（任务状态追踪 - 双写）
        # ============================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gateway_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'pending',
                result_json TEXT,  -- JSON格式存储
                stages TEXT,  -- JSON格式存储
                error TEXT,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)
        
        # 创建索引（加速查询）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_gateway_tasks_task_id 
            ON gateway_tasks(task_id)
        """)
        
        self.conn.commit()
        logger.info("✅ 本地缓存表已创建（7个表）")
    
    # ============================================================
    # 查询方法：优先本地查询，Supabase作为Fallback
    # ============================================================
    
    def get_attribute_cache(self, description_category_id: int, type_id: Optional[int], language: str = "ZH_HANS") -> Optional[Dict[str, Any]]:
        """查询属性缓存（本地优先）"""
        current_time = int(time.time())
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT attributes_schema, expires_at 
            FROM attribute_cache 
            WHERE description_category_id = ? 
            AND type_id = ? 
            AND language = ?
            AND expires_at > ?
        """, (description_category_id, type_id, language, current_time))
        
        row = cursor.fetchone()
        if row:
            logger.info(f"✅ 本地查询命中：attribute_cache（category_id={description_category_id}）")
            return {
                "attributes_schema": json.loads(row["attributes_schema"]) if row["attributes_schema"] else None,
                "expires_at": row["expires_at"]
            }
        
        logger.info(f"❌ 本地查询未命中：attribute_cache（category_id={description_category_id}）")
        return None
    
    def get_dictionary_value_cache(self, attribute_id: int, description_category_id: int, type_id: Optional[int], language: str = "ZH_HANS") -> Optional[Dict[str, Any]]:
        """查询字典值缓存（本地优先）"""
        current_time = int(time.time())
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT values_data, expires_at 
            FROM dictionary_value_cache 
            WHERE attribute_id = ? 
            AND description_category_id = ? 
            AND type_id = ? 
            AND language = ?
            AND expires_at > ?
        """, (attribute_id, description_category_id, type_id, language, current_time))
        
        row = cursor.fetchone()
        if row:
            logger.info(f"✅ 本地查询命中：dictionary_value_cache（attribute_id={attribute_id}）")
            return {
                "values_data": json.loads(row["values_data"]) if row["values_data"] else None,
                "expires_at": row["expires_at"]
            }
        
        logger.info(f"❌ 本地查询未命中：dictionary_value_cache（attribute_id={attribute_id}）")
        return None
    
    def get_category_cache(self, ozon_client_id: str, language: str = "ZH_HANS") -> Optional[Dict[str, Any]]:
        """查询类目树缓存（本地优先）"""
        current_time = int(time.time())
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT tree_data, expires_at 
            FROM category_cache 
            WHERE ozon_client_id = ? 
            AND language = ?
            AND expires_at > ?
        """, (ozon_client_id, language, current_time))
        
        row = cursor.fetchone()
        if row:
            logger.info(f"✅ 本地查询命中：category_cache（client_id={ozon_client_id}）")
            return {
                "tree_data": json.loads(row["tree_data"]) if row["tree_data"] else None,
                "expires_at": row["expires_at"]
            }
        
        logger.info(f"❌ 本地查询未命中：category_cache（client_id={ozon_client_id}）")
        return None
    
    def get_logistics_cost(self, weight: float, channel: str = "standard") -> Optional[Dict[str, Any]]:
        """查询物流费率（本地优先）- ✅ 兼容新12列结构"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT base_cost, per_gram_rate, scoring_group, service_level, tpl_provider
                FROM logistics_rates 
                WHERE weight_min <= ? 
                AND weight_max >= ? 
                LIMIT 1
            """, (weight, weight))
            
            row = cursor.fetchone()
            if row:
                logger.info(f"✅ 本地查询命中：logistics_rates（weight={weight}g）")
                return {
                    "base_cost": row["base_cost"],
                    "per_gram_rate": row["per_gram_rate"],
                    "scoring_group": row["scoring_group"],
                    "service_level": row["service_level"],
                    "tpl_provider": row["tpl_provider"]
                }
        except Exception as e:
            logger.warning(f"⚠️ logistics_rates查询异常: {e}")
        
        logger.info(f"❌ 本地查询未命中：logistics_rates（weight={weight}g）")
        return None
    
    def get_exchange_rate(self, from_currency: str = "CNY", to_currency: str = "RUB") -> Optional[float]:
        """查询汇率（本地优先）"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT rate, updated_at 
            FROM exchange_rates 
            WHERE from_currency = ? 
            AND to_currency = ?
        """, (from_currency, to_currency))
        
        row = cursor.fetchone()
        if row:
            # 检查汇率是否过期（24小时）
            current_time = int(time.time())
            if current_time - row["updated_at"] < 86400:  # 24小时有效
                logger.info(f"✅ 本地查询命中：exchange_rates（{from_currency}→{to_currency}）")
                return row["rate"]
            else:
                logger.info(f"❌ 汇率已过期：exchange_rates（{from_currency}→{to_currency}）")
                return None
        
        logger.info(f"❌ 本地查询未命中：exchange_rates（{from_currency}→{to_currency}）")
        return None
    
    def get_attribute_mappings(self, category_id: int) -> List[Dict[str, Any]]:
        """查询属性映射学习记录（本地优先）"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * 
            FROM ozon_attribute_mappings 
            WHERE category_id = ?
            ORDER BY success_count DESC, last_used_at DESC
        """, (category_id,))
        
        rows = cursor.fetchall()
        if rows:
            logger.info(f"✅ 本地查询命中：ozon_attribute_mappings（category_id={category_id}，{len(rows)}条）")
            return [dict(row) for row in rows]
        
        logger.info(f"❌ 本地查询未命中：ozon_attribute_mappings（category_id={category_id}）")
        return []
    
    def get_gateway_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """查询任务状态（本地优先）"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * 
            FROM gateway_tasks 
            WHERE task_id = ?
        """, (task_id,))
        
        row = cursor.fetchone()
        if row:
            logger.info(f"✅ 本地查询命中：gateway_tasks（task_id={task_id}）")
            return dict(row)
        
        logger.info(f"❌ 本地查询未命中：gateway_tasks（task_id={task_id}）")
        return None
    
    # ============================================================
    # 写入方法：双写机制（本地SQLite + Supabase）
    # ============================================================
    
    def set_attribute_cache(self, description_category_id: int, type_id: Optional[int], attributes_schema: Dict[str, Any], language: str = "ZH_HANS", expires_in: int = 86400):
        """写入属性缓存（双写）"""
        current_time = int(time.time())
        expires_at = current_time + expires_in
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO attribute_cache 
            (description_category_id, type_id, language, attributes_schema, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (description_category_id, type_id, language, json.dumps(attributes_schema), expires_at, current_time))
        
        self.conn.commit()
        logger.info(f"✅ 本地写入成功：attribute_cache（category_id={description_category_id}，有效期{expires_in}秒）")
    
    def set_dictionary_value_cache(self, attribute_id: int, description_category_id: int, type_id: Optional[int], values_data: List[Dict[str, Any]], language: str = "ZH_HANS", expires_in: int = 86400):
        """写入字典值缓存（双写）"""
        current_time = int(time.time())
        expires_at = current_time + expires_in
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO dictionary_value_cache 
            (attribute_id, description_category_id, type_id, language, values_data, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (attribute_id, description_category_id, type_id, language, json.dumps(values_data), expires_at, current_time))
        
        self.conn.commit()
        logger.info(f"✅ 本地写入成功：dictionary_value_cache（attribute_id={attribute_id}，有效期{expires_in}秒）")
    
    def set_category_cache(self, ozon_client_id: str, tree_data: Dict[str, Any], language: str = "ZH_HANS", expires_in: int = 86400):
        """写入类目树缓存（双写）"""
        current_time = int(time.time())
        expires_at = current_time + expires_in
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO category_cache 
            (ozon_client_id, language, tree_data, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (ozon_client_id, language, json.dumps(tree_data), expires_at, current_time))
        
        self.conn.commit()
        logger.info(f"✅ 本地写入成功：category_cache（client_id={ozon_client_id}，有效期{expires_in}秒）")
    
    def set_exchange_rate(self, from_currency: str, to_currency: str, rate: float):
        """写入汇率（双写）"""
        current_time = int(time.time())
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO exchange_rates 
            (from_currency, to_currency, rate, updated_at)
            VALUES (?, ?, ?, ?)
        """, (from_currency, to_currency, rate, current_time))
        
        self.conn.commit()
        logger.info(f"✅ 本地写入成功：exchange_rates（{from_currency}→{to_currency}={rate}）")
    
    def set_gateway_task(self, task_id: str, status: str, result_json: Optional[Dict[str, Any]] = None, stages: Optional[Dict[str, str]] = None, error: Optional[str] = None):
        """写入任务状态（双写）"""
        current_time = int(time.time())
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO gateway_tasks 
            (task_id, status, result_json, stages, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (task_id, status, json.dumps(result_json) if result_json else None, json.dumps(stages) if stages else None, error, current_time, current_time))
        
        self.conn.commit()
        logger.info(f"✅ 本地写入成功：gateway_tasks（task_id={task_id}，status={status}）")
    
    def add_attribute_mapping(self, category_id: int, attribute_id: int, attribute_name: str, source_value: str, target_value: str, dictionary_value_id: Optional[int] = None):
        """添加属性映射学习记录（双写）"""
        current_time = int(time.time())
        
        cursor = self.conn.cursor()
        
        # 检查是否已存在
        cursor.execute("""
            SELECT id, success_count, fail_count 
            FROM ozon_attribute_mappings 
            WHERE category_id = ? 
            AND attribute_id = ? 
            AND source_value = ?
        """, (category_id, attribute_id, source_value))
        
        row = cursor.fetchone()
        if row:
            # 更新成功计数
            cursor.execute("""
                UPDATE ozon_attribute_mappings 
                SET success_count = success_count + 1, 
                    last_used_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (current_time, current_time, row["id"]))
            logger.info(f"✅ 本地更新成功：ozon_attribute_mappings（映射已存在，success_count+1）")
        else:
            # 插入新映射
            cursor.execute("""
                INSERT INTO ozon_attribute_mappings 
                (category_id, attribute_id, attribute_name, source_value, target_value, dictionary_value_id, success_count, last_used_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (category_id, attribute_id, attribute_name, source_value, target_value, dictionary_value_id, current_time, current_time))
            logger.info(f"✅ 本地写入成功：ozon_attribute_mappings（新映射）")
        
        self.conn.commit()
    
    def close(self):
        """关闭数据库连接"""
        self.conn.close()
        logger.info("✅ 本地SQLite数据库连接已关闭")