# -*- coding: utf-8 -*-
"""
每日热点网站 —— 新闻详情抓取器

对每条热点的链接发起一次请求，解析详情页，提取：
  - content       正文摘要（若干 <p> 段落拼接，截断到配置长度）
  - author        作者 / 来源账号（meta author、og:article:author 等）
  - published_at  发布时间（meta article:published_time 等，解析为 MySQL DATETIME）
  - cover_url     封面图（og:image）
  - source_site   来源站点（域名）

设计要点：
1. 仅用标准库 html.parser，无额外依赖；
2. 只保存元信息与摘要片段（聚合器惯例，不镜像全文）；
3. 并发限量抓取，单条失败不影响其他，全部结果写入 crawl_logs；
4. 已在库中的详情不重复抓取（由 db.items_missing_detail 保证）。
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

import db
from config import Config

log = logging.getLogger('detail')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)\s*=\s*["\']([^"\']+)["\'][^>]*'
    r'content\s*=\s*["\']([^"\']*)["\']', re.I)
_META_RE_REV = re.compile(
    r'<meta[^>]+content\s*=\s*["\']([^"\']*)["\'][^>]*'
    r'(?:property|name)\s*=\s*["\']([^"\']+)["\']', re.I)


class _TextExtractor(HTMLParser):
    """提取 <p> 段落文本，忽略 script/style/noscript。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_p = 0
        self.buf = []
        self.paras = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip_depth += 1
        elif tag == 'p' and self.skip_depth == 0:
            self.in_p += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.skip_depth:
            self.skip_depth -= 1
        elif tag == 'p' and self.in_p:
            self.in_p -= 1
            text = ''.join(self.buf).strip()
            if text:
                self.paras.append(text)
            self.buf = []

    def handle_data(self, data):
        if self.skip_depth == 0 and self.in_p:
            self.buf.append(data)


def _parse_time(value):
    """把各种格式的时间串解析为 'YYYY-MM-DD HH:MM:SS'，失败返回 None。"""
    if not value:
        return None
    v = value.strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d'):
        try:
            dt = datetime.strptime(v.replace('+08:00', '+0800'), fmt)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    m = re.match(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', v)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} 00:00:00"
    return None


def fetch_detail(url, timeout=None):
    """
    抓取单个链接的详情。成功返回 dict，失败返回 None（异常向上抛出由调用方记录）。
    """
    timeout = timeout or Config.DETAIL_TIMEOUT
    resp = requests.get(url, timeout=timeout, headers={
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }, allow_redirects=True)
    resp.raise_for_status()
    ctype = resp.headers.get('Content-Type', '')
    if 'html' not in ctype:
        raise ValueError(f'非 HTML 内容: {ctype}')

    html = resp.text
    # ---- meta 提取 ----
    metas = {}
    for m in _META_RE.finditer(html):
        metas[m.group(1).strip().lower()] = m.group(2).strip()
    for m in _META_RE_REV.finditer(html):
        metas.setdefault(m.group(2).strip().lower(), m.group(1).strip())

    def _meta(*keys):
        for k in keys:
            v = metas.get(k)
            if v:
                return v
        return None

    # ---- 正文段落 ----
    parser = _TextExtractor()
    try:
        parser.feed(html[:600000])  # 只解析前 600KB，避免超大页面
        parser.close()
    except Exception:
        pass
    paras = [p for p in parser.paras if len(p) >= 15][:40]
    content = '\n'.join(paras).strip()
    if not content:
        content = _meta('description', 'og:description') or ''
    if len(content) > Config.DETAIL_MAX_CHARS:
        content = content[:Config.DETAIL_MAX_CHARS].rstrip() + '…'

    author = _meta('author', 'article:author', 'og:article:author',
                   'twitter:creator')
    published_at = _parse_time(_meta(
        'article:published_time', 'og:article:published_time',
        'publishdate', 'pubdate', 'date'))
    cover_url = _meta('og:image', 'twitter:image')
    source_site = urlparse(url).netloc or None

    if not content and not author and not published_at:
        raise ValueError('未能从页面提取到有效详情信息')

    return {
        'content': content or None,
        'author': author,
        'published_at': published_at,
        'cover_url': cover_url,
        'source_site': source_site,
    }


def fetch_details_for_date(date, per_platform=None):
    """
    为某日的热点批量抓取详情（只抓缺失的，每平台限量）。
    返回汇总 dict。
    """
    per_platform = per_platform or Config.DETAIL_TOP_N
    pending = db.items_missing_detail(date, per_platform=per_platform)
    if not pending:
        return {'date': str(date), 'fetched': 0, 'succeeded': 0,
                'failed': 0, 'message': '没有待抓取详情的条目'}

    log.info("开始抓取 %d 条热点详情（日期 %s）", len(pending), date)
    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=Config.DETAIL_CONCURRENCY) as pool:
        futures = {pool.submit(fetch_detail, it['url']): it for it in pending}
        for fut in as_completed(futures):
            it = futures[fut]
            try:
                detail = fut.result()
                db.save_detail(it['id'], detail, status='success')
                ok += 1
            except Exception as e:
                db.save_detail(it['id'], None, status='failed')
                fail += 1
                log.warning("详情抓取失败 %s: %s (%s)",
                            it['url'], type(e).__name__, e)

    db.write_log('detail', 'detail-fetch', 'success' if ok else 'failed',
                 ok, f'成功 {ok} / 失败 {fail}')
    summary = {'date': str(date), 'fetched': len(pending),
               'succeeded': ok, 'failed': fail}
    log.info("详情抓取完成: %s", summary)
    return summary
