-- ============================================================
-- Supabase数据库表创建脚本（纯SQL版本）
-- 用途：解决Supabase 404错误，创建必需的表
-- 使用方法：复制粘贴到Supabase SQL Editor，点击Run执行
-- ============================================================

-- ============================================================
-- Step 1: 创建gateway_tasks表（任务状态追踪）
-- ============================================================

CREATE TABLE IF NOT EXISTS gateway_tasks (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'pending',
    result_json JSONB,
    stages JSONB,
    error TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 创建索引（加速task_id查询）
CREATE INDEX IF NOT EXISTS idx_gateway_tasks_task_id ON gateway_tasks(task_id);

-- 创建更新时间触发器（自动更新updated_at）
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_gateway_tasks_updated_at 
    BEFORE UPDATE ON gateway_tasks 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- Step 2: 创建attribute_cache表（Ozon类目属性缓存）
-- ============================================================

CREATE TABLE IF NOT EXISTS attribute_cache (
    id BIGSERIAL PRIMARY KEY,
    description_category_id BIGINT NOT NULL,
    type_id BIGINT,
    language TEXT DEFAULT 'ZH_HANS',
    attributes_schema JSONB,
    expires_at BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 创建复合索引（加速category+type+language查询）
CREATE INDEX IF NOT EXISTS idx_attribute_cache_composite 
ON attribute_cache(description_category_id, type_id, language);

-- 创建过期时间索引（加速缓存清理）
CREATE INDEX IF NOT EXISTS idx_attribute_cache_expires_at 
ON attribute_cache(expires_at);

-- ============================================================
-- Step 3: 创建dictionary_value_cache表（字典值缓存）
-- ============================================================

CREATE TABLE IF NOT EXISTS dictionary_value_cache (
    id BIGSERIAL PRIMARY KEY,
    attribute_id BIGINT NOT NULL,
    description_category_id BIGINT NOT NULL,
    type_id BIGINT,
    language TEXT DEFAULT 'ZH_HANS',
    values_data JSONB,
    expires_at BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 创建复合索引（加速attribute+category+type+language查询）
CREATE INDEX IF NOT EXISTS idx_dictionary_value_cache_composite 
ON dictionary_value_cache(attribute_id, description_category_id, type_id, language);

-- 创建过期时间索引（加速缓存清理）
CREATE INDEX IF NOT EXISTS idx_dictionary_value_cache_expires_at 
ON dictionary_value_cache(expires_at);

-- ============================================================
-- Step 4: 配置RLS权限策略（推荐：禁用RLS）
-- ============================================================

-- 方案1：禁用RLS（如果不需要权限控制，推荐使用）
ALTER TABLE gateway_tasks DISABLE ROW LEVEL SECURITY;
ALTER TABLE attribute_cache DISABLE ROW LEVEL SECURITY;
ALTER TABLE dictionary_value_cache DISABLE ROW LEVEL SECURITY;

-- ============================================================
-- 完成！现在可以验证表创建成功
-- ============================================================

-- 验证方法1：使用Supabase Table Editor查看表列表
-- 验证方法2：使用curl命令查询表（返回200表示成功）
-- curl -X GET "https://kekmppsuiiokdckdeolv.supabase.co/rest/v1/gateway_tasks?limit=1" \
--   -H "apikey: YOUR_API_KEY" \
--   -H "Authorization: Bearer YOUR_API_KEY"