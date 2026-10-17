# -*- coding: utf-8 -*-
"""
每日热点网站 —— 数据访问层
基于 PyMySQL，提供平台、热点条目、抓取日志的读写操作。
每条操作独立获取连接（简单可靠），写入使用 INSERT ... ON DUPLICATE KEY UPDATE 实现幂等。
"""
import json
import re
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

import sentiment
from config import Config


def normalize_title(title):
    """归一化标题：去空白/标点、小写，用于跨日期趋势与跨平台共振匹配。"""
    if not title:
        return ''
    return re.sub(r'[\s\W_]+', '', title).lower()


@contextmanager
def get_conn():
    """获取数据库连接的上下文管理器，自动提交/回滚并关闭。"""
    conn = pymysql.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME,
        charset=Config.DB_CHARSET,
        cursorclass=DictCursor,
        autocommit=True,
    )
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------- 平台
def list_platforms(enabled_only=True):
    sql = "SELECT * FROM platforms"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY sort_order"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def get_platform_map():
    """返回 {code: platform_row}"""
    return {p['code']: p for p in list_platforms(enabled_only=False)}


# ---------------------------------------------------------------- 热点条目
def save_items(platform_code, items, snapshot_date):
    """
    保存某平台某日的热点条目（幂等：同平台同日同排名覆盖更新）。
    items: [{'title','url','heat','heat_display','rank','summary','extra'}]
    返回写入条数。
    """
    if not items:
        return 0
    platforms = get_platform_map()
    if platform_code not in platforms:
        raise ValueError(f"未知平台: {platform_code}")
    pid = platforms[platform_code]['id']

    rows = []
    for i, it in enumerate(items, start=1):
        extra = it.get('extra') or None
        rows.append((
            pid,
            it['title'],
            normalize_title(it['title']),
            it.get('url'),
            int(it.get('heat') or 0),
            it.get('heat_display'),
            i,
            it.get('summary'),
            json.dumps(extra, ensure_ascii=False) if extra else None,
            snapshot_date,
        ))

    sql = """
        INSERT INTO hot_items
            (platform_id, title, title_norm, url, heat_value, heat_display, rank_no,
             summary, extra, snapshot_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            title = VALUES(title), title_norm = VALUES(title_norm),
            url = VALUES(url), heat_value = VALUES(heat_value),
            heat_display = VALUES(heat_display), summary = VALUES(summary),
            extra = VALUES(extra), crawled_at = CURRENT_TIMESTAMP
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    return len(rows)


def save_items_with_details(platform_code, items, snapshot_date):
    """
    保存热点条目，并一并保存其中自带的详细信息（detail 字段，用于演示数据）。
    详情按 (平台, 日期, 排名) 关联到 hot_items.id。
    """
    saved = save_items(platform_code, items, snapshot_date)
    # 查出该平台该日的 id <-> 排名映射
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT h.id, h.rank_no FROM hot_items h
                JOIN platforms p ON p.id = h.platform_id
                WHERE p.code = %s AND h.snapshot_date = %s
            """, (platform_code, snapshot_date))
            rank_to_id = {r['rank_no']: r['id'] for r in cur.fetchall()}
    detail_count = 0
    for i, it in enumerate(items, start=1):
        detail = it.get('detail')
        item_id = rank_to_id.get(i)
        if detail and item_id:
            save_detail(item_id, detail, status='success')
            detail_count += 1
    return saved, detail_count


# ---------------------------------------------------------------- 热点详情
def backfill_title_norm():
    """为历史数据回填 title_norm（迁移后执行一次）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title FROM hot_items WHERE title_norm IS NULL OR title_norm = ''")
            rows = cur.fetchall()
            for r in rows:
                cur.execute("UPDATE hot_items SET title_norm = %s WHERE id = %s",
                            (normalize_title(r['title']), r['id']))
    return len(rows)


def save_detail(item_id, detail, status='success'):
    """保存/更新某热点的详细信息（幂等）。detail 为 dict，或直接为正文文本。"""
    if isinstance(detail, str):
        detail = {'content': detail}
    detail = detail or {}
    sql = """
        INSERT INTO hot_item_details
            (item_id, content, author, published_at, cover_url, source_site, fetch_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            content = VALUES(content), author = VALUES(author),
            published_at = VALUES(published_at), cover_url = VALUES(cover_url),
            source_site = VALUES(source_site), fetch_status = VALUES(fetch_status),
            fetched_at = CURRENT_TIMESTAMP
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                item_id,
                detail.get('content'),
                detail.get('author'),
                detail.get('published_at'),
                detail.get('cover_url'),
                detail.get('source_site'),
                status,
            ))


def get_detail(item_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.*, h.title, h.url, h.snapshot_date, h.rank_no,
                       p.name AS platform_name, p.code AS platform_code
                FROM hot_item_details d
                JOIN hot_items h ON h.id = d.item_id
                JOIN platforms p ON p.id = h.platform_id
                WHERE d.item_id = %s
            """, (item_id,))
            row = cur.fetchone()
    if row and row.get('published_at'):
        row['published_at'] = str(row['published_at'])
    if row and row.get('fetched_at'):
        row['fetched_at'] = str(row['fetched_at'])
    return row


def get_item(item_id):
    """查询单条热点（含平台信息）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT h.*, p.name AS platform_name, p.code AS platform_code,
                       p.category, p.homepage
                FROM hot_items h
                JOIN platforms p ON p.id = h.platform_id
                WHERE h.id = %s
            """, (item_id,))
            row = cur.fetchone()
    if row and row.get('extra'):
        try:
            row['extra'] = json.loads(row['extra'])
        except (ValueError, TypeError):
            row['extra'] = None
    return row


def items_missing_detail(date, per_platform=10):
    """
    查询某日还没有成功详情的条目（每个平台取排名前 per_platform 条），
    返回 [{'id','title','url','platform_code','platform_name'}]。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT h.id, h.title, h.url, h.rank_no,
                       p.code AS platform_code, p.name AS platform_name
                FROM hot_items h
                JOIN platforms p ON p.id = h.platform_id
                LEFT JOIN hot_item_details d ON d.item_id = h.id
                    AND d.fetch_status = 'success'
                WHERE h.snapshot_date = %s AND d.id IS NULL AND h.url IS NOT NULL
                ORDER BY p.sort_order, h.rank_no
            """, (date,))
            rows = cur.fetchall()
    # 每平台限量
    seen = {}
    out = []
    for r in rows:
        c = seen.get(r['platform_code'], 0)
        if c < per_platform:
            seen[r['platform_code']] = c + 1
            out.append(r)
    return out


def query_items(date=None, platform_code=None, keyword=None, limit=500):
    """按条件查询热点条目，按平台、排名排序。"""
    sql = """
        SELECT h.id, h.title, h.url, h.heat_value, h.heat_display, h.rank_no,
               h.summary, h.extra, h.snapshot_date, h.crawled_at,
               p.code AS platform_code, p.name AS platform_name,
               p.category, p.homepage,
               CASE WHEN d.id IS NULL THEN 0 ELSE 1 END AS has_detail
        FROM hot_items h
        JOIN platforms p ON p.id = h.platform_id
        LEFT JOIN hot_item_details d ON d.item_id = h.id
            AND d.fetch_status = 'success'
        WHERE 1 = 1
    """
    params = []
    if date:
        sql += " AND h.snapshot_date = %s"
        params.append(date)
    if platform_code:
        sql += " AND p.code = %s"
        params.append(platform_code)
    if keyword:
        sql += " AND h.title LIKE %s"
        params.append(f"%{keyword}%")
    sql += " ORDER BY p.sort_order, h.rank_no LIMIT %s"
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    for r in rows:
        if r.get('extra'):
            try:
                r['extra'] = json.loads(r['extra'])
            except (ValueError, TypeError):
                r['extra'] = None
    return rows


def get_latest_date():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(snapshot_date) AS d FROM hot_items")
            row = cur.fetchone()
            return row['d'] if row else None


def list_dates(limit=30):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT snapshot_date, COUNT(*) AS item_count
                FROM hot_items GROUP BY snapshot_date
                ORDER BY snapshot_date DESC LIMIT %s
            """, (limit,))
            return cur.fetchall()


def count_today(date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM hot_items WHERE snapshot_date = %s",
                (date,))
            return cur.fetchone()['c']


# ---------------------------------------------------------------- 抓取日志
def write_log(platform_code, source, status, item_count=0, message=None, duration_ms=None):
    sql = """
        INSERT INTO crawl_logs (platform_code, source, status, item_count, message, duration_ms)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (platform_code, source, status, item_count, message, duration_ms))


def recent_logs(limit=20):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM crawl_logs ORDER BY id DESC LIMIT %s
            """, (limit,))
            return cur.fetchall()


# ================================================================ 用户
def create_user(username, password, email=None):
    """注册用户，返回 user_id；用户名/邮箱已存在时抛出 ValueError。"""
    from werkzeug.security import generate_password_hash
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO users (username, email, password_hash)
                    VALUES (%s, %s, %s)
                """, (username, email, generate_password_hash(password)))
                return cur.lastrowid
            except pymysql.err.IntegrityError as e:
                if e.args[0] == 1062:
                    raise ValueError('用户名或邮箱已被注册')
                raise


def get_user_by_username(username):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            return cur.fetchone()


def get_user_by_id(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()


def set_user_tg_chat(user_id, chat_id):
    """保存 / 清空用户的 Telegram chat_id（空字符串视为解绑）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET tg_chat_id = %s WHERE id = %s",
                (chat_id or None, user_id))
            return cur.rowcount


def get_user_tg_chat(user_id):
    row = get_user_by_id(user_id)
    return row.get('tg_chat_id') if row else None


def set_user_email(user_id, email):
    """保存用户的收件邮箱（用于邮件推送）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET email = %s WHERE id = %s",
                        (email or None, user_id))
            return cur.rowcount


def get_user_email(user_id):
    row = get_user_by_id(user_id)
    return row.get('email') if row else None


def list_users_with_email():
    """返回所有已填写邮箱的用户（username / email），用于每日邮件推送。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT username, email FROM users
                WHERE email IS NOT NULL AND email <> ''
            """)
            return cur.fetchall()


def list_users_with_tg():
    """返回所有已绑定 Telegram 的用户（id/username/tg_chat_id）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, tg_chat_id FROM users
                WHERE tg_chat_id IS NOT NULL AND tg_chat_id <> ''
            """)
            return cur.fetchall()


# ================================================================ 站点设置
def get_setting(key, default=None):
    """读取站点级设置（键不存在返回 default）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT v FROM site_settings WHERE k = %s", (key,))
            row = cur.fetchone()
            return row['v'] if row else default


def set_setting(key, value):
    """写入 / 更新站点级设置（UPSERT）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO site_settings (k, v, updated_at)
                VALUES (%s, %s, NOW())
                ON DUPLICATE KEY UPDATE v = VALUES(v), updated_at = NOW()
            """, (key, value))
            return cur.rowcount


def verify_user(username, password):
    """校验用户名密码，成功返回用户行，失败返回 None。"""
    from werkzeug.security import check_password_hash
    user = get_user_by_username(username)
    if user and check_password_hash(user['password_hash'], password):
        return user
    return None


# ================================================================ 收藏
def toggle_favorite(user_id, item_id, note=None):
    """切换收藏状态，返回 True=已收藏 / False=已取消。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM user_favorites WHERE user_id = %s AND item_id = %s",
                (user_id, item_id))
            row = cur.fetchone()
            if row:
                cur.execute("DELETE FROM user_favorites WHERE id = %s", (row['id'],))
                return False
            cur.execute("""
                INSERT INTO user_favorites (user_id, item_id, note)
                VALUES (%s, %s, %s)
            """, (user_id, item_id, note))
            return True


def favorited_item_ids(user_id, item_ids):
    """批量查询哪些条目已被该用户收藏，返回 set。"""
    if not user_id or not item_ids:
        return set()
    fmt = ','.join(['%s'] * len(item_ids))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT item_id FROM user_favorites
                WHERE user_id = %s AND item_id IN ({fmt})
            """, (user_id, *item_ids))
            return {r['item_id'] for r in cur.fetchall()}


def list_favorites(user_id, limit=200):
    """用户的收藏列表（含条目与平台信息）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT f.id AS fav_id, f.note, f.created_at AS fav_at,
                       h.id, h.title, h.url, h.heat_value, h.heat_display,
                       h.rank_no, h.snapshot_date,
                       p.code AS platform_code, p.name AS platform_name
                FROM user_favorites f
                JOIN hot_items h ON h.id = f.item_id
                JOIN platforms p ON p.id = h.platform_id
                WHERE f.user_id = %s
                ORDER BY f.id DESC LIMIT %s
            """, (user_id, limit))
            return cur.fetchall()


def count_favorites(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM user_favorites WHERE user_id = %s",
                        (user_id,))
            return cur.fetchone()['c']


# ================================================================ 订阅
def set_subscriptions(user_id, platforms, keywords):
    """整体覆盖用户的订阅（平台代码列表 + 关键词列表）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_subscriptions WHERE user_id = %s", (user_id,))
            for code in platforms:
                cur.execute("""
                    INSERT IGNORE INTO user_subscriptions (user_id, sub_type, value)
                    VALUES (%s, 'platform', %s)
                """, (user_id, code))
            for kw in keywords:
                cur.execute("""
                    INSERT IGNORE INTO user_subscriptions (user_id, sub_type, value)
                    VALUES (%s, 'keyword', %s)
                """, (user_id, kw))


def get_subscriptions(user_id):
    """返回 {'platforms': [...], 'keywords': [...]}"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sub_type, value FROM user_subscriptions WHERE user_id = %s
            """, (user_id,))
            rows = cur.fetchall()
    out = {'platforms': [], 'keywords': []}
    for r in rows:
        key = 'platforms' if r['sub_type'] == 'platform' else 'keywords'
        out[key].append(r['value'])
    return out


def digest_items(user_id, date, per_platform=5):
    """
    个性化日报数据：订阅平台的热榜 + 关键词命中的条目。
    返回 {'platforms': {code: {'name':..., 'items': [...]}},
          'keyword_hits': [...], 'date': ...}
    """
    subs = get_subscriptions(user_id)
    result = {'date': str(date), 'platforms': {}, 'keyword_hits': []}
    for code in subs['platforms']:
        items = query_items(date=date, platform_code=code,
                            limit=per_platform)
        if items:
            result['platforms'][code] = {
                'name': items[0]['platform_name'], 'items': items}
    if subs['keywords']:
        seen = set()
        for kw in subs['keywords']:
            for it in query_items(date=date, keyword=kw, limit=10):
                if it['id'] not in seen:
                    seen.add(it['id'])
                    it['matched_keyword'] = kw
                    result['keyword_hits'].append(it)
        result['keyword_hits'].sort(key=lambda x: x['rank_no'])
    return result


# ================================================================ 趋势与共振
def get_title_trend(title, days=14):
    """
    某标题（自动归一化）近 N 日的热度/排名走势，按日期升序。
    同一天出现在多个平台时取当日最高热度（避免折线锯齿）。
    返回 [{'date','heat_value','rank_no','platform_name'}, ...]
    """
    norm = normalize_title(title)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT h.snapshot_date AS date, MAX(h.heat_value) AS heat_value,
                       p.name AS platform_name
                FROM hot_items h
                JOIN platforms p ON p.id = h.platform_id
                WHERE h.title_norm = %s
                  AND h.snapshot_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                GROUP BY h.snapshot_date, p.name
                ORDER BY h.snapshot_date
            """, (norm, days))
            rows = cur.fetchall()
    # 每日只保留最高热度的一条（含其平台名）
    best = {}
    for r in rows:
        d = r['date']
        if d not in best or r['heat_value'] > best[d]['heat_value']:
            best[d] = r
    out = sorted(best.values(), key=lambda r: r['date'])
    for r in out:
        r['date'] = str(r['date'])
    return out


def _trend_series_bulk(title_norms, days=14):
    """
    批量取多个话题的热度走势，一次查询解决 N+1 问题。
    返回 {title_norm: [{'date','heat_value','rank_no','platform_name'}, ...]}
    """
    norms = [n for n in (title_norms or []) if n]
    if not norms:
        return {}
    fmt = ','.join(['%s'] * len(norms))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT h.title_norm, h.snapshot_date AS date,
                       MAX(h.heat_value) AS heat_value,
                       SUBSTRING_INDEX(GROUP_CONCAT(p.name ORDER BY h.heat_value DESC),
                                       ',', 1) AS platform_name
                FROM hot_items h
                JOIN platforms p ON p.id = h.platform_id
                WHERE h.title_norm IN ({fmt})
                  AND h.snapshot_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                GROUP BY h.title_norm, h.snapshot_date
            """, (*norms, days))
            rows = cur.fetchall()
    grouped = {}
    for r in rows:
        grouped.setdefault(r['title_norm'], []).append({
            'date': str(r['date']),
            'heat_value': r['heat_value'],
            'rank_no': r.get('rank_no'),
            'platform_name': r['platform_name'],
        })
    for series in grouped.values():
        series.sort(key=lambda x: x['date'])
    return grouped


def trending_topics(days=7, min_days=2, limit=30):
    """
    近 N 日持续在榜的话题（跨日期趋势），按在榜天数降序。
    返回 [{'title','days','platforms','first_date','last_date',
           'heat_change','latest_heat','series':[...]}]
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT h.title, h.title_norm,
                       COUNT(DISTINCT h.snapshot_date) AS days,
                       MIN(h.snapshot_date) AS first_date,
                       MAX(h.snapshot_date) AS last_date,
                       GROUP_CONCAT(DISTINCT p.name) AS platforms
                FROM hot_items h
                JOIN platforms p ON p.id = h.platform_id
                WHERE h.snapshot_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                GROUP BY h.title_norm, h.title
                HAVING days >= %s
                ORDER BY days DESC, MAX(h.heat_value) DESC
                LIMIT %s
            """, (days, min_days, limit))
            rows = cur.fetchall()
    # 批量取所有话题的热度序列（1 次查询，避免 N+1）
    bulk = _trend_series_bulk([r['title_norm'] for r in rows], days=days)
    for r in rows:
        series = bulk.get(r['title_norm']) or []
        r['series'] = series
        if len(series) >= 2:
            first, last = series[0]['heat_value'], series[-1]['heat_value']
            r['heat_change'] = (last - first) / first * 100 if first else 0
            r['latest_heat'] = last
        else:
            r['heat_change'] = 0
            r['latest_heat'] = series[0]['heat_value'] if series else 0
        r['first_date'] = str(r['first_date'])
        r['last_date'] = str(r['last_date'])
    return rows


def trend_points(series, width=120, height=36):
    """把趋势序列 [{'heat_value'}, ...] 转成 SVG polyline points 字符串。"""
    if not series or len(series) < 2:
        return ''
    heats = [float(p['heat_value']) for p in series]
    mn, mx = min(heats), max(heats)
    span = mx - mn
    pts = []
    n = len(series)
    for i, p in enumerate(series):
        x = i * width / (n - 1)
        if span == 0:          # 全程等值：置于中线，避免贴边
            y = height / 2
        else:
            y = height - 4 - ((float(p['heat_value']) - mn) / span) * (height - 8)
        pts.append(f"{x:.1f},{y:.1f}")
    return ' '.join(pts)


def resonance_items(date, min_platforms=2, limit=30):
    """
    跨平台共振：同一天出现在多个平台热榜的话题，按覆盖平台数降序。
    返回 [{'title','platform_count','platforms','max_heat','items':[...]}]
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT h.title, h.title_norm,
                       COUNT(DISTINCT h.platform_id) AS platform_count,
                       GROUP_CONCAT(DISTINCT p.name ORDER BY p.sort_order) AS platforms,
                       MAX(h.heat_value) AS max_heat
                FROM hot_items h
                JOIN platforms p ON p.id = h.platform_id
                WHERE h.snapshot_date = %s
                GROUP BY h.title_norm, h.title
                HAVING platform_count >= %s
                ORDER BY platform_count DESC, max_heat DESC
                LIMIT %s
            """, (date, min_platforms, limit))
            rows = cur.fetchall()
    for r in rows:
        r['items'] = query_items(date=date, keyword=r['title'], limit=20)
    return rows

# ================================================================ 全文检索
_FT_SPECIAL = set('+-<>()~*"@')


def _sanitize_ft_query(q):
    """清洗 BOOLEAN MODE 查询串：去掉操作符字符，避免语法错误。"""
    out = ''.join((' ' if ch in _FT_SPECIAL else ch) for ch in (q or ''))
    return ' '.join(out.split())


_SEARCH_COLS = """
    h.id, h.title, h.title_norm, h.url, h.heat_value, h.heat_display, h.rank_no,
    h.summary, h.snapshot_date, h.crawled_at,
    p.code AS platform_code, p.name AS platform_name, p.category,
    CASE WHEN dx.id IS NULL THEN 0 ELSE 1 END AS has_detail
"""


def _search_title(q, date, platform_code, limit):
    """标题 + 摘要的全文检索。"""
    sql = f"""
        SELECT {_SEARCH_COLS},
               MATCH(h.title, h.summary) AGAINST (%s IN BOOLEAN MODE) AS score,
               'title' AS match_in, NULL AS content_snippet
        FROM hot_items h
        JOIN platforms p ON p.id = h.platform_id
        LEFT JOIN hot_item_details dx ON dx.item_id = h.id
            AND dx.fetch_status = 'success'
        WHERE MATCH(h.title, h.summary) AGAINST (%s IN BOOLEAN MODE)
    """
    params = [q, q]
    if date:
        sql += " AND h.snapshot_date = %s"
        params.append(date)
    if platform_code:
        sql += " AND p.code = %s"
        params.append(platform_code)
    sql += " ORDER BY score DESC, h.snapshot_date DESC LIMIT %s"
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _search_content(q, date, platform_code, limit):
    """详情正文的全文检索。"""
    sql = f"""
        SELECT {_SEARCH_COLS},
               MATCH(d.content) AGAINST (%s IN BOOLEAN MODE) AS score,
               'content' AS match_in,
               LEFT(d.content, 160) AS content_snippet
        FROM hot_item_details d
        JOIN hot_items h ON h.id = d.item_id
        JOIN platforms p ON p.id = h.platform_id
        LEFT JOIN hot_item_details dx ON dx.item_id = h.id
            AND dx.fetch_status = 'success'
        WHERE MATCH(d.content) AGAINST (%s IN BOOLEAN MODE)
          AND d.fetch_status = 'success'
    """
    params = [q, q]
    if date:
        sql += " AND h.snapshot_date = %s"
        params.append(date)
    if platform_code:
        sql += " AND p.code = %s"
        params.append(platform_code)
    sql += " ORDER BY score DESC, h.snapshot_date DESC LIMIT %s"
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _search_like(q, date, platform_code, limit):
    """降级方案：无全文索引时用 LIKE 模糊匹配。"""
    sql = f"""
        SELECT {_SEARCH_COLS}, 1.0 AS score, 'title' AS match_in,
               NULL AS content_snippet
        FROM hot_items h
        JOIN platforms p ON p.id = h.platform_id
        LEFT JOIN hot_item_details dx ON dx.item_id = h.id
            AND dx.fetch_status = 'success'
        WHERE h.title LIKE %s
    """
    params = [f"%{q}%"]
    if date:
        sql += " AND h.snapshot_date = %s"
        params.append(date)
    if platform_code:
        sql += " AND p.code = %s"
        params.append(platform_code)
    sql += " ORDER BY h.snapshot_date DESC, p.sort_order, h.rank_no LIMIT %s"
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def search_items(q, date=None, platform_code=None, include_content=False,
                 limit=50):
    """
    全文检索：先用 MySQL ngram 全文索引按相关度排序，
    索引不可用（旧库未迁移）时自动降级为 LIKE 模糊匹配。
    返回已去重的条目列表（含 score / match_in / content_snippet）。
    """
    raw = (q or '').strip()
    q = _sanitize_ft_query(raw)
    if not q:
        return []

    use_ft = True
    try:
        rows = _search_title(q, date, platform_code, limit)
        if include_content:
            rows += _search_content(q, date, platform_code, limit)
    except Exception as e:      # 全文索引缺失或查询语法异常 -> 降级
        if 'FULLTEXT' in str(e).upper() or getattr(e, 'args', [None])[0] in (1191, 1064):
            use_ft = False
        else:                   # 查询串本身有问题，改用 LIKE 兜底
            use_ft = False
        rows = _search_like(raw or q, date, platform_code, limit)

    # 同一话题可能在多个日期/多个平台重复命中：按标题归一化聚合。
    # 展示代表取「日期最新」的一条（反映当前状态），
    # 排序权重取组内「最高相关度」，并统计命中天数。
    groups = {}
    for r in rows:
        if r.get('crawled_at'):
            r['crawled_at'] = str(r['crawled_at'])
        r['snapshot_date'] = str(r['snapshot_date'])
        score = round(float(r['score'] or 0), 4)
        r['score'] = score
        r['best_score'] = score
        key = r.get('title_norm') or normalize_title(r['title'])
        cur = groups.get(key)
        if cur is None:
            r['hit_dates'] = {r['snapshot_date']}
            r['content_hit'] = (r['match_in'] == 'content')
            groups[key] = r
            continue

        cur['hit_dates'].add(r['snapshot_date'])
        best = max(float(cur['best_score']), score)
        if r['match_in'] == 'content':
            cur['content_hit'] = True
        if r['snapshot_date'] > cur['snapshot_date']:
            # 用更新的条目做代表，继承已汇总的信息
            r['hit_dates'] = cur['hit_dates']
            r['best_score'] = best
            r['content_hit'] = cur['content_hit']
            if not r.get('content_snippet') and cur.get('content_snippet'):
                r['content_snippet'] = cur['content_snippet']
            groups[key] = r
        else:
            cur['best_score'] = best
            if not cur.get('content_snippet') and r.get('content_snippet'):
                cur['content_snippet'] = r['content_snippet']

    out = list(groups.values())
    for r in out:
        r['hit_dates'] = sorted(r['hit_dates'], reverse=True)
        r['hit_days'] = len(r['hit_dates'])
        if r.get('content_hit'):
            r['match_in'] = 'content'
    out.sort(key=lambda r: (-float(r['best_score']), r['snapshot_date']))
    return out[:limit]


def search_supported():
    """当前数据库是否已具备全文检索能力（用于页面提示）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS c FROM information_schema.statistics
                WHERE table_schema = DATABASE() AND index_type = 'FULLTEXT'
            """)
            return cur.fetchone()['c'] > 0


# ================================================================ 数据导出
def export_rows(date=None, platform_code=None, keyword=None,
                with_detail=False, limit=2000):
    """
    导出用数据：在 query_items 基础上批量附加详情正文。
    返回 (rows, columns)
    """
    rows = query_items(date=date, platform_code=platform_code,
                       keyword=keyword, limit=limit)
    details = {}
    if with_detail and rows:
        ids = [r['id'] for r in rows]
        fmt = ','.join(['%s'] * len(ids))
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT item_id, content, author, published_at, source_site
                    FROM hot_item_details
                    WHERE item_id IN ({fmt}) AND fetch_status = 'success'
                """, ids)
                for d in cur.fetchall():
                    details[d['item_id']] = d
    for r in rows:
        r['snapshot_date'] = str(r['snapshot_date'])
        if r.get('crawled_at'):
            r['crawled_at'] = str(r['crawled_at'])
        d = details.get(r['id']) or {}
        r['detail_content'] = d.get('content')
        r['detail_author'] = d.get('author')
        r['detail_published_at'] = str(d['published_at']) if d.get('published_at') else None
        r['detail_source_site'] = d.get('source_site')
    columns = [
        ('snapshot_date', '日期'), ('platform_name', '平台'), ('rank_no', '排名'),
        ('title', '标题'), ('heat_display', '热度'), ('url', '链接'),
        ('summary', '摘要'),
    ]
    if with_detail:
        columns += [('detail_author', '作者'),
                    ('detail_published_at', '发布时间'),
                    ('detail_source_site', '来源站点'),
                    ('detail_content', '正文')]
    return rows, columns


# ================================================================ 智能分析
def sentiment_stats(date=None):
    """
    某日（默认最新一期）热点的情绪分布统计。
    返回 {
      'date', 'total',
      'counts': {'positive':n,'neutral':n,'negative':n},
      'positive':[top 条目], 'negative':[top 条目], 'controversial':[top 条目]
    }
    top 排序：命中词数多者优先；controversial 取正负词同时命中的条目。
    """
    date = date or str(get_latest_date())
    items = query_items(date=date, limit=500)
    counts = {'positive': 0, 'neutral': 0, 'negative': 0}
    for it in items:
        text = f"{it.get('title') or ''} {it.get('summary') or ''}"
        a = sentiment.analyze(text)
        it['sentiment'] = a['label']
        it['pos'] = a['pos']
        it['neg'] = a['neg']
        it['pos_words'] = a['pos_words']
        it['neg_words'] = a['neg_words']
        counts[a['label']] += 1

    def score(it):
        return it['pos'] + it['neg']

    positive = sorted([i for i in items if i['sentiment'] == 'positive'],
                      key=score, reverse=True)[:12]
    negative = sorted([i for i in items if i['sentiment'] == 'negative'],
                      key=score, reverse=True)[:12]
    controversial = sorted(
        [i for i in items if i['pos'] and i['neg']],
        key=lambda i: i['pos'] + i['neg'], reverse=True)[:10]
    return {
        'date': date,
        'total': len(items),
        'counts': counts,
        'positive': positive,
        'negative': negative,
        'controversial': controversial,
    }


def lifecycle_label(series):
    """
    根据某话题的每日热度序列判断生命周期阶段。
    series: [{'heat_value', 'snapshot_date'}, ...]（按日期升序）
    返回 {'label': 'rising'|'peak'|'declining'|'steady'|'new', 'cn': 中文}
    """
    if not series or len(series) < 2:
        # 仅出现 1 天：新晋话题
        return {'label': 'new', 'cn': '新晋'}
    heats = [float(p['heat_value']) for p in series]
    first, last = heats[0], heats[-1]
    prev = heats[-2]
    base = first if first else 1.0
    change = (last - first) / base              # 区间整体涨跌
    recent = (last - prev) / (prev if prev else 1.0)  # 最近一期涨跌
    if recent >= 0.5 and change > 0:
        label, cn = 'peak', '爆发'
    elif change >= 0.05 or recent >= 0.1:
        label, cn = 'rising', '上升'
    elif change <= -0.05 or recent <= -0.1:
        label, cn = 'declining', '衰退'
    else:
        label, cn = 'steady', '平稳'
    return {'label': label, 'cn': cn}


def lifecycle_topics(days=7, min_days=2, limit=30):
    """
    在 trending_topics 基础上为每个话题附加生命周期阶段标签。
    """
    topics = trending_topics(days=days, min_days=min_days, limit=limit)
    for t in topics:
        t['lifecycle'] = lifecycle_label(t.get('series') or [])
    return topics

