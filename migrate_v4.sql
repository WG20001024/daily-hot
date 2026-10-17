-- ============================================================
-- v4 迁移：全文检索（RSS / 全文检索 / 数据导出 中的检索部分）
-- 针对已有的 daily_hot 库增量执行，可重复执行（幂等）
-- 用法： mysql -u root -p daily_hot < migrate_v4.sql
-- ============================================================
USE daily_hot;

-- 说明：MySQL 8.0.46 自带 ngram 全文解析器（ngram_token_size 默认 2），
--       适合中文按双字切分建索引；默认解析器对中文是按标点/空格切词，几乎不可用。

-- ---------- 1) hot_items：标题 + 摘要 的 ngram 全文索引 ----------
SET @idx_exists := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'hot_items'
      AND index_name = 'ft_hot_items'
);
SET @ddl := IF(@idx_exists = 0,
    'ALTER TABLE hot_items ADD FULLTEXT INDEX ft_hot_items (title, summary) WITH PARSER ngram',
    'SELECT "ft_hot_items 已存在，跳过" AS skip');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------- 2) hot_item_details：正文内容 的 ngram 全文索引 ----------
SET @idx_exists2 := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'hot_item_details'
      AND index_name = 'ft_detail_content'
);
SET @ddl := IF(@idx_exists2 = 0,
    'ALTER TABLE hot_item_details ADD FULLTEXT INDEX ft_detail_content (content) WITH PARSER ngram',
    'SELECT "ft_detail_content 已存在，跳过" AS skip');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------- 3) 搜索辅助索引（按日期/平台过滤时加速） ----------
SET @idx_date := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'hot_items'
      AND index_name = 'idx_hot_items_date'
);
SET @ddl := IF(@idx_date = 0,
    'ALTER TABLE hot_items ADD INDEX idx_hot_items_date (snapshot_date)',
    'SELECT "idx_hot_items_date 已存在，跳过" AS skip');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------- 校验 ----------
SELECT table_name, index_name, index_type
FROM information_schema.statistics
WHERE table_schema = DATABASE()
  AND index_type = 'FULLTEXT'
GROUP BY table_name, index_name, index_type;
