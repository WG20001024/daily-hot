<?xml version="1.0" encoding="UTF-8"?>
<!--
  RSS 浏览器美化样式表：
  仅当用浏览器直接打开 /rss.xml 时生效（把 XML 渲染为可读页面），
  RSS 阅读器抓取时忽略本文件，订阅功能不受影响。
-->
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:atom="http://www.w3.org/2005/Atom">
  <xsl:output method="html" encoding="UTF-8" indent="yes"/>
  <xsl:template match="/rss/channel">
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
      <title><xsl:value-of select="title"/></title>
      <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
          background: #f5f6f8; color: #1f2329; line-height: 1.6;
        }
        .wrap { max-width: 860px; margin: 0 auto; padding: 32px 20px 60px; }
        .badge {
          display: inline-flex; align-items: center; gap: 6px;
          background: #fdeceb; color: #d54941; border: 1px solid #ffd8d3;
          border-radius: 999px; padding: 3px 14px; font-size: 13px; font-weight: 600;
        }
        h1 { font-size: 24px; margin: 12px 0 4px; }
        .desc { color: #646a73; font-size: 14px; margin-bottom: 16px; }
        .tip {
          background: #e8f3ff; border: 1px solid #91caff; color: #0958d9;
          border-radius: 10px; padding: 10px 14px; font-size: 13.5px; margin-bottom: 24px;
        }
        .tip code { background: #fff; border-radius: 4px; padding: 1px 6px; }
        .items { list-style: none; display: flex; flex-direction: column; gap: 10px; }
        .items li {
          background: #fff; border-radius: 12px; padding: 14px 18px;
          box-shadow: 0 1px 3px rgba(31,35,41,.06), 0 4px 12px rgba(31,35,41,.05);
        }
        .items a { color: #1f2329; font-weight: 600; font-size: 15px; text-decoration: none; }
        .items a:hover { color: #d54941; }
        .meta { font-size: 12.5px; color: #8f959e; margin: 4px 0; font-variant-numeric: tabular-nums; }
        .meta .cat { background: #f5f6f8; border-radius: 999px; padding: 1px 10px; margin-right: 8px; }
        .items p { font-size: 13.5px; color: #646a73; margin-top: 4px; }
        .foot { text-align: center; color: #8f959e; font-size: 12.5px; margin-top: 32px; }
      </style>
    </head>
    <body>
      <div class="wrap">
        <span class="badge">📡 RSS 订阅源</span>
        <h1><xsl:value-of select="title"/></h1>
        <p class="desc"><xsl:value-of select="description"/></p>
        <div class="tip">
          这是本站的 RSS 订阅源。把浏览器地址栏的链接复制到 RSS 阅读器
          （Feedly、Inoreader、NetNewsWire、RSSHub Radar 等）即可自动追更；
          也可以加参数过滤：<code>?platform=weibo</code> 指定平台、<code>?date=2026-10-17</code> 指定日期、<code>?limit=100</code> 指定条数。
          如果你的浏览器没有自动渲染此页面，可到 <a href="/feed" style="color:#0958d9;text-decoration:underline;">/feed</a> 查看 HTML 预览版。
        </div>
        <ol class="items">
          <xsl:for-each select="item">
            <li>
              <a>
                <xsl:attribute name="href"><xsl:value-of select="link"/></xsl:attribute>
                <xsl:attribute name="target">_blank</xsl:attribute>
                <xsl:attribute name="rel">noopener</xsl:attribute>
                <xsl:value-of select="title"/>
              </a>
              <div class="meta">
                <span class="cat"><xsl:value-of select="category"/></span>
                <xsl:value-of select="pubDate"/>
              </div>
              <p><xsl:value-of select="description"/></p>
            </li>
          </xsl:for-each>
        </ol>
        <p class="foot">Powered by Daily Hot · Python + Flask + MySQL</p>
      </div>
    </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
