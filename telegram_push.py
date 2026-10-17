# -*- coding: utf-8 -*-
"""
Telegram 推送：基于 Bot API 把每日洞察 / 日报推送到 Telegram。

零额外依赖，使用标准库 urllib 调用 api.telegram.org。
未配置 TG_BOT_TOKEN 或网络不可达时，函数返回 (ok, error)，由调用方决定提示，不影响主流程。
"""
import json
import logging
import urllib.request

from config import Config
import db
import ai_summary

log = logging.getLogger('app.telegram')

API_URL = 'https://api.telegram.org/bot{token}/sendMessage'
# Telegram 支持的 HTML 标签（用于消息排版）
_MAX_LEN = 4000


def get_config():
    """合并站点设置与环境变量：网页配置优先，未配置时回退 env。"""
    token = (db.get_setting('TG_BOT_TOKEN')
             or getattr(Config, 'TG_BOT_TOKEN', ''))
    chat_id = (db.get_setting('TG_CHAT_ID')
               or getattr(Config, 'TG_CHAT_ID', ''))
    return {'token': token, 'chat_id': chat_id}


def site_url():
    """
    生成用于推送链接的站点地址，正确处理 IPv6 字面量。
    IPv6 在 URL 中必须写成 http://[addr]:port/...；
    已带方括号的输入保持原样，避免重复包裹。
    """
    url = (db.get_setting('SITE_URL') or getattr(Config, 'SITE_URL', '') or '')
    if not url:
        return ''
    url = url.rstrip('/')
    scheme, sep, rest = url.partition('://')
    if not sep:
        return url
    hostport, slash, path = rest.partition('/')
    # 已是 [ipv6]:port 形式，直接返回
    if hostport.startswith('['):
        return url
    # 多个冒号 => IPv6 字面量，需要补方括号
    if hostport.count(':') > 1:
        hostport = '[' + hostport + ']'
    return f'{scheme}://{hostport}{slash}{path}'


def send_message(chat_id, text, parse_mode='HTML', token=None):
    """向指定 chat_id 发送一条消息。返回 (ok: bool, error: str|None)。"""
    token = token or get_config()['token']
    if not token:
        return False, '未配置 TG_BOT_TOKEN'
    text = (text or '').strip()
    if not text:
        return False, '消息为空'
    if len(text) > _MAX_LEN:
        text = text[:_MAX_LEN] + '…'
    body = json.dumps({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True,
    }).encode('utf-8')
    req = urllib.request.Request(
        API_URL.format(token=token),
        data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if data.get('ok'):
            return True, None
        return False, data.get('description', 'unknown error')
    except Exception as e:  # 网络/鉴权/解析异常 -> 由调用方提示，不影响主流程
        return False, f'{type(e).__name__}: {e}'


def _esc(s):
    """转义用户文本中的 HTML 特殊字符（消息里我们自己控制标签）。"""
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def format_insight(date, summary=None, topics=None, sentiment=None,
                   resonance=None, site_url=''):
    """把智能洞察拼成 Telegram 友好（HTML）文本。标题合并为一行摘要。"""
    rising = [t for t in (topics or [])
              if t['lifecycle']['label'] in ('rising', 'peak')]
    c = (sentiment or {}).get('counts', {})

    # ---- 合并标题：日期 + 关键指标压成一行，避免消息顶部标题过长 ----
    bits = [date]
    if c:
        bits.append('正{}/中{}/负{}'.format(
            c.get('positive', 0), c.get('neutral', 0), c.get('negative', 0)))
    if rising:
        bits.append(f'升温 {len(rising)}')
    if resonance:
        bits.append(f'共振 {len(resonance)}')
    lines = ['📰 <b>每日热点 · 智能洞察</b> — ' + _esc(' · '.join(bits))]

    if summary and summary.get('text'):
        lines.append('')
        lines.append(_esc(summary['text']))
    if c:
        lines.append('')
        lines.append(
            f'😊 情绪分布：正面 {c.get("positive", 0)} · 中性 '
            f'{c.get("neutral", 0)} · 负面 {c.get("negative", 0)}')
    if rising:
        lines.append('')
        lines.append('🔥 <b>升温话题</b>')
        for t in rising[:5]:
            lines.append(f'· {_esc(t["title"])}（{t["lifecycle"]["cn"]}）')
    if resonance:
        lines.append('')
        lines.append('🌐 <b>跨平台共振</b>')
        for r in resonance[:4]:
            lines.append(f'· {_esc(r["title"])}（{r["platform_count"]} 个平台）')
    if site_url:
        lines.append('')
        lines.append(f'<a href="{_esc(site_url)}">在网页查看完整洞察 →</a>')
    return '\n'.join(lines)


def push_insight(chat_id, date=None, token=None):
    """生成并推送某日智能洞察到指定 chat_id。返回 (ok, error)。"""
    date = date or str(db.get_latest_date())
    topics = db.lifecycle_topics(days=7, limit=10)
    sentiment = db.sentiment_stats(date)
    resonance = db.resonance_items(date)[:6]
    summary = ai_summary.generate_summary(
        date, topics=topics, resonance=resonance, sentiment=sentiment)
    text = format_insight(
        date, summary=summary, topics=topics, sentiment=sentiment,
        resonance=resonance, site_url=site_url())
    return send_message(chat_id, text, token=token)


def push_all_users(date=None):
    """
    每日抓取后，向所有已绑定 Telegram 的用户推送当日智能洞察。
    返回 {'sent': int, 'failed': int}（未配置 token 时 sent=failed=0）。
    """
    if not getattr(Config, 'TG_BOT_TOKEN', ''):
        return {'sent': 0, 'failed': 0, 'skipped': 'no_token'}
    date = date or str(db.get_latest_date())
    if not get_config()['token']:
        return {'sent': 0, 'failed': 0, 'skipped': 'no_token'}
    users = db.list_users_with_tg()
    sent = failed = 0
    for u in users:
        try:
            ok, err = push_insight(u['tg_chat_id'], date)
            if ok:
                sent += 1
            else:
                failed += 1
                log.warning('Telegram 推送给用户 %s 失败：%s', u['username'], err)
        except Exception as e:
            failed += 1
            log.warning('Telegram 推送给用户 %s 异常：%s', u['username'], e)
    log.info('Telegram 群推完成：成功 %d，失败 %d', sent, failed)
    return {'sent': sent, 'failed': failed}
