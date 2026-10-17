-- =============================================================
-- 每日热点网站 —— 数据库结构 v3（用户系统 / 收藏 / 订阅 / 趋势匹配）
-- 在 v2 基础上新增 users、user_favorites、user_subscriptions，
-- 并为 hot_items 增加 title_norm（归一化标题，用于趋势与跨平台匹配）。
-- 对已有数据库：直接执行本文件即可，不丢数据。
-- =============================================================

USE daily_hot;

-- -------------------------------------------------------------
-- 用户表
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(32)  NOT NULL COMMENT '用户名',
    email         VARCHAR(128) DEFAULT NULL COMMENT '邮箱',
    password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希',
    tg_chat_id    VARCHAR(64) DEFAULT NULL COMMENT 'Telegram chat_id（绑定后每日推送洞察）',
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_username (username),
    UNIQUE KEY uk_email (email)
) ENGINE=InnoDB COMMENT='用户';

-- -------------------------------------------------------------
-- 收藏表
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_favorites (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    item_id    BIGINT NOT NULL,
    note       VARCHAR(255) DEFAULT NULL COMMENT '收藏备注',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_item (user_id, item_id),
    CONSTRAINT fk_fav_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_fav_item FOREIGN KEY (item_id) REFERENCES hot_items(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='用户收藏';

-- -------------------------------------------------------------
-- 订阅表：sub_type = 'platform'（订阅平台）或 'keyword'（订阅关键词）
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_subscriptions (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    sub_type   VARCHAR(16) NOT NULL COMMENT 'platform / keyword',
    value      VARCHAR(128) NOT NULL COMMENT '平台代码或关键词',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_sub (user_id, sub_type, value),
    CONSTRAINT fk_sub_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='用户订阅';

-- -------------------------------------------------------------
-- hot_items 增加归一化标题列（去空白/标点/大小写，供趋势与共振匹配）
-- -------------------------------------------------------------
-- MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS，用 information_schema 判断
SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = 'daily_hot' AND table_name = 'hot_items'
      AND column_name = 'title_norm'
);
SET @ddl := IF(@col_exists = 0,
    'ALTER TABLE hot_items ADD COLUMN title_norm VARCHAR(512) DEFAULT NULL COMMENT ''归一化标题(趋势/共振匹配用)'' AFTER title',
    'SELECT ''title_norm already exists''');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @idx_exists := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = 'daily_hot' AND table_name = 'hot_items'
      AND index_name = 'idx_title_norm'
);
SET @ddl := IF(@idx_exists = 0,
    'ALTER TABLE hot_items ADD INDEX idx_title_norm (title_norm(191))',
    'SELECT ''idx_title_norm already exists''');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
