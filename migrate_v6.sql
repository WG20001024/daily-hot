-- =============================================================
-- 每日热点网站 —— 数据库结构 v6（站点设置）
-- 新增 site_settings 键值表，用于网页端配置 Telegram token / 频道 ID 等。
-- 对已有数据库：直接执行本文件即可，不丢数据。
-- =============================================================

USE daily_hot;

CREATE TABLE IF NOT EXISTS site_settings (
    k           VARCHAR(64)  NOT NULL COMMENT '配置键',
    v           TEXT         DEFAULT NULL COMMENT '配置值',
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (k)
) ENGINE=InnoDB COMMENT='站点设置（网页可配置项）';
