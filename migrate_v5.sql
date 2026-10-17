-- =============================================================
-- 每日热点网站 —— 数据库结构 v5（Telegram 推送绑定）
-- 在 users 表新增 tg_chat_id 列，用于存储用户的 Telegram chat_id。
-- 对已有数据库：直接执行本文件即可，不丢数据。
-- =============================================================

USE daily_hot;

-- MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS，用 information_schema 判断
SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = 'daily_hot' AND table_name = 'users'
      AND column_name = 'tg_chat_id'
);
SET @ddl := IF(@col_exists = 0,
    "ALTER TABLE users ADD COLUMN tg_chat_id VARCHAR(64) DEFAULT NULL COMMENT 'Telegram chat_id（绑定后每日推送洞察）' AFTER password_hash",
    'SELECT ''tg_chat_id already exists''');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
