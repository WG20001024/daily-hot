# -*- coding: utf-8 -*-
"""
每日热点网站 —— RSS 输出（RSS 2.0）

- 纯标准库实现，无第三方依赖
- /rss.xml           全部平台最新一期
- /rss.xml?platform= 指定平台
- /rss.xml?date=     指定日期
- /rss.xml?limit=    条数上限（默认 50，最大 200）
"""
from xml.sax.saxutils import escape

from config import Config


def _rfc822(dt):
    """把 datetime/date 转为 RFC 822 时间串（RSS pubDate 要求）。"""
    if dt is None:
        return ''
    if hasattr(dt, 'strftime'):
        # 英文星期/月份缩写，RSS 阅读器普遍要求
        return dt.strftime('%a, %d %b %Y %H:%M:%S +0800')
    return str(dt)


def _plain(text):
    """去掉换行与多余空白，便于放入单行标签。"""
    return ' '.join((text or '').split())


def build_rss(items, title, site_url, description, self_url=None, language='zh-CN'):
    """
    生成 RSS 2.0 文档字符串。
    items: 条目列表（db.query_items 的返回结构）
    """
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           # 浏览器直接打开时渲染为可读页面；RSS 阅读器会忽略此指令
           '<?xml-stylesheet type="text/xsl" href="/static/rss.xsl"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
           '<channel>',
           f'  <title>{escape(title)}</title>',
           f'  <link>{escape(site_url)}</link>',
           f'  <description>{escape(description)}</description>',
           f'  <language>{language}</language>',
           f'  <generator>Daily Hot (Python + Flask + MySQL)</generator>']
    if self_url:
        out.append(f'  <atom:link href="{escape(self_url)}" rel="self" '
                   'type="application/rss+xml"/>')

    for it in items:
        plat = it.get('platform_name') or ''
        rank = it.get('rank_no')
        # 标题带平台与排名前缀，便于在阅读器里快速辨识
        prefix = f'[{plat} #{rank}] ' if plat else ''
        item_title = prefix + (it.get('title') or '')

        summary = _plain(it.get('summary'))
        heat = it.get('heat_display') or it.get('heat_value')
        desc_parts = []
        if summary:
            desc_parts.append(summary)
        if heat:
            desc_parts.append(f'热度：{heat}')
        desc_parts.append(f'平台：{plat}　排名：{rank}　日期：{it.get("snapshot_date")}')
        description_text = ' ｜ '.join(p for p in desc_parts if p)

        link = it.get('url') or f'{site_url}/item/{it.get("id")}'
        guid = f'{site_url}/item/{it.get("id")}'
        pub = _rfc822(it.get('crawled_at') or it.get('snapshot_date'))

        out.append('  <item>')
        out.append(f'    <title>{escape(_plain(item_title))}</title>')
        out.append(f'    <link>{escape(link)}</link>')
        out.append(f'    <guid isPermaLink="false">{escape(guid)}</guid>')
        if pub:
            out.append(f'    <pubDate>{escape(pub)}</pubDate>')
        out.append(f'    <description>{escape(description_text)}</description>')
        if plat:
            out.append(f'    <category>{escape(plat)}</category>')
        out.append('  </item>')

    out += ['</channel>', '</rss>']
    return '\n'.join(out)


def feed_meta(platform_name=None, date_str=None):
    """根据过滤条件生成 feed 的标题与描述。"""
    base = Config.SITE_NAME
    if platform_name and date_str:
        title = f'{base} · {platform_name} · {date_str}'
        desc = f'{platform_name} {date_str} 的热榜快照'
    elif platform_name:
        title = f'{base} · {platform_name}'
        desc = f'{platform_name} 每日热榜'
    elif date_str:
        title = f'{base} · {date_str}'
        desc = f'{date_str} 全平台热榜快照'
    else:
        title = f'{base} · 全平台每日热榜'
        desc = '聚合微博、知乎、百度、B站、抖音等平台的每日热榜'
    return title, desc
