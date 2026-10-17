/* ============ 每日热点 —— 交互脚本 ============ */

// 平台筛选
document.getElementById('platformTabs')?.addEventListener('click', (e) => {
  const btn = e.target.closest('.tab');
  if (!btn) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  const platform = btn.dataset.platform;
  document.querySelectorAll('.platform-card').forEach(card => {
    card.style.display = (platform === 'all' || card.dataset.platform === platform)
      ? '' : 'none';
  });
});

// 关键词搜索（前端即时过滤）
function filterItems() {
  const kw = document.getElementById('searchInput').value.trim().toLowerCase();
  document.querySelectorAll('.hot-item').forEach(item => {
    item.style.display = !kw || item.dataset.title.includes(kw) ? '' : 'none';
  });
  // 隐藏没有可见条目的平台卡片
  document.querySelectorAll('.platform-card').forEach(card => {
    const visible = [...card.querySelectorAll('.hot-item')]
      .some(i => i.style.display !== 'none');
    card.style.display = visible ? '' : 'none';
  });
}

// 日期跳转
function gotoDate(value) {
  if (value) location.href = '/?date=' + value;
}

// 手动触发抓取
async function refreshData() {
  const btn = document.getElementById('refreshBtn');
  const chip = document.getElementById('sourceChip');
  btn.disabled = true;
  btn.textContent = '⏳ 抓取中…';
  try {
    const resp = await fetch('/api/refresh', { method: 'POST' });
    const data = await resp.json();
    if (data.ok) {
      chip.textContent = data.source;
      showToast(`✅ 抓取完成：${data.platforms} 个平台 / ${data.items} 条（${data.source}）`, 'info');
      setTimeout(() => location.reload(), 1200);
    } else {
      showToast('❌ 抓取失败：' + (data.message || '未知错误'), 'warn');
    }
  } catch (err) {
    showToast('❌ 请求失败：' + err.message, 'warn');
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 立即抓取';
  }
}

// 轻量提示条
function showToast(text, type = 'info') {
  document.querySelector('.banner')?.remove();
  const div = document.createElement('div');
  div.className = 'banner ' + (type === 'warn' ? 'banner-warn' : 'banner-info');
  div.textContent = text;
  const list = document.getElementById('hotList');
  (list || document.querySelector('main')).parentNode.insertBefore(div, list);
  setTimeout(() => div.remove(), 6000);
}

// 收藏切换
async function toggleFavorite(btn) {
  const itemId = btn.dataset.item;
  try {
    const resp = await fetch('/api/favorite/' + itemId, { method: 'POST' });
    if (resp.status === 401) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
      return;
    }
    const data = await resp.json();
    if (data.ok) {
      btn.classList.toggle('is-active', data.favorited);
      btn.textContent = btn.classList.contains('fav-btn-lg')
        ? (data.favorited ? '★ 已收藏' : '★ 收藏')
        : '★';
      btn.title = data.favorited ? '取消收藏' : '收藏此热点';
      showToast(data.favorited ? '⭐ 已收藏' : '已取消收藏');
    }
  } catch (err) {
    showToast('❌ 操作失败：' + err.message, 'warn');
  }
}
