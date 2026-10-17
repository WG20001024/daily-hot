# 每日热点（Daily Hot）

基于 **Python + Flask + MySQL** 的全平台热榜聚合网站。每日自动抓取各平台热榜，归档到 MySQL，网页按平台分组展示，支持搜索、日期回溯与手动刷新。

## 快速开始

```bash
# 1. 安装依赖（已在本机 venv 中装好）
pip install -r requirements.txt

# 2. 初始化数据库（首次）
mysql -h 127.0.0.1 -u root -p < schema.sql

# 已有旧库升级到用户体系（v3）：
mysql -h 127.0.0.1 -u root -p daily_hot < migrate_v3.sql

# 已有旧库启用中文全文检索（v4，ngram 全文索引）：
mysql -h 127.0.0.1 -u root -p daily_hot < migrate_v4.sql

# 已有旧库启用 Telegram 推送与站点设置（v5 + v6）：
mysql -h 127.0.0.1 -u root -p daily_hot < migrate_v5.sql
mysql -h 127.0.0.1 -u root -p daily_hot < migrate_v6.sql

# 3. 启动（库为空时自动后台抓取一次）
python app.py
```

启动后访问 **http://127.0.0.1:5000**

## Docker 部署（推荐）

一条命令拉起 **Web + MySQL 8**，自动建库建表，不用手工执行 `schema.sql`。

```bash
# 可选：按需修改配置（端口 / 密码 / 推送等）
cp .env.example .env

# 构建并后台启动
docker compose up -d --build

# 看日志（首次会自动建表 + 后台抓取一次）
docker compose logs -f web

# 停止（数据保留在 mysql-data 卷里）
docker compose down
```

启动后访问 **http://localhost:5000**（改端口用 `WEB_PORT`）。

> Windows 用户也可以直接双击 **`start-docker.bat`**：菜单式操作（启动 / 停止 / 日志 / 重建 / 状态），
> 会自动检测 Docker 是否安装并运行，并在启动前检查端口是否被占用。

**⚠️ 端口冲突**：本机若已用 `python app.py` 启动过站点，5000 端口已被占用，docker 会启动失败。
二选一：停掉本机站点，或在 `.env` 里改 `WEB_PORT=5001`（容器内端口始终是 5000）。

**只用镜像、数据库用外部 MySQL 时**：

```bash
docker build -t daily-hot:latest .
docker run -d --name daily-hot-web -p 5000:5000 \
  -e DB_HOST=host.docker.internal -e DB_USER=root \
  -e DB_PASSWORD=你的密码 -e DB_NAME=daily_hot \
  -e SITE_URL=http://localhost:5000 \
  daily-hot:latest
```

**容器启动时做了什么**（`docker-entrypoint.sh` → `init_db.py`）：

1. 轮询等待 MySQL 就绪（默认最长 90 秒，`DB_WAIT_TIMEOUT` 可调）
2. 创建数据库（不存在时）
3. 空库执行 `schema.sql` 建表并写入 10 个平台种子数据；**已有表时直接跳过，绝不 DROP 现有数据**
4. 依次执行 `migrate_v2` ~ `migrate_v6` 增量迁移（全部幂等，可重复执行）
5. 启动 Web 服务；库为空时后台自动抓取一次

**注意**

- `schema.sql` 与迁移文件里硬编码的 `USE daily_hot;` 会被初始化脚本忽略，实际库名以 `DB_NAME` 为准，避免误操作其他库
- 数据库由外部托管、不想让容器改表结构时：设置 `SKIP_DB_INIT=1`
- 生产建议 `SERVER=gunicorn`；gunicorn 固定 1 worker，避免定时任务重复抓取
- 建议设置 `SECRET_KEY`，否则容器重建后登录态会失效
- 国内构建加速：`docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t daily-hot:latest .`

## 功能

- **多平台聚合**：微博、知乎、百度热搜、B站、抖音、今日头条、GitHub、V2EX、IT之家、少数派
- **新闻详情入库**：每条热点自动抓取详情页，将正文摘要、作者、发布时间、封面、来源站点保存到 MySQL 的 `hot_item_details` 表（每平台前 10 条，后台并发抓取）
- **详情页**：点击列表页 📄 图标查看已保存的详细信息（`/item/<id>`），无详情时优雅降级并引导查看原文
- **每日定时抓取**：默认每天 08:00（APScheduler，可用 `CRAWL_HOUR`/`CRAWL_MINUTE` 环境变量调整）
- **历史归档**：每日快照入库，支持按日期回溯
- **手动刷新**：页面右上角「立即抓取」即时触发
- **搜索与筛选**：平台 Tab 筛选 + 关键词即时搜索
- **热度可视化**：平台内热度条 + 原值展示（万/亿自动格式化）
- **离线降级**：所有在线数据源不可达时自动使用内置演示数据（页面有明显标识），站点永不为空
- **用户系统**：注册 / 登录 / 退出（werkzeug 密码哈希 + Flask session，密钥持久化到 `.secret_key`）
- **收藏夹**：热榜、详情页均可 ★ 收藏，个人中心「我的收藏」集中管理（未登录点击引导登录）
- **个性化日报**：订阅平台 + 关键词，个人中心「我的日报」生成「只读你关心的」日报（订阅平台 Top5 + 关键词命中 + 最近收藏）
- **个人中心**：`/me` 用标签页把「我的日报 / 我的收藏 / 我的 Telegram / 邮件推送 / 站点设置」聚合到一处，顶部汇总收藏数、订阅数与绑定状态
- **邮件推送（QQ 邮箱等）**：在个人中心绑定收件邮箱并配置 SMTP（QQ 邮箱用授权码），每日抓取完成自动发送 HTML 格式的智能洞察日报
- **暗黑模式**：导航栏一键切换，选择记忆到 `localStorage`；首次访问跟随系统 `prefers-color-scheme`，全程无闪烁（`<head>` 内联脚本提前应用主题）
- **PWA 可安装**：含 `manifest.json` 与 Service Worker，可添加到手机主屏独立启动，断网仍可浏览已访问过的页面
- **热度趋势**：`/trends` 展示近 7/14/30 天持续在榜的话题，SVG 折线图 + 涨跌幅（涨红跌绿）
- **跨平台共振**：`/resonance` 与首页「全网热议」条展示同日登上多个平台热榜的话题（标题归一化匹配）
- **RSS 订阅**：`/rss.xml` 输出 RSS 2.0，支持平台/日期/条数过滤，可直接粘贴到 Feedly 等阅读器；`/feed` 提供服务端 HTML 预览，保证任意浏览器都能友好阅读
- **全文检索**：`/search` 基于 MySQL ngram 全文索引（中文双字分词），标题/摘要/正文三路检索、相关度排序、同话题跨日期聚合
- **数据导出**：`/export` 一键导出 CSV（UTF-8 BOM，Excel 直接打开）或 JSON，支持日期/平台/关键词/正文过滤
- **智能洞察**：`/insight` 聚合 AI 综述 + 情绪分析 + 话题生命周期，自动识别正面/负面热点、上升/爆发/衰退话题
- **Telegram 推送**：用户在个人中心「我的 Telegram」绑定个人 chat_id 后，每日抓取完成自动把智能洞察推送到私聊；站点可在「站点设置」配置全局频道 `TG_CHAT_ID` 做广播；`/insight` 提供一键推送按钮

## RSS 浏览器展示

- `/rss.xml` 返回标准 RSS 2.0 XML，并附带 `<?xml-stylesheet?>` XSLT 样式声明；支持 XSLT 的现代浏览器会直接渲染为带徽章、标题、条目卡片的可读页面。
- 为兼容无头浏览器、安全模式或是不支持客户端 XSLT 的环境，额外提供 `/feed`：服务端渲染的 HTML 预览页，与 `/rss.xml` 同参数（`platform` / `date` / `limit`），顶部内置「复制 /rss.xml 链接到 RSS 阅读器」按钮。
- RSS 阅读器只识别 `/rss.xml`，`/feed` 对订阅无效，仅供浏览器查看。

## 页面一览

| 路径 | 说明 |
|---|---|
| `/` | 今日热榜（平台分组、收藏按钮、全网热议条、演示数据提示） |
| `/item/<id>` | 热点详情页（正文、收藏、热度走势折线图） |
| `/trends?days=7` | 热度趋势（持续在榜话题 + SVG 走势） |
| `/resonance?date=` | 全网热议（跨平台共振话题） |
| `/me` | **个人中心**（标签页聚合：我的日报 / 我的收藏 / 我的 Telegram / 邮件推送 / 站点设置，需登录，`?tab=` 切换） |
| `/insight?date=&days=` | 智能洞察（AI 综述、情绪分布、话题生命周期） |
| `/history` | 历史归档（按日期回溯） |
| `/register` · `/login` · `/logout` | 用户注册 / 登录 / 退出 |
| `/digest` · `/favorites` · `/settings` | 旧入口，已 302 重定向到 `/me` 对应标签 |
| `/search?q=` | 全文检索（`full=1` 同时搜正文，按相关度排序） |
| `/export` | 数据导出 + RSS / API 速查 |
| `/rss.xml` | RSS 2.0 订阅源（`?platform=&date=&limit=`） |
| `/feed` | RSS 的 HTML 预览页（浏览器友好，同参数过滤） |

## 目录结构

```
daily-hot/
├── app.py               # Flask 入口：页面路由 + JSON API + 定时任务
├── crawler.py           # 抓取器：3 个在线数据源 + 离线演示数据 + 归一化入库
├── detail_fetcher.py    # 新闻详情抓取器：解析详情页，提取正文/作者/时间/封面
├── db.py                # 数据访问层（PyMySQL）：热点/详情/用户/收藏/订阅/趋势/共振/洞察
├── sentiment.py         # 中文情绪分析词典（零依赖，离线可用）
├── ai_summary.py        # AI 综述：OpenAI 兼容接口 + 启发式回退
├── telegram_push.py     # Telegram 推送（Bot API，零依赖，含优雅降级）
├── mail_push.py         # 邮件推送（SMTP，零依赖，HTML 日报邮件）
├── config.py            # 配置（全部可用环境变量覆盖）
├── schema.sql           # 建库建表 SQL（全新安装，含 v3 用户体系 + v4 全文索引 + site_settings）
├── migrate_v2.sql       # v2 迁移 SQL（已有库增加详情表）
├── migrate_v3.sql       # v3 迁移 SQL（users / user_favorites / user_subscriptions + title_norm）
├── migrate_v4.sql       # v4 迁移 SQL（ngram 全文索引：标题/摘要/正文）
├── migrate_v5.sql       # v5 迁移 SQL（users.tg_chat_id）
├── migrate_v6.sql       # v6 迁移 SQL（site_settings 站点设置键值表）
├── feed.py              # RSS 2.0 生成器（纯标准库）
├── init_db.py           # 数据库初始化：等待 MySQL → 建库 → 建表 → 执行增量迁移（容器入口调用）
├── gunicorn.conf.py     # gunicorn 配置（SERVER=gunicorn 时启用，1 worker + post_fork 起定时任务）
├── requirements.txt
├── Dockerfile           # 镜像：python:3.11-slim，非 root 运行，含健康检查
├── docker-compose.yml   # 编排：web + mysql:8.0（健康检查 + 数据卷）
├── docker-entrypoint.sh # 容器启动入口：先初始化数据库，再拉起 Web
├── start-docker.bat    # Windows 一键脚本：检测 Docker / 端口检查 / 启动·停止·日志·重建
├── .dockerignore        # 构建上下文排除（密钥 / 文档 / 归档页）
├── .env.example         # 部署环境变量示例
├── templates/           # Jinja2 模板（base / index / item / history / trends / resonance / me 个人中心 / insight / search / export / feed / login / register；_legacy 存放已并入 /me 的旧模板备份）
├── static/              # CSS / JS / rss.xsl（浏览器美化样式表）/ manifest.json / sw.js / PWA 图标
└── docs/                # 截图
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/hot?date=&platform=&q=` | 热点列表（平台/关键词过滤，含 has_detail 标记；登录用户含 favorited 标记） |
| GET | `/api/item/<id>` | 单条热点及其详细信息 |
| GET | `/api/dates` | 全部快照日期 |
| GET | `/api/platforms` | 平台列表 |
| GET | `/api/status` | 运行状态（最新日期、条数、下次抓取时间；登录用户含收藏数） |
| POST | `/api/refresh` | 手动触发抓取 |
| GET | `/api/logs` | 最近抓取日志 |
| POST | `/api/favorite/<id>` | 切换收藏（需登录，返回最新收藏数） |
| POST | `/api/subscriptions` | 保存订阅（需登录，平台多选 + 关键词逗号分隔） |
| GET | `/api/search?q=&date=&platform=&full=` | 全文检索（相关度排序，含正文时 `full=1`） |
| GET | `/api/export?format=json\|csv&date=&platform=&q=&full=&limit=` | 数据导出（CSV 带 BOM） |
| GET | `/rss.xml?platform=&date=&limit=` | RSS 2.0 订阅源（浏览器支持时通过 XSLT 渲染为可读页面） |
| GET | `/feed?platform=&date=&limit=` | RSS 的服务端 HTML 预览页（阅读器不识别，仅用于浏览器查看） |
| POST | `/api/tg/bind` | 绑定 / 解绑 Telegram chat_id 并发送测试消息（需登录） |
| POST | `/api/tg/send` | 把当日智能洞察推送到当前用户绑定的 Telegram（需登录） |
| POST | `/api/tg/broadcast` | 向全局频道 `TG_CHAT_ID` 广播当日洞察（需登录 + 已配置） |
| POST | `/api/tg/bind-mine` | 一键把「我的 chat_id」写入站点设置并绑定当前账号（需登录） |
| POST | `/api/tg/verify` | 校验 token + chat_id（发送测试消息，需登录） |
| POST | `/api/mail/bind` | 绑定 / 解绑收件邮箱并发送测试邮件（需登录） |
| POST | `/api/mail/send` | 把当日洞察发送到当前账号邮箱（需登录） |
| POST | `/api/mail/smtp` | 保存 SMTP 配置（QQ 邮箱授权码等），可选发测试邮件（需登录） |
| GET | `/healthz` | 健康检查 |

## 数据库设计（`daily_hot`）

- `platforms` — 平台（代码/名称/分类/排序）
- `hot_items` — 热点条目，按 `snapshot_date` 归档，唯一键 `(platform_id, snapshot_date, rank_no)` 保证同日重复抓取幂等覆盖；`title_norm` 列（标题归一化：去空白/标点/小写）用于跨日期趋势与跨平台共振匹配
- `hot_item_details` — **新闻详细信息**：正文摘要（MEDIUMTEXT）、作者、发布时间、封面图、来源站点、抓取状态，与 hot_items 一对一关联
- `users` — 用户（用户名/邮箱唯一，werkzeug 密码哈希）
- `user_favorites` — 收藏（user_id + item_id 唯一，可附备注）
- `user_subscriptions` — 订阅（sub_type: platform/keyword）
- `crawl_logs` — 抓取日志（数据源、状态、条数、耗时、错误信息）
- `site_settings` — 站点设置键值表（网页可配置 Telegram token / 频道 ID 等，优先于环境变量）

## 详情抓取说明

- 抓取策略：每条热点的链接 → 请求详情页 → 解析 `og:`/`article:` 等 meta 与 `<p>` 段落 → 保存元信息和摘要片段（聚合器惯例，不镜像全文）
- 限量并发：每平台前 `DETAIL_TOP_N` 条、`DETAIL_CONCURRENCY` 个并发、单条 `DETAIL_TIMEOUT` 秒超时，失败不影响其他条目
- 已入库的详情不会重复抓取；抓取在后台线程进行，不阻塞页面与 API
- 离线演示数据自带前 5 条/平台的详情内容，完整演示详情页效果

## 全文检索说明

- 索引：`hot_items(title, summary)` 与 `hot_item_details(content)` 上的 **ngram 全文索引**（`WITH PARSER ngram`），中文按双字切分（`ngram_token_size=2`），英文/数字同样可用
- 匹配：BOOLEAN MODE（无 50% 阈值问题），查询词自动清洗（去掉 `+ - > < ( ) ~ * " @` 等操作符字符）
- 聚合：同一话题（`title_norm` 相同）跨日期/平台命中会合并为一条，展示最新状态并标注「N 天在榜」；正文命中优先展示正文摘要
- 降级：数据库未建全文索引时自动退回 `LIKE` 模糊匹配，页面会提示「未启用全文索引」；执行 `migrate_v4.sql` 即可启用

## 智能洞察说明

- **情绪分析**：基于内置中文正负面词典对标题+摘要离线打分，归为 正面 / 中性 / 负面，并展示命中词（零依赖、无需 API）。
- **话题生命周期**：对 `trending_topics` 的跨日热度序列做阶段判断，分为 爆发 / 上升 / 衰退 / 平稳 / 新晋。
- **AI 综述**：优先调用 OpenAI 兼容接口（`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 环境变量）；未配置或网络不可达时，自动回退到启发式模板，保证沙箱/离线环境也能输出可读综述。
- 所有能力集中在 `/insight` 页面，也便于后续扩展为邮件日报正文来源。

## Telegram 推送说明

- 先在 [@BotFather](https://t.me/BotFather) 发 `/newbot` 创建机器人拿到 `TG_BOT_TOKEN`；**获取 chat_id 的可靠方式**：给自己的机器人发一条消息后打开 `https://api.telegram.org/bot<TOKEN>/getUpdates`，在 JSON 中找 `"from":{"id":...}`（个人）或 `"chat":{"id":-100...}`（群组/频道）。
  - 注意：`@userinfobot` 现已改为 AI 聊天机器人，不再返回用户 ID，不建议使用。
- **配置入口（均在个人中心 `/me` 内）**：
  - 「我的 Telegram」标签：填 chat_id 点「保存并绑定」，同时写入站点设置与当前账号，每日自动推送；同标签内还有 **chat_id 校验工具**与获取说明。
  - 「站点设置」标签：填站点级 `TG_BOT_TOKEN` / `TG_CHAT_ID`（存入 `site_settings`，优先于环境变量）。
  - 环境变量：启动前设置 `TG_BOT_TOKEN` / `TG_CHAT_ID`（网页未配置时回退读取）。
- 用户绑定后，**每日定时抓取完成会自动推送**当日智能洞察到其 Telegram 私聊（`push_all_users`，见 `app.py` 的 `daily_job`）。
- 站点级：`TG_CHAT_ID` 配置为群组/频道 ID，可在 `/insight` 点「推送到 Telegram」一键广播；未配置则只推送给已绑定个人。
- 实现：`telegram_push.py` 用标准库 urllib 调 Bot API（`sendMessage`，HTML 排版），**零额外依赖**；未配置 token 或网络不可达时函数返回 `(ok, error)`，由页面提示，不影响抓取主流程。

## 暗黑模式与 PWA

**暗黑模式**

- 全部颜色统一走 CSS 变量（`--bg` / `--card` / `--text` / `--primary` 等 30+ 个），深色主题只需覆盖 `html[data-theme="dark"]` 下的变量值，无需重复写规则。
- `<head>` 内联脚本在首屏渲染前应用主题，避免刷新闪烁；优先级：`localStorage` 用户选择 > 系统 `prefers-color-scheme`。
- 导航栏 🌙/☀️ 按钮切换，系统主题变化时若用户未手动选择过会自动跟随。

**PWA**

- `static/manifest.json`（站点名、图标、主题色、快捷方式）+ `static/sw.js`（离线缓存）即可安装到手机主屏。
- 缓存策略：静态资源**缓存优先 + 后台更新**；页面导航**网络优先 + 失败回落缓存**（离线可看历史页面）；`/api/*` 与 `/rss.xml` **不缓存**保证数据新鲜。
- 修改 `sw.js` 中的 `VERSION` 常量即可让所有客户端清缓存升级。
- 图标由 `_gen_icons.py` 生成（零依赖，手写 PNG 编码），改动配色后重跑即可：
  ```bash
  python _gen_icons.py
  ```
- **部署注意**：Service Worker 要求 HTTPS（`localhost`/`127.0.0.1` 调试时浏览器会放行），公网部署需为域名配置证书。

## 局域网 / IPv6 手机访问

服务默认以 **IPv6 + IPv4 双栈**监听（`HOST='::'`），手机与电脑在同一网络即可直接访问。

**启动时会打印手机访问地址**，形如：

```
每日热点网站启动: http://:::5000 (IPv6 + IPv4 双栈)
手机访问地址: http://[2408:8256:3884:2871:78c1:8cdb:404c:3a2c]:5000
```

在手机浏览器输入该地址即可（IPv6 字面量**必须带方括号**，且方括号是地址的一部分）。

**查看本机 IPv6 地址**

```powershell
Get-NetIPAddress -AddressFamily IPv6 | Where-Object { $_.IPAddress -notlike 'fe80*' -and $_.IPAddress -notlike '::1' }
```

`2408:` / `2409:` 开头的是运营商分配的**公网 IPv6**，手机在外网也能直连；`fe80:` 开头的是链路本地地址，无法用于访问。

**首次配置需放行防火墙**（管理员 PowerShell）：

```powershell
New-NetFirewallRule -DisplayName "每日热点 Flask 5000" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 5000 -Profile Private
```

**注意事项**

- IPv6 地址通常是**动态分配**的，重启或换网络后可能变化，需重新确认并更新 `SITE_URL`。
- 服务未检测到 IPv6 支持时会自动回退 `0.0.0.0`，此时改用 IPv4 局域网地址 `http://192.168.x.x:5000` 访问。
- `SITE_URL` 影响 Telegram 推送里的「查看完整洞察」链接，写成 IPv6 时务必带方括号。

## 邮件推送说明（QQ 邮箱 / 163 / Gmail 等）

个人中心「✉️ 邮件推送」标签完成全部配置，无需改代码、无需环境变量。

**QQ 邮箱配置步骤**

1. 浏览器登录 [mail.qq.com](https://mail.qq.com)
2. 「设置 → 账户与安全 → 账户」→ 找到 **POP3/SMTP 服务**，点「开启」，按提示用手机短信验证
3. 开启后显示一串 16 位**授权码**，复制它（这就是密码，不是你的 QQ 密码）
4. 回到网站「邮件推送」标签填写：

| 字段 | 填写内容 |
|---|---|
| SMTP 服务器 | `smtp.qq.com` |
| 端口 | `465` |
| 发信账号 | 你的 QQ 邮箱，如 `123456@qq.com` |
| 授权码 | 上一步复制的 16 位码 |
| 使用 SSL | 勾选（465 端口必选） |

5. 点「保存并发测试邮件」，收到即配置成功

**其他邮箱**

- **163**：服务器 `smtp.163.com`，端口 465，授权码在 163 邮箱设置中开启 SMTP 后获取
- **Gmail**：服务器 `smtp.gmail.com`，端口 587，**关闭 SSL 改用 STARTTLS**，密码填「应用专用密码」（需开启两步验证）

**实现要点**

- `mail_push.py` 使用标准库 `smtplib` + `email`，**零第三方依赖**；SSL 走 `SMTP_SSL`，非 SSL 走 `SMTP.starttls()`
- 认证失败会明确提示「请确认使用授权码而非 QQ 密码」，避免用户反复排查
- SMTP 配置存 `site_settings`（键前缀 `SMTP_`），收件邮箱存 `users.email`
- 每日定时抓取完成后自动向所有已填邮箱的用户发送，失败仅记录日志、不影响抓取主流程

## 数据导出说明

- CSV：UTF-8 **带 BOM**，Excel 双击打开中文不乱码；含正文时追加作者/发布时间/来源站点/正文列
- JSON：`columns` 元数据 + `items` 数据，便于程序消费
- 上限：单次 `EXPORT_MAX_ROWS`（默认 5000）条，超出可按平台/关键词分批导出

## 抓取数据源

依次尝试，任一成功即停止：`vvhan`（多平台聚合）→ `tenapi`（按平台，并发请求）→ `60s`。全部失败时降级为 `offline-demo` 演示数据，并在 `crawl_logs` 中记录 `status='mock'`，页面顶部显示提示条。

> 本机沙箱网络无法访问上述热榜 API，因此当前展示的是演示数据；在正常网络环境下启动即可获得真实热榜，无需改代码。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `127.0.0.1` / `3306` / `root` / `123456` / `daily_hot` | MySQL 连接 |
| `CRAWL_HOUR` / `CRAWL_MINUTE` | `8` / `0` | 每日抓取时间 |
| `HOST` / `PORT` | `::` / `5000` | Web 监听地址。`::` = IPv6+IPv4 双栈（手机可用 IPv6 直连）；`0.0.0.0` = 仅 IPv4 局域网；`127.0.0.1` = 仅本机 |
| `REQUEST_TIMEOUT` | `15` | 单源请求超时（秒） |
| `MAX_ITEMS_PER_PLATFORM` | `30` | 每平台最多保留条数 |
| `DETAIL_ENABLED` | `1` | 是否抓取新闻详情 |
| `DETAIL_TOP_N` | `10` | 每平台抓取前 N 条详情 |
| `DETAIL_CONCURRENCY` | `8` | 详情抓取并发数 |
| `DETAIL_TIMEOUT` | `10` | 单条详情超时（秒） |
| `DETAIL_MAX_CHARS` | `2000` | 正文摘要最大长度 |
| `SITE_NAME` / `SITE_URL` | `每日热点` / （空） | 站点名与对外地址（推送链接使用）。IPv6 需写成 `http://[2408:xxx]:5000` 并带方括号；留空则推送中不附带链接 |
| `RSS_LIMIT` / `RSS_MAX_LIMIT` | `50` / `200` | RSS 默认/最大条数 |
| `SEARCH_LIMIT` | `50` | 检索结果条数 |
| `EXPORT_MAX_ROWS` | `5000` | 单次导出条数上限 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | `''` / `https://api.openai.com/v1` / `gpt-4o-mini` | 智能洞察 AI 综述调用（留空则使用启发式模板） |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | `''` / `''` | Telegram 推送（也可在网页「⚙️ 设置」填写，网页配置优先） |
| `SMTP_HOST` / `SMTP_PORT` | `''` / `465` | 邮件推送 SMTP 服务器（QQ 邮箱填 `smtp.qq.com`），网页可配，优先 |
| `SMTP_USER` / `SMTP_PASSWORD` | `''` / `''` | 发信账号与**授权码**（非 QQ 登录密码） |
| `SMTP_SENDER` | `''` | 发件人地址，留空则用 `SMTP_USER` |
| `SECRET_KEY` | （自动生成） | Flask 会话密钥；容器部署建议显式设置，否则重建后登录态失效 |
| `SERVER` | `flask` | 容器启动方式：`flask` 内置服务器 / `gunicorn` 生产模式 |
| `BIND` | `0.0.0.0:5000` | 仅 `SERVER=gunicorn` 时生效；启用 IPv6 可设为 `[::]:5000` |
| `GUNICORN_THREADS` / `GUNICORN_TIMEOUT` | `8` / `120` | gunicorn 线程数与超时（秒） |
| `SKIP_DB_INIT` | `0` | 设为 `1` 时容器跳过建库建表（数据库由外部托管） |
| `DB_WAIT_TIMEOUT` | `90` | 启动时等待 MySQL 就绪的最长秒数 |
