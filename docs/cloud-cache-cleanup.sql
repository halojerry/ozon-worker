-- ═══════════════════════════════════════════════════════════════════
-- 云端学习缓存污染清理（ozon_attribute_mappings）
-- 执行人：云端运维/同事
-- 背景：旧版 bug（8229 盲补首值=「套娃」）写入的污染记录，会让该
--       类目的 8229 类型属性复用错误值 → Ozon 拒收/评分下降
-- 安全：只读查询请用【查询】段；确认后执行【清理】段
-- ═══════════════════════════════════════════════════════════════════

-- ── 查询：先看有没有污染（8229→套娃 dict 91965，或其他异常）──
SELECT id, category_id, attribute_id, attribute_name,
       source_value, target_value, dictionary_value_id, source, success_count, fail_count
FROM ozon_attribute_mappings
WHERE (attribute_id = 8229 AND dictionary_value_id = 91965)   -- 套娃错配
   OR (attribute_id = 9782 AND dictionary_value_id NOT IN (   -- 危险等级非安全值
        SELECT dictionary_value_id FROM ozon_attribute_mappings WHERE attribute_id = 9782
        AND target_value ILIKE '%не опас%'
   ))
ORDER BY id;

-- ── 清理：删除确认的污染记录（8229 套娃 + 9782 非安全值）──
-- ⚠️ 执行前先跑上面的查询确认影响行数；id 范围按实际结果调整
BEGIN;
DELETE FROM ozon_attribute_mappings
WHERE (attribute_id = 8229 AND dictionary_value_id = 91965)   -- 套娃
   OR (attribute_id = 9782 AND dictionary_value_id NOT IN (970661099, 0));  -- 非「Не опасен」
-- 核对删除行数后 COMMIT / ROLLBACK
COMMIT;

-- ── 可选：学习表其他潜在污染（old 非 approved 记录）──
-- 说明：v0.21+ 只写 source='learned_approved'，旧记录若 source 不同可失效
SELECT source, count(*) FROM ozon_attribute_mappings GROUP BY source ORDER BY count DESC;
