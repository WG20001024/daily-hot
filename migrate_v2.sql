-- =============================================================
-- 每日热点网站 —— 数据库结构 v2（新增新闻详情表）
-- 在 v1 基础上增加 hot_item_details，用于保存每条热点的详细信息。
-- 对已有数据库：直接执行本文件即可（CREATE TABLE IF NOT EXISTS，不丢数据）。
-- =============================================================

USE daily_hot;

CREATE TABLE IF NOT EXISTS hot_item_details (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    item_id      BIGINT NOT NULL COMMENT '关联 hot_items.id',
    content      MEDIUMTEXT COMMENT '正文摘要（抓取的详情内容）',
    author       VARCHAR(128) DEFAULT NULL COMMENT '作者/来源账号',
    published_at DATETIME DEFAULT NULL COMMENT '发布时间（源站）',
    cover_url    VARCHAR(1024) DEFAULT NULL COMMENT '封面图',
    source_site  VARCHAR(128) DEFAULT NULL COMMENT '来源站点',
    fetch_status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/success/failed',
    fetched_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '抓取时间',
    UNIQUE KEY uk_item (item_id),
    CONSTRAINT fk_details_item FOREIGN KEY (item_id) REFERENCES hot_items(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='热点详细信息';
