# -*- coding: utf-8 -*-
"""
每日热点网站 —— 配置文件
所有配置均可通过环境变量覆盖，便于在不同环境部署。
"""
import os


class Config:
    # ---------- MySQL 连接（默认指向本目录部署的 MySQL） ----------
    DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
    DB_PORT = int(os.environ.get('DB_PORT', 3306))
    DB_USER = os.environ.get('DB_USER', 'root')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '123456')
    DB_NAME = os.environ.get('DB_NAME', 'daily_hot')
    DB_CHARSET = 'utf8mb4'

    # ---------- 抓取策略 ----------
    # 每日定时抓取时间（默认每天 08:00）
    CRAWL_HOUR = int(os.environ.get('CRAWL_HOUR', 8))
    CRAWL_MINUTE = int(os.environ.get('CRAWL_MINUTE', 0))
    REQUEST_TIMEOUT = int(os.environ.get('REQUEST_TIMEOUT', 15))      # 单源请求超时(秒)
    MAX_ITEMS_PER_PLATFORM = int(os.environ.get('MAX_ITEMS_PER_PLATFORM', 30))

    # ---------- 新闻详情抓取 ----------
    DETAIL_ENABLED = os.environ.get('DETAIL_ENABLED', '1') == '1'     # 是否抓取详情
    DETAIL_TOP_N = int(os.environ.get('DETAIL_TOP_N', 10))            # 每平台抓取前 N 条详情
    DETAIL_CONCURRENCY = int(os.environ.get('DETAIL_CONCURRENCY', 8)) # 并发数
    DETAIL_TIMEOUT = int(os.environ.get('DETAIL_TIMEOUT', 10))        # 单条详情超时(秒)
    DETAIL_MAX_CHARS = int(os.environ.get('DETAIL_MAX_CHARS', 2000))  # 正文摘要最大长度

    # ---------- Web 服务 ----------
    # 监听地址：
    #   '::'        同时监听 IPv6 + IPv4（双栈，手机可用 IPv6 直连）
    #   '0.0.0.0'   仅 IPv4（局域网手机通过 192.168.x.x 访问）
    #   '127.0.0.1' 仅本机
    HOST = os.environ.get('HOST', '::')
    PORT = int(os.environ.get('PORT', 5000))
    SITE_NAME = os.environ.get('SITE_NAME', '每日热点')
    # 对外可访问的基础地址（RSS/导出里的绝对链接）。
    # 手机通过 IPv6 访问时需填写实际地址，例如：
    #   http://[2408:8256:...]:5000
    # IPv6 字面量必须用方括号包裹，且不含端口时也要带括号。
    SITE_URL = os.environ.get('SITE_URL', '').rstrip('/')

    # ---------- RSS / 检索 / 导出 ----------
    RSS_LIMIT = int(os.environ.get('RSS_LIMIT', 50))          # RSS 默认条数
    RSS_MAX_LIMIT = int(os.environ.get('RSS_MAX_LIMIT', 200))  # RSS 条数上限
    SEARCH_LIMIT = int(os.environ.get('SEARCH_LIMIT', 50))     # 检索结果条数
    EXPORT_MAX_ROWS = int(os.environ.get('EXPORT_MAX_ROWS', 5000))  # 单导出条数上限

    # ---------- 智能分析（AI 综述，可选） ----------
    # 配置后调用 OpenAI 兼容接口生成每日综述；留空则使用本地启发式模板。
    LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL = os.environ.get('LLM_MODEL', 'gpt-4o-mini')

    # ---------- Telegram 推送（可选） ----------
    # 在 @BotFather 创建机器人获取 token；TG_CHAT_ID 为全局广播群/频道 ID
    # （以负号开头表示群组），留空则仅向已绑定个人 chat_id 的用户推送。
    TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
    TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')

    # ---------- 邮件推送（可选，QQ 邮箱用授权码） ----------
    # QQ 邮箱：SMTP_HOST=smtp.qq.com, SMTP_PORT=465(SSL), SMTP_USER=你的QQ号@qq.com
    # SMTP_PASSWORD 填「授权码」而非 QQ 登录密码（在 QQ 邮箱设置中开启 SMTP 服务获取）。
    SMTP_HOST = os.environ.get('SMTP_HOST', '')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 465))
    SMTP_USER = os.environ.get('SMTP_USER', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')  # 授权码
    SMTP_SENDER = os.environ.get('SMTP_SENDER', '')      # 留空则用 SMTP_USER
