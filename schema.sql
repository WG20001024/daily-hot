-- =============================================================
-- 每日热点网站 —— MySQL 数据库结构
-- 数据库: daily_hot   (MySQL 8.0)
-- =============================================================

CREATE DATABASE IF NOT EXISTS daily_hot
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE daily_hot;

-- -------------------------------------------------------------
-- 重建全部表（全新安装时执行；已有数据请改用 migrate_v2.sql）
-- -------------------------------------------------------------
DROP TABLE IF EXISTS hot_item_details;
DROP TABLE IF EXISTS hot_items;
DROP TABLE IF EXISTS crawl_logs;
DROP TABLE IF EXISTS platforms;

CREATE TABLE platforms (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    code        VARCHAR(32)  NOT NULL COMMENT '平台代码: weibo/zhihu/bilibili...',
    name        VARCHAR(64)  NOT NULL COMMENT '平台显示名',
    category    VARCHAR(32)  NOT NULL DEFAULT '综合' COMMENT '分类: 社交/科技/新闻/视频/开发者',
    homepage    VARCHAR(255) DEFAULT NULL COMMENT '平台官网',
    enabled     TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否启用抓取',
    sort_order  INT          NOT NULL DEFAULT 0 COMMENT '展示排序',
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_code (code),
    KEY idx_sort (sort_order)
) ENGINE=InnoDB COMMENT='热榜平台';

-- -------------------------------------------------------------
-- 热点条目表：按快照日期归档
-- -------------------------------------------------------------
CREATE TABLE hot_items (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    platform_id   INT NOT NULL COMMENT '所属平台',
    title         VARCHAR(512) NOT NULL COMMENT '热点标题',
    url           VARCHAR(1024) DEFAULT NULL COMMENT '详情链接',
    heat_value    BIGINT NOT NULL DEFAULT 0 COMMENT '原始热度值',
    heat_display  VARCHAR(64) DEFAULT NULL COMMENT '展示用热度, 如 452.6万',
    rank_no       INT NOT NULL COMMENT '当日排名',
    summary       VARCHAR(1024) DEFAULT NULL COMMENT '摘要',
    extra         JSON DEFAULT NULL COMMENT '扩展字段(作者/标签等)',
    snapshot_date DATE NOT NULL COMMENT '快照日期',
    crawled_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '抓取时间',
    UNIQUE KEY uk_platform_date_rank (platform_id, snapshot_date, rank_no),
    KEY idx_snapshot (snapshot_date),
    KEY idx_title_prefix (title(191)),
    FULLTEXT INDEX ft_hot_items (title, summary) WITH PARSER ngram,
    CONSTRAINT fk_items_platform FOREIGN KEY (platform_id) REFERENCES platforms(id)
) ENGINE=InnoDB COMMENT='热点条目(按日归档)';

-- -------------------------------------------------------------
-- 热点详情表：保存每条热点的详细信息（正文摘要/作者/发布时间等）
-- -------------------------------------------------------------
CREATE TABLE hot_item_details (
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
    FULLTEXT INDEX ft_detail_content (content) WITH PARSER ngram,
    CONSTRAINT fk_details_item FOREIGN KEY (item_id) REFERENCES hot_items(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='热点详细信息';

-- -------------------------------------------------------------
-- 抓取日志表：记录每次抓取结果，便于排查数据源健康状况
-- -------------------------------------------------------------
CREATE TABLE crawl_logs (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    platform_code VARCHAR(32) DEFAULT NULL COMMENT '平台代码, all 表示整体',
    source       VARCHAR(64) NOT NULL COMMENT '数据源名称',
    status       VARCHAR(16) NOT NULL COMMENT 'success/failed/mock',
    item_count   INT NOT NULL DEFAULT 0 COMMENT '抓取条目数',
    message      VARCHAR(512) DEFAULT NULL COMMENT '说明或错误信息',
    duration_ms  INT DEFAULT NULL COMMENT '耗时(毫秒)',
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_created (created_at)
) ENGINE=InnoDB COMMENT='抓取日志';

-- -------------------------------------------------------------
-- 初始平台数据
-- -------------------------------------------------------------
INSERT INTO platforms (code, name, category, homepage, sort_order) VALUES
('weibo',    '微博',      '社交',    'https://weibo.com',            10),
('zhihu',    '知乎',      '社交',    'https://www.zhihu.com',        20),
('baidu',    '百度热搜',  '新闻',    'https://top.baidu.com',        30),
('bilibili', '哔哩哔哩',  '视频',    'https://www.bilibili.com',     40),
('douyin',   '抖音',      '视频',    'https://www.douyin.com',       50),
('toutiao',  '今日头条',  '新闻',    'https://www.toutiao.com',      60),
('github',   'GitHub',   '开发者',  'https://github.com/trending',  70),
('v2ex',     'V2EX',     '开发者',  'https://www.v2ex.com',         80),
('ithome',   'IT之家',   '科技',    'https://www.ithome.com',       90),
('sspai',    '少数派',    '科技',    'https://sspai.com',            100);

-- -------------------------------------------------------------
-- 站点设置（网页可配置项：Telegram token / 频道 ID 等）
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_settings (
    k           VARCHAR(64)  NOT NULL COMMENT '配置键',
    v           TEXT         DEFAULT NULL COMMENT '配置值',
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (k)
) ENGINE=InnoDB COMMENT='站点设置';
