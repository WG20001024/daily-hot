# -*- coding: utf-8 -*-
"""
每日热点网站 —— Flask 应用入口
提供页面路由、JSON API 与每日定时抓取任务。

启动：
    python app.py            # 开发模式，默认 127.0.0.1:5000
    python app.py --init     # 先执行一次抓取再启动（库为空时自动触发）
"""
import csv
import io
import logging
import os
import sys
import threading
from datetime import date, datetime
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (Flask, Response, abort, g, jsonify, make_response,
                   redirect, render_template, request, session, url_for)

import crawler
import db
import feed
import ai_summary
import telegram_push
import mail_push
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
log = logging.getLogger('app')

SECRET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '.secret_key')


def _load_secret():
    """读取或生成持久化的 session 密钥（避免重启后登录态失效）。"""
    if os.environ.get('SECRET_KEY'):
        return os.environ['SECRET_KEY']
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()
    import secrets
    key = secrets.token_hex(32)
    with open(SECRET_FILE, 'w', encoding='utf-8') as f:
        f.write(key)
    return key


def login_required(view):
    """要求登录的视图装饰器。"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            if request.path.startswith('/api/'):
                return jsonify({'ok': False,
                                'message': '请先登录'}), 401
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapper


def create_app():
    app = Flask(__name__)
    app.config['JSON_AS_ASCII'] = False
    app.secret_key = _load_secret()
    app.jinja_env.globals['trend_points'] = db.trend_points

    # ------------------------------------------------------------ PWA 响应头
    @app.after_request
    def pwa_headers(response):
        # Service Worker 允许控制整个站点作用域
        if request.path == '/static/sw.js':
            response.headers['Service-Worker-Allowed'] = '/'
            response.headers['Cache-Control'] = 'no-cache'
        # manifest / 图标是可长期缓存的静态资源（覆盖 Flask 默认 no-cache）
        elif request.path.startswith('/static/') and request.path.endswith(
                ('manifest.json', '.png')):
            response.headers['Cache-Control'] = 'public, max-age=604800'
        return response

    # ------------------------------------------------------------ 上下文
    @app.before_request
    def load_current_user():
        g.user = None
        uid = session.get('user_id')
        if uid:
            g.user = db.get_user_by_id(uid)

    @app.context_processor
    def inject_user():
        return {'current_user': g.user}

    def _latest_source(date_str):
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT source FROM crawl_logs
                    WHERE status IN ('success', 'mock')
                    ORDER BY id DESC LIMIT 1
                """)
                row = cur.fetchone()
                return row['source'] if row else None

    # ============================================================ 页面路由
    @app.route('/')
    def index():
        date_str = request.args.get('date') or str(db.get_latest_date() or date.today())
        platforms = db.list_platforms(enabled_only=True)
        items = db.query_items(date=date_str)
        # 标记收藏状态
        if g.user:
            fav_ids = db.favorited_item_ids(
                g.user['id'], [i['id'] for i in items])
            for it in items:
                it['favorited'] = it['id'] in fav_ids
        source = _latest_source(date_str)
        resonance = db.resonance_items(date_str) if items else []
        return render_template(
            'index.html',
            items=items,
            platforms=platforms,
            current_date=date_str,
            is_demo=(source == 'offline-demo'),
            source=source,
            item_count=len(items),
            platform_count=len({i['platform_code'] for i in items}),
            resonance=resonance,
        )

    @app.route('/history')
    def history():
        dates = db.list_dates(limit=60)
        return render_template('history.html', dates=dates)

    # ------------------------------------------------------------ 搜索
    @app.route('/search')
    def search():
        q = (request.args.get('q') or '').strip()
        date_str = request.args.get('date') or ''
        platform = request.args.get('platform') or ''
        include_content = request.args.get('full') == '1'
        results = []
        if q:
            results = db.search_items(
                q, date=date_str or None, platform_code=platform or None,
                include_content=include_content, limit=Config.SEARCH_LIMIT)
            if g.user:
                fav_ids = db.favorited_item_ids(
                    g.user['id'], [r['id'] for r in results])
                for r in results:
                    r['favorited'] = r['id'] in fav_ids
        return render_template(
            'search.html', q=q, results=results, current_date=date_str,
            platform=platform, full=include_content,
            platforms=db.list_platforms(), ft_supported=db.search_supported())

    # ------------------------------------------------------------ 导出
    @app.route('/export')
    def export_page():
        return render_template(
            'export.html',
            platforms=db.list_platforms(),
            dates=db.list_dates(limit=60),
            latest_date=str(db.get_latest_date() or date.today()),
            max_rows=Config.EXPORT_MAX_ROWS)

    # ------------------------------------------------------------ RSS
    @app.route('/rss.xml')
    def rss_feed():
        """RSS 2.0 订阅源，支持 platform / date / limit 过滤。"""
        platform = request.args.get('platform')
        date_str = request.args.get('date') or str(
            db.get_latest_date() or date.today())
        limit = request.args.get('limit', Config.RSS_LIMIT, type=int)
        limit = max(1, min(limit or Config.RSS_LIMIT, Config.RSS_MAX_LIMIT))
        items = db.query_items(date=date_str, platform_code=platform,
                               limit=limit)
        plat_name = None
        if platform:
            for p in db.list_platforms(enabled_only=False):
                if p['code'] == platform:
                    plat_name = p['name']
                    break
        title, desc = feed.feed_meta(plat_name, date_str)
        site_url = request.host_url.rstrip('/')
        xml = feed.build_rss(items, title, site_url, desc, self_url=request.url)
        return Response(xml, mimetype='application/rss+xml; charset=utf-8')

    @app.route('/feed')
    def feed_preview():
        """RSS 的 HTML 预览页，保证任意浏览器都能友好阅读。"""
        platform = request.args.get('platform')
        date_str = request.args.get('date') or str(
            db.get_latest_date() or date.today())
        limit = request.args.get('limit', Config.RSS_LIMIT, type=int)
        limit = max(1, min(limit or Config.RSS_LIMIT, Config.RSS_MAX_LIMIT))
        items = db.query_items(date=date_str, platform_code=platform,
                               limit=limit)
        plat_name = None
        if platform:
            for p in db.list_platforms(enabled_only=False):
                if p['code'] == platform:
                    plat_name = p['name']
                    break
        feed_title, feed_description = feed.feed_meta(plat_name, date_str)
        rss_url = '/rss.xml'
        if platform or date_str or limit != Config.RSS_LIMIT:
            parts = []
            if platform:
                parts.append(f'platform={platform}')
            if date_str:
                parts.append(f'date={date_str}')
            if limit != Config.RSS_LIMIT:
                parts.append(f'limit={limit}')
            rss_url = '/rss.xml?' + '&'.join(parts)
        return render_template(
            'feed.html',
            items=items,
            feed_title=feed_title,
            feed_description=feed_description,
            platform_code=platform,
            date_str=date_str,
            rss_url=rss_url,
        )

    @app.route('/trends')
    def trends():
        days = request.args.get('days', 7, type=int)
        topics = db.trending_topics(days=days)
        return render_template('trends.html', topics=topics, days=days)

    @app.route('/resonance')
    def resonance():
        date_str = request.args.get('date') or str(db.get_latest_date() or date.today())
        topics = db.resonance_items(date_str)
        return render_template('resonance.html', topics=topics,
                               current_date=date_str)

    @app.route('/insight')
    def insight():
        """智能洞察：AI 综述 + 情绪分布 + 话题生命周期。"""
        days = request.args.get('days', 7, type=int)
        date_str = request.args.get('date') or str(
            db.get_latest_date() or date.today())
        topics = db.lifecycle_topics(days=days, limit=30)
        resonance = db.resonance_items(date_str)
        sentiment = db.sentiment_stats(date_str)
        summary = ai_summary.generate_summary(
            date_str, topics=topics, resonance=resonance, sentiment=sentiment)
        return render_template(
            'insight.html',
            date=date_str,
            days=days,
            topics=topics,
            resonance=resonance[:10],
            sentiment=sentiment,
            summary=summary,
            tg_bot_set=bool(telegram_push.get_config()['token']),
            tg_chat_set=bool(telegram_push.get_config()['chat_id']),
        )

    @app.route('/item/<int:item_id>')
    def item_detail(item_id):
        item = db.get_item(item_id)
        if not item:
            return render_template('item.html', item=None), 404
        detail = db.get_detail(item_id)
        trend = db.get_title_trend(item['title'])
        favorited = False
        if g.user:
            favorited = item_id in db.favorited_item_ids(g.user['id'], [item_id])
        return render_template('item.html', item=item, detail=detail,
                               trend=trend, favorited=favorited,
                               is_demo=(_latest_source(str(item['snapshot_date']))
                                        == 'offline-demo'))

    # ============================================================ 用户系统
    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            email = (request.form.get('email') or '').strip() or None
            if not username or len(username) < 2:
                return render_template('register.html', error='用户名至少 2 个字符')
            if len(password) < 6:
                return render_template('register.html', error='密码至少 6 位')
            try:
                uid = db.create_user(username, password, email)
                session['user_id'] = uid
                session['username'] = username
                return redirect(request.args.get('next') or url_for('index'))
            except ValueError as e:
                return render_template('register.html', error=str(e))
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            user = db.verify_user(username, password)
            if user:
                session['user_id'] = user['id']
                session['username'] = user['username']
                return redirect(request.args.get('next') or url_for('index'))
            return render_template('login.html', error='用户名或密码错误')
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('index'))

    # ============================================================ 个人中心
    @app.route('/me')
    @login_required
    def me():
        """个人中心：聚合我的日报 / 收藏 / Telegram / 邮件 / 站点设置。"""
        tab = request.args.get('tab', 'digest')
        if tab not in ('digest', 'favorites', 'telegram', 'mail', 'settings'):
            tab = 'digest'
        date_str = request.args.get('date') or str(
            db.get_latest_date() or date.today())
        smtp = mail_push.get_config()
        return render_template(
            'me.html',
            tab=tab,
            digest=db.digest_items(g.user['id'], date_str),
            subs=db.get_subscriptions(g.user['id']),
            favorites=db.list_favorites(g.user['id'], limit=10),
            fav_items=db.list_favorites(g.user['id']),
            fav_count=db.count_favorites(g.user['id']),
            platforms=db.list_platforms(),
            tg_token=db.get_setting('TG_BOT_TOKEN', '') or Config.TG_BOT_TOKEN,
            tg_chat_id=db.get_setting('TG_CHAT_ID', '') or Config.TG_CHAT_ID,
            my_tg_chat_id=db.get_setting('MY_TG_CHAT_ID', ''),
            my_bound_chat=g.user.get('tg_chat_id'),
            tg_bot_env=bool(getattr(Config, 'TG_BOT_TOKEN', '')),
            tg_chat_env=bool(getattr(Config, 'TG_CHAT_ID', '')),
            my_email=g.user.get('email'),
            smtp_host=smtp['host'],
            smtp_port=smtp['port'],
            smtp_user=smtp['user'],
            smtp_password=smtp['password'],
            smtp_use_ssl=smtp['use_ssl'],
        )

    # ============================================================ 收藏
    @app.route('/favorites')
    @login_required
    def favorites():
        """旧入口：并入个人中心的「我的收藏」标签。"""
        return redirect(url_for('me', tab='favorites'))

    @app.route('/api/favorite/<int:item_id>', methods=['POST'])
    @login_required
    def api_favorite(item_id):
        note = (request.form.get('note') or '').strip() or None
        state = db.toggle_favorite(g.user['id'], item_id, note)
        return jsonify({'ok': True, 'favorited': state,
                        'count': db.count_favorites(g.user['id'])})

    # ============================================================ 个性化日报
    @app.route('/digest')
    @login_required
    def digest():
        """旧入口：并入个人中心的「我的日报」标签。"""
        return redirect(url_for('me', tab='digest'))

    @app.route('/api/subscriptions', methods=['POST'])
    @login_required
    def api_subscriptions():
        platforms = request.form.getlist('platforms')
        raw = request.form.get('keywords') or ''
        keywords = [k.strip() for k in raw.replace('，', ',').split(',') if k.strip()]
        db.set_subscriptions(g.user['id'], platforms, keywords[:20])
        return jsonify({'ok': True, 'platforms': platforms, 'keywords': keywords})

    @app.route('/api/tg/bind', methods=['POST'])
    @login_required
    def api_tg_bind():
        """绑定 / 解绑用户 Telegram chat_id，并向其发送测试消息。"""
        chat_id = (request.form.get('chat_id') or '').strip()
        db.set_user_tg_chat(g.user['id'], chat_id)
        if not chat_id:
            return jsonify({'ok': True, 'saved': True, 'message': '已解绑'})
        ok, err = telegram_push.send_message(
            chat_id,
            '✅ 绑定成功！每日热点已连接到你的 Telegram，'
            '每日抓取后会自动推送智能洞察。')
        return jsonify({'ok': True, 'saved': True, 'sent': ok, 'error': err})

    @app.route('/api/tg/send', methods=['POST'])
    @login_required
    def api_tg_send():
        """把当日智能洞察推送到当前用户绑定的 Telegram。"""
        chat_id = db.get_user_tg_chat(g.user['id'])
        if not chat_id:
            return jsonify({'ok': False, 'message': '尚未绑定 Telegram'}), 400
        ok, err = telegram_push.push_insight(chat_id)
        return jsonify({'ok': ok, 'error': err})

    @app.route('/api/tg/broadcast', methods=['POST'])
    @login_required
    def api_tg_broadcast():
        """向站点全局频道（TG_CHAT_ID）广播当日洞察（需已配置）。"""
        chat_id = telegram_push.get_config()['chat_id']
        if not chat_id:
            return jsonify({'ok': False,
                            'message': '未配置全局 TG_CHAT_ID'}), 400
        ok, err = telegram_push.push_insight(chat_id)
        return jsonify({'ok': ok, 'error': err})

    @app.route('/settings', methods=['GET', 'POST'])
    @login_required
    def site_settings():
        """站点设置：GET 并入个人中心「站点设置」标签；POST 仍接收表单保存。"""
        if request.method == 'POST':
            db.set_setting('TG_BOT_TOKEN',
                           (request.form.get('tg_token') or '').strip())
            db.set_setting('TG_CHAT_ID',
                           (request.form.get('tg_chat_id') or '').strip())
            return redirect(url_for('me', tab='settings'))
        return redirect(url_for('me', tab='settings'))

    @app.route('/api/tg/bind-mine', methods=['POST'])
    @login_required
    def api_tg_bind_mine():
        """把「我的 Telegram chat_id」一键绑定到当前登录账号。"""
        chat_id = (request.form.get('chat_id')
                   or db.get_setting('MY_TG_CHAT_ID', '')).strip()
        if not chat_id:
            return jsonify({'ok': False, 'message': '请先填写我的 chat_id'}), 400
        db.set_setting('MY_TG_CHAT_ID', chat_id)
        db.set_user_tg_chat(g.user['id'], chat_id)
        ok, err = telegram_push.send_message(
            chat_id, '✅ 已绑定成功！此后每日热点会自动推送到这个 Telegram。')
        return jsonify({'ok': True, 'saved': True, 'sent': ok, 'error': err})

    @app.route('/api/tg/verify', methods=['POST'])
    @login_required
    def api_tg_verify():
        """校验 token + chat_id：向该 chat_id 发送测试消息，返回结果。"""
        token = (request.form.get('token') or '').strip()
        chat_id = (request.form.get('chat_id') or '').strip()
        if not token or not chat_id:
            return jsonify({'ok': False, 'message': '请填写 token 和 chat_id'}), 400
        ok, err = telegram_push.send_message(
            chat_id,
            '✅ 校验成功！这个 chat_id 可以正常接收每日热点的 Telegram 推送。',
            token=token)
        return jsonify({'ok': ok, 'error': err})

    # ============================================================ 邮件推送
    @app.route('/api/mail/bind', methods=['POST'])
    @login_required
    def api_mail_bind():
        """保存收件邮箱并发送测试邮件。"""
        email = (request.form.get('email') or '').strip()
        if email and '@' not in email:
            return jsonify({'ok': False, 'message': '邮箱格式不正确'}), 400
        db.set_user_email(g.user['id'], email)
        if not email:
            return jsonify({'ok': True, 'saved': True, 'message': '已解绑邮箱'})
        ok, err = mail_push.send_mail(
            email,
            f'【{Config.SITE_NAME}】测试邮件',
            '<div style="font-family:sans-serif;padding:20px">'
            '<h2>✅ 绑定成功！</h2>'
            '<p>此后每日抓取完成，智能洞察会自动发送到本邮箱。</p></div>')
        return jsonify({'ok': True, 'saved': True, 'sent': ok, 'error': err})

    @app.route('/api/mail/send', methods=['POST'])
    @login_required
    def api_mail_send():
        """把当日洞察发送到当前账号的邮箱。"""
        email = db.get_user_email(g.user['id'])
        if not email:
            return jsonify({'ok': False, 'message': '尚未绑定邮箱'}), 400
        ok, err = mail_push.send_digest(
            email, site_url=telegram_push.site_url())
        return jsonify({'ok': ok, 'error': err})

    @app.route('/api/mail/smtp', methods=['POST'])
    @login_required
    def api_mail_smtp():
        """保存 SMTP 配置（QQ 邮箱授权码等）并可选发测试邮件。"""
        db.set_setting('SMTP_HOST', (request.form.get('smtp_host') or '').strip())
        port = (request.form.get('smtp_port') or '').strip()
        db.set_setting('SMTP_PORT', port or '465')
        db.set_setting('SMTP_USER', (request.form.get('smtp_user') or '').strip())
        db.set_setting('SMTP_PASSWORD',
                       (request.form.get('smtp_password') or '').strip())
        db.set_setting('SMTP_USE_SSL',
                       '0' if request.form.get('smtp_ssl') == '0' else '1')
        to_addr = (request.form.get('test_to') or '').strip()
        if not to_addr:
            return jsonify({'ok': True, 'saved': True, 'message': '已保存 SMTP 配置'})
        ok, err = mail_push.send_mail(
            to_addr,
            f'【{Config.SITE_NAME}】SMTP 测试邮件',
            '<div style="font-family:sans-serif;padding:20px">'
            '<h2>✅ SMTP 配置可用</h2>'
            '<p>每日热点将自动推送到本邮箱。</p></div>')
        return jsonify({'ok': True, 'saved': True, 'sent': ok, 'error': err})

    # ============================================================ JSON API
    @app.route('/api/hot')
    def api_hot():
        date_str = request.args.get('date') or str(db.get_latest_date() or date.today())
        platform = request.args.get('platform')
        keyword = request.args.get('q')
        items = db.query_items(date=date_str, platform_code=platform,
                               keyword=keyword, limit=1000)
        if g.user:
            fav_ids = db.favorited_item_ids(
                g.user['id'], [i['id'] for i in items])
            for it in items:
                it['favorited'] = it['id'] in fav_ids
        return jsonify({
            'date': date_str,
            'total': len(items),
            'source': _latest_source(date_str),
            'items': items,
        })

    @app.route('/api/item/<int:item_id>')
    def api_item(item_id):
        """单条热点及其详细信息。"""
        item = db.get_item(item_id)
        if not item:
            return jsonify({'ok': False, 'message': '条目不存在'}), 404
        detail = db.get_detail(item_id)
        for row in (item, detail):
            if row and row.get('crawled_at'):
                row['crawled_at'] = str(row['crawled_at'])
        return jsonify({'ok': True, 'item': item, 'detail': detail})

    @app.route('/api/dates')
    def api_dates():
        return jsonify({'dates': [str(d['snapshot_date']) for d in db.list_dates()]})

    @app.route('/api/platforms')
    def api_platforms():
        return jsonify({'platforms': db.list_platforms()})

    @app.route('/api/status')
    def api_status():
        latest = db.get_latest_date()
        return jsonify({
            'latest_date': str(latest) if latest else None,
            'today_count': db.count_today(date.today()),
            'latest_source': _latest_source(str(latest)) if latest else None,
            'user': g.user['username'] if g.user else None,
            'favorites': db.count_favorites(g.user['id']) if g.user else 0,
            'next_run': _next_run_str(),
        })

    @app.route('/api/refresh', methods=['POST'])
    def api_refresh():
        """手动触发一次抓取（立即执行）。"""
        try:
            summary = crawler.crawl_and_save()
            return jsonify({'ok': True, **summary})
        except Exception as e:
            log.exception("手动抓取失败")
            return jsonify({'ok': False, 'message': f'{type(e).__name__}: {e}'}), 500

    @app.route('/api/search')
    def api_search():
        """全文检索 API。参数：q / date / platform / full(1 含正文) / limit"""
        q = (request.args.get('q') or '').strip()
        if not q:
            return jsonify({'ok': False, 'message': '缺少查询词 q'}), 400
        limit = request.args.get('limit', Config.SEARCH_LIMIT, type=int)
        rows = db.search_items(
            q,
            date=request.args.get('date') or None,
            platform_code=request.args.get('platform') or None,
            include_content=request.args.get('full') == '1',
            limit=max(1, min(limit or Config.SEARCH_LIMIT, 200)))
        return jsonify({'ok': True, 'query': q, 'total': len(rows),
                        'items': rows})

    @app.route('/api/export')
    def api_export():
        """
        数据导出 API。
        参数：format=json|csv、date、platform、q、full(1 含正文)、limit
        """
        fmt = (request.args.get('format') or 'json').lower()
        date_str = request.args.get('date') or str(
            db.get_latest_date() or date.today())
        platform = request.args.get('platform') or None
        keyword = request.args.get('q') or None
        with_detail = request.args.get('full') == '1'
        limit = max(1, min(request.args.get(
            'limit', Config.EXPORT_MAX_ROWS, type=int) or Config.EXPORT_MAX_ROWS,
            Config.EXPORT_MAX_ROWS))

        rows, columns = db.export_rows(
            date=date_str, platform_code=platform, keyword=keyword,
            with_detail=with_detail, limit=limit)

        stamp = datetime.now().strftime('%Y%m%d')
        fname = f"daily-hot-{date_str}-{platform or 'all'}-{stamp}"

        if fmt == 'csv':
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([label for _, label in columns])
            for r in rows:
                writer.writerow([r.get(key) or '' for key, _ in columns])
            resp = make_response('\ufeff' + buf.getvalue())
            resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
            resp.headers['Content-Disposition'] = \
                f'attachment; filename="{fname}.csv"'
            return resp

        if fmt != 'json':
            return jsonify({'ok': False,
                            'message': 'format 只支持 json 或 csv'}), 400

        payload = {
            'ok': True,
            'date': date_str,
            'platform': platform,
            'keyword': keyword,
            'with_detail': with_detail,
            'total': len(rows),
            'columns': [{'key': k, 'label': label} for k, label in columns],
            'items': rows,
        }
        resp = make_response(jsonify(payload))
        resp.headers['Content-Disposition'] = \
            f'attachment; filename="{fname}.json"'
        return resp

    @app.route('/api/logs')
    def api_logs():
        logs = db.recent_logs(limit=20)
        for l in logs:
            l['created_at'] = str(l['created_at'])
        return jsonify({'logs': logs})

    @app.route('/healthz')
    def healthz():
        return jsonify({'status': 'ok'})

    # ============================================================ 定时任务
    scheduler = BackgroundScheduler(daemon=True)

    def daily_job():
        """每日定时：先抓取，再推送 Telegram + 邮件（失败不影响抓取）。"""
        try:
            res = crawler.crawl_and_save()
            dt = res.get('date')
        except Exception:
            log.exception("定时抓取失败")
            return
        site = telegram_push.site_url()
        try:
            if telegram_push.get_config()['token']:
                chat_id = telegram_push.get_config()['chat_id']
                if chat_id:
                    telegram_push.push_insight(chat_id, dt)
                telegram_push.push_all_users(dt)
        except Exception:
            log.exception("Telegram 定时推送失败")
        try:
            mail_push.push_all_users(dt, site_url=site)
        except Exception:
            log.exception("邮件定时推送失败")

    def start_scheduler():
        scheduler.add_job(
            daily_job, 'cron',
            hour=Config.CRAWL_HOUR, minute=Config.CRAWL_MINUTE,
            id='daily_crawl', replace_existing=True,
            misfire_grace_time=3600)
        scheduler.start()
        log.info("定时抓取任务已注册：每天 %02d:%02d",
                 Config.CRAWL_HOUR, Config.CRAWL_MINUTE)

    def _next_run_str():
        job = scheduler.get_job('daily_crawl')
        return str(job.next_run_time) if job and job.next_run_time else None

    app.start_scheduler = start_scheduler
    return app


app = create_app()


if __name__ == '__main__':
    def bootstrap_crawl():
        """首次抓取：后台线程执行，不阻塞 Web 服务启动。"""
        try:
            crawler.crawl_and_save()
        except Exception:
            log.exception("首次抓取失败")

    if '--init' in sys.argv or not db.get_latest_date():
        log.info("数据库为空或指定 --init，后台执行首次抓取...")
        threading.Thread(target=bootstrap_crawl, daemon=True).start()

    app.start_scheduler()

    # ---- 启动 Web 服务（IPv6 优先，失败自动回退 IPv4）----
    host = Config.HOST
    listen_note = ''
    if host == '::':
        try:
            import socket
            socket.has_ipv6            # 触发检测
            addr = socket.getaddrinfo('::1', None, socket.AF_INET6)
            if not addr:
                raise OSError('no ipv6')
            listen_note = '(IPv6 + IPv4 双栈)'
        except Exception as e:
            log.warning("IPv6 不可用（%s），回退监听 IPv4 0.0.0.0", e)
            host = '0.0.0.0'
            listen_note = '(已回退 IPv4)'
    elif host == '0.0.0.0':
        listen_note = '(仅 IPv4)'

    site = telegram_push.site_url()
    log.info("每日热点网站启动: http://%s:%d %s", host, Config.PORT, listen_note)
    if site:
        log.info("手机访问地址: %s", site)
    else:
        log.info("提示：设置 SITE_URL 环境变量（或网站 /settings 中填写）可让推送链接指向可访问地址")
    app.run(host=host, port=Config.PORT, debug=False, threaded=True)
