# -*- coding: utf-8 -*-
"""
邮件推送：把每日智能洞察通过 SMTP 发送到邮箱。

零第三方依赖，使用标准库 smtplib / email。
- QQ 邮箱：服务器 smtp.qq.com，端口 465（SSL），登录需用「授权码」而非 QQ 密码。
- 未配置 SMTP 或发送失败时返回 (ok, error)，由调用方提示，不影响抓取主流程。
"""
import logging
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

from config import Config
import db
import ai_summary

log = logging.getLogger('app.mail')


def get_config():
    """读取 SMTP 配置：网页设置优先，环境变量兜底。"""
    return {
        'host': db.get_setting('SMTP_HOST') or Config.SMTP_HOST,
        'port': int(db.get_setting('SMTP_PORT') or Config.SMTP_PORT or 465),
        'user': db.get_setting('SMTP_USER') or Config.SMTP_USER,
        'password': db.get_setting('SMTP_PASSWORD') or Config.SMTP_PASSWORD,
        'sender': db.get_setting('SMTP_SENDER') or Config.SMTP_SENDER,
        'use_ssl': (db.get_setting('SMTP_USE_SSL') or '1') == '1',
    }


def send_mail(to_addr, subject, html_body, text_body=None):
    """
    发送一封邮件。返回 (ok: bool, error: str|None)。
    to_addr 形如 123456@qq.com
    """
    cfg = get_config()
    if not (cfg['host'] and cfg['user'] and cfg['password']):
        return False, '未配置 SMTP（主机/账号/授权码）'
    if not to_addr or '@' not in to_addr:
        return False, f'收件地址无效：{to_addr}'
    sender = cfg['sender'] or cfg['user']

    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header(Config.SITE_NAME, 'utf-8')), sender))
    msg['To'] = to_addr
    msg['Date'] = formatdate(localtime=True)
    msg.attach(MIMEText(text_body or '请使用支持 HTML 的邮件客户端查看。',
                        'plain', 'utf-8'))
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        if cfg['use_ssl']:
            server = smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=20)
        else:
            server = smtplib.SMTP(cfg['host'], cfg['port'], timeout=20)
            server.starttls()
        try:
            server.login(cfg['user'], cfg['password'])
            server.sendmail(sender, [to_addr], msg.as_string())
        finally:
            server.quit()
        log.info('邮件已发送至 %s（主题：%s）', to_addr, subject)
        return True, None
    except smtplib.SMTPAuthenticationError as e:
        return False, f'认证失败（{e}）：请确认使用「授权码」而非 QQ 密码'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def build_digest(date, summary=None, topics=None, sentiment=None,
                 resonance=None, site_url=''):
    """把智能洞察渲染为 HTML 邮件正文。"""
    c = (sentiment or {}).get('counts', {})
    rising = [t for t in (topics or [])
              if t['lifecycle']['label'] in ('rising', 'peak')]

    rows = []
    if c:
        total = c.get('positive', 0) + c.get('neutral', 0) + c.get('negative', 0)
        pct = lambda n: (100.0 * n / total) if total else 0
        rows.append(f"""
        <tr><td colspan="2" style="padding:8px 0;border-bottom:1px solid #eee;">
          <span style="color:#2f9e44;">● 正面 {c.get('positive', 0)}（{pct(c.get('positive', 0)):.0f}%）</span>
          &nbsp;&nbsp;<span style="color:#8f959e;">● 中性 {c.get('neutral', 0)}（{pct(c.get('neutral', 0)):.0f}%）</span>
          &nbsp;&nbsp;<span style="color:#d54941;">● 负面 {c.get('negative', 0)}（{pct(c.get('negative', 0)):.0f}%）</span>
        </td></tr>""")

    if rising:
        items = ''.join(
            f'<li style="margin:6px 0;">{t["title"]}'
            f'<span style="color:#8f959e;font-size:12px;"> · {t["lifecycle"]["cn"]}'
            f' · 在榜 {t["days"]} 天</span></li>'
            for t in rising[:8])
        rows.append(f'<tr><td colspan="2" style="padding:14px 0;">'
                    f'<b style="color:#d54941;">🔥 升温话题</b><ul>{items}</ul></td></tr>')

    if resonance:
        chips = ''.join(
            f'<span style="display:inline-block;background:#f5f6f8;border:1px solid #e5e6eb;'
            f'border-radius:999px;padding:4px 10px;margin:4px 4px 4px 0;font-size:12px;">'
            f'<b style="color:#d54941;">{r["platform_count"]}</b> {r["title"]}</span>'
            for r in resonance[:8])
        rows.append(f'<tr><td colspan="2" style="padding:14px 0;">'
                    f'<b style="color:#d54941;">🌐 跨平台共振</b><div>{chips}</div></td></tr>')

    pos = (sentiment or {}).get('positive', [])[:5]
    neg = (sentiment or {}).get('negative', [])[:5]
    if pos:
        items = ''.join(f'<li style="margin:6px 0;">{i["title"]}</li>' for i in pos)
        rows.append(f'<tr><td colspan="2" style="padding:14px 0;">'
                    f'<b style="color:#2f9e44;">👍 正向热点</b><ul>{items}</ul></td></tr>')
    if neg:
        items = ''.join(f'<li style="margin:6px 0;">{i["title"]}</li>' for i in neg)
        rows.append(f'<tr><td colspan="2" style="padding:14px 0;">'
                    f'<b style="color:#d54941;">⚠️ 负面 / 争议</b><ul>{items}</ul></td></tr>')

    btn = ''
    if site_url:
        btn = (f'<p style="margin:22px 0;">'
               f'<a href="{site_url}" style="background:#d54941;color:#fff;'
               f'text-decoration:none;padding:11px 22px;border-radius:8px;'
               f'display:inline-block;font-size:14px;">在网页查看完整洞察 →</a></p>')

    summary_text = (summary or {}).get('text', '')

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:20px;background:#f5f6f8;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
  color:#1f2329;line-height:1.7;">
<div style="max-width:640px;margin:0 auto;background:#fff;border-radius:14px;
  padding:28px;box-shadow:0 2px 10px rgba(0,0,0,.05);">
  <h1 style="margin:0 0 4px;font-size:20px;">📰 每日热点 · 智能洞察</h1>
  <p style="margin:0 0 18px;color:#8f959e;font-size:13px;">{date}</p>
  <div style="background:#fdeceb;border-left:3px solid #d54941;border-radius:6px;
    padding:14px 16px;margin-bottom:10px;">{summary_text}</div>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">{''.join(rows)}</table>
  {btn}
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0 14px;">
  <p style="color:#8f959e;font-size:12px;margin:0;">
    每日热点自动推送 · 由 {Config.SITE_NAME} 生成<br>
    <a href="{site_url or '#'}" style="color:#d54941;">{site_url or ''}</a>
  </p>
</div>
</body></html>"""


def send_digest(to_addr, date=None, site_url=''):
    """生成并发送某日洞察邮件。返回 (ok, error)。"""
    date = date or str(db.get_latest_date())
    topics = db.lifecycle_topics(days=7, limit=10)
    sentiment = db.sentiment_stats(date)
    resonance = db.resonance_items(date)[:8]
    summary = ai_summary.generate_summary(
        date, topics=topics, resonance=resonance, sentiment=sentiment)
    html = build_digest(
        date, summary=summary, topics=topics, sentiment=sentiment,
        resonance=resonance, site_url=site_url)
    subject = f'【{Config.SITE_NAME}】{date} 每日热点智能洞察'
    return send_mail(to_addr, subject, html)


def push_all_users(date=None, site_url=''):
    """
    每日抓取后向所有已填写邮箱的用户推送。
    返回 {'sent': int, 'failed': int}
    """
    cfg = get_config()
    if not (cfg['host'] and cfg['user'] and cfg['password']):
        return {'sent': 0, 'failed': 0, 'skipped': 'no_smtp'}
    date = date or str(db.get_latest_date())
    targets = [(u['username'], u['email']) for u in db.list_users_with_email()]
    sent = failed = 0
    for username, email in targets:
        try:
            ok, err = send_digest(email, date, site_url)
            if ok:
                sent += 1
            else:
                failed += 1
                log.warning('邮件推送至 %s 失败：%s', username, err)
        except Exception as e:
            failed += 1
            log.warning('邮件推送至 %s 异常：%s', username, e)
    log.info('邮件推送完成：成功 %d，失败 %d', sent, failed)
    return {'sent': sent, 'failed': failed}
