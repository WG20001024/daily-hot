/* 每日热点 —— Service Worker
 * 策略：
 *   - 静态资源（CSS/JS/图标）：缓存优先，后台更新（stale-while-revalidate）
 *   - 页面导航 HTML：网络优先，失败时回落到缓存（离线可看历史页面）
 *   - API 与 RSS：不缓存，保证数据新鲜
 * 版本号变更即触发全量更新。
 */
const VERSION = 'v1';
const STATIC_CACHE = `dh-static-${VERSION}`;
const PAGE_CACHE = `dh-page-${VERSION}`;

const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/apple-touch-icon.png',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((k) => k !== STATIC_CACHE && k !== PAGE_CACHE)
          .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // 外部资源不拦截

  // API / RSS 永不缓存
  if (url.pathname.startsWith('/api/') || url.pathname === '/rss.xml') return;

  // 页面导航：网络优先 -> 回落缓存
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(PAGE_CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || offlineFallback()))
    );
    return;
  }

  // 静态资源：缓存优先 -> 后台更新
  event.respondWith(
    caches.match(req).then((hit) => {
      const network = fetch(req)
        .then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => hit);
      return hit || network;
    })
  );
});

async function offlineFallback() {
  const cache = await caches.open(PAGE_CACHE);
  const cached = await cache.match('/');
  if (cached) return cached;
  return new Response(
    '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>离线 · 每日热点</title></head>' +
    '<body style="font-family:system-ui;padding:40px;text-align:center">' +
    '<h1>当前处于离线状态</h1>' +
    '<p>请检查网络连接后重试。</p></body></html>',
    { headers: { 'Content-Type': 'text/html; charset=utf-8' }, status: 503 }
  );
}
