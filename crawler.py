# -*- coding: utf-8 -*-
"""
每日热点网站 —— 热点抓取器

设计要点：
1. 多数据源依次尝试（免费热榜聚合 API），任一源成功即停止；
2. 每个适配器对返回结构做防御性解析，兼容常见字段命名；
3. 全部源不可达时，自动降级为内置离线演示数据，保证站点永远有内容；
4. 抓取结果按平台归一化（排名、热度、链接）后幂等写入 MySQL，并记录抓取日志。
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests

import db
from config import Config

log = logging.getLogger(__name__)

# 平台代码 -> 各数据源中可能出现的名称（用于把第三方数据映射到本地平台）
PLATFORM_ALIASES = {
    'weibo':    ['微博', 'weibo', '微博热搜'],
    'zhihu':    ['知乎', 'zhihu', '知乎热榜'],
    'baidu':    ['百度', 'baidu', '百度热搜', '百度热点'],
    'bilibili': ['哔哩哔哩', 'bilibili', 'B站', 'BILI', '哔哩哔哩热搜'],
    'douyin':   ['抖音', 'douyin', '抖音热搜'],
    'toutiao':  ['今日头条', 'toutiao', '头条'],
    'github':   ['github', 'GitHub', 'Github'],
    'v2ex':     ['v2ex', 'V2EX', 'V2EX 最热'],
    'ithome':   ['IT之家', 'ithome', 'ITHome'],
    'sspai':    ['少数派', 'sspai', 'Sspai'],
}


# ================================================================ 数据源适配器
def _norm_alias(name):
    n = (name or '').strip()
    for code, aliases in PLATFORM_ALIASES.items():
        if n in aliases:
            return code
    return None


def _parse_heat(v):
    """把 '452.6万' / 12345 / '1,234' 等统一转为整数热度值，并生成展示串。"""
    if v is None:
        return 0, None
    if isinstance(v, (int, float)):
        return int(v), _format_heat(int(v))
    s = str(v).strip().replace(',', '')
    mult = 1
    if s.endswith('万'):
        mult, s = 10000, s[:-1]
    elif s.endswith('亿'):
        mult, s = 100000000, s[:-1]
    try:
        return int(float(s) * mult), str(v).strip()
    except ValueError:
        return 0, str(v).strip()


def _format_heat(n):
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def fetch_vvhan():
    """数据源 1：vvhan 热榜聚合（一次返回多平台）。"""
    url = 'https://api.vvhan.com/api/hotlist/all'
    r = requests.get(url, timeout=Config.REQUEST_TIMEOUT,
                     headers={'User-Agent': 'Mozilla/5.0'})
    r.raise_for_status()
    data = r.json()
    # 兼容两种结构: {"data": [{"name":..., "data":[...]}, ...]} 或 {"data": {"weibo": [...]}}
    nodes = data.get('data') if isinstance(data, dict) else data
    result = {}
    if isinstance(nodes, list):
        for node in nodes:
            code = _norm_alias(node.get('name'))
            items = node.get('data') or node.get('list') or []
            if code and isinstance(items, list):
                result[code] = items
    elif isinstance(nodes, dict):
        for name, items in nodes.items():
            code = _norm_alias(name)
            if code and isinstance(items, list):
                result[code] = items
    return result


def fetch_tenapi():
    """数据源 2：tenapi（按平台单独接口，并发聚合调用）。"""
    endpoints = {
        'weibo': 'https://tenapi.cn/v2/weibohot',
        'zhihu': 'https://tenapi.cn/v2/zhihuhot',
        'baidu': 'https://tenapi.cn/v2/baiduhot',
        'bilibili': 'https://tenapi.cn/v2/bilihot',
        'douyin': 'https://tenapi.cn/v2/douyinhot',
        'toutiao': 'https://tenapi.cn/v2/toutiaohot',
    }

    def _one(args):
        code, url = args
        r = requests.get(url, timeout=Config.REQUEST_TIMEOUT,
                         headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        data = r.json()
        items = data.get('data') if isinstance(data, dict) else None
        return code, items

    result = {}
    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        futures = {pool.submit(_one, kv): kv[0] for kv in endpoints.items()}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                code, items = fut.result()
                if isinstance(items, list) and items:
                    result[code] = items
            except Exception as e:  # 单平台失败不影响其他平台
                log.warning("tenapi %s 失败: %s", code, e)
    return result


def fetch_official():
    """
    数据源 0：各平台官方接口直连（当前最稳定）。

    第三方聚合 API（vvhan / tenapi / 60s）在数据中心 IP 下普遍失效，
    这里直接调用平台自己的公开接口，逐个尝试、互不影响。
    目前可实现：今日头条、抖音、百度；其余平台失败时静默跳过。
    """
    ua = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
    headers = {'User-Agent': ua, 'Referer': 'https://www.baidu.com/'}
    result = {}

    def _get(url, timeout=12):
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    # ---- 今日头条热榜 ----
    try:
        data = _get('https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc')
        items = []
        for it in (data.get('data') or [])[:Config.MAX_ITEMS_PER_PLATFORM]:
            title = (it.get('Title') or '').strip()
            if not title:
                continue
            items.append({
                'title': title,
                'url': it.get('Url'),
                'heat': it.get('HotValue') or it.get('Heat'),
            })
        if items:
            result['toutiao'] = items
    except Exception as e:
        log.warning("官方 头条 失败: %s", str(e)[:120])

    # ---- 抖音热搜 ----
    try:
        data = _get('https://www.douyin.com/aweme/v1/web/hot/search/list/')
        words = ((data.get('data') or {}).get('word_list')) or []
        items = []
        for it in words[:Config.MAX_ITEMS_PER_PLATFORM]:
            title = (it.get('word') or '').strip()
            if not title:
                continue
            items.append({
                'title': title,
                'url': 'https://www.douyin.com/search/' + title,
                'heat': it.get('hot_value'),
            })
        if items:
            result['douyin'] = items
    except Exception as e:
        log.warning("官方 抖音 失败: %s", str(e)[:120])

    # ---- 百度热搜 ----
    try:
        data = _get('https://top.baidu.com/api/board?platform=wise&tab=realtime')
        cards = ((data.get('data') or {}).get('cards')) or []
        rows = []
        if cards:
            inner = cards[0].get('content') or []
            # 兼容两种层级：直接是词条列表，或再包一层 {"content": [...]}
            if inner and isinstance(inner[0], dict) and 'content' in inner[0]:
                rows = inner[0].get('content') or []
            else:
                rows = inner
        items = []
        for it in rows[:Config.MAX_ITEMS_PER_PLATFORM]:
            title = (it.get('word') or it.get('query') or '').strip()
            if not title:
                continue
            items.append({
                'title': title,
                'url': it.get('url') or it.get('rawUrl'),
                'heat': it.get('hotScore'),
                'summary': (it.get('desc') or '').strip() or None,
            })
        if items:
            result['baidu'] = items
    except Exception as e:
        log.warning("官方 百度 失败: %s", str(e)[:120])

    # ---- 微博热搜（移动端接口；数据中心 IP 常被限流，失败即跳过）----
    try:
        data = _get('https://weibo.com/ajax/side/hotSearch')
        realtime = ((data.get('data') or {}).get('realtime')) or []
        items = []
        for it in realtime[:Config.MAX_ITEMS_PER_PLATFORM]:
            title = (it.get('word') or '').strip()
            if not title or it.get('is_ad'):
                continue
            items.append({
                'title': title,
                'url': 'https://s.weibo.com/weibo?q=' + requests.utils.quote('#' + title + '#'),
                'heat': it.get('num'),
                'label': it.get('label_name'),
            })
        if items:
            result['weibo'] = items
    except Exception as e:
        log.warning("官方 微博 失败: %s", str(e)[:120])

    # ---- B站热门视频（popular 接口，按播放量）----
    try:
        r = requests.get('https://api.bilibili.com/x/web-interface/popular?ps=30&pn=1',
                         headers={**headers, 'Referer': 'https://www.bilibili.com/'},
                         timeout=12)
        r.raise_for_status()
        data = r.json()
        items = []
        if data.get('code') == 0:
            for it in ((data.get('data') or {}).get('list') or [])[:Config.MAX_ITEMS_PER_PLATFORM]:
                title = (it.get('title') or '').strip()
                if not title:
                    continue
                bvid = it.get('bvid') or ''
                stat = it.get('stat') or {}
                items.append({
                    'title': title,
                    'url': 'https://www.bilibili.com/video/' + bvid if bvid else None,
                    'heat': stat.get('view'),
                    'summary': (it.get('desc') or '').strip() or None,
                    'extra': {'owner': (it.get('owner') or {}).get('name'),
                              'bvid': bvid, 'pic': it.get('pic')},
                })
        if items:
            result['bilibili'] = items
    except Exception as e:
        log.warning("官方 B站 失败: %s", str(e)[:120])

    # ---- IT之家最新资讯（官方 newslist 接口）----
    try:
        r = requests.get('https://api.ithome.com/json/newslist/news',
                         headers={**headers, 'Referer': 'https://www.ithome.com/'},
                         timeout=12)
        r.raise_for_status()
        news = r.json().get('newslist') or []
        items = []
        for it in news[:Config.MAX_ITEMS_PER_PLATFORM]:
            title = (it.get('title') or '').strip()
            if not title:
                continue
            path = it.get('url') or ''
            items.append({
                'title': title,
                'url': ('https://www.ithome.com' + path) if path.startswith('/') else path or None,
                'heat': it.get('commentcount') or it.get('hitcount'),
                'summary': (it.get('description') or '').strip() or None,
                'extra': {'newsid': it.get('newsid'),
                          'postdate': it.get('postdate'),
                          'image': it.get('image')},
            })
        if items:
            result['ithome'] = items
    except Exception as e:
        log.warning("官方 IT之家 失败: %s", str(e)[:120])

    # ---- GitHub 近期热门仓库（官方 Search API）----
    try:
        from datetime import date, timedelta
        since = (date.today() - timedelta(days=7)).isoformat()
        r = requests.get(
            'https://api.github.com/search/repositories',
            params={'q': 'created:>' + since, 'sort': 'stars',
                    'order': 'desc', 'per_page': Config.MAX_ITEMS_PER_PLATFORM},
            headers={**headers, 'Accept': 'application/vnd.github+json'},
            timeout=15)
        r.raise_for_status()
        rows = r.json().get('items') or []
        items = []
        for it in rows[:Config.MAX_ITEMS_PER_PLATFORM]:
            full = (it.get('full_name') or '').strip()
            if not full:
                continue
            items.append({
                'title': full,
                'url': it.get('html_url'),
                'heat': it.get('stargazers_count'),
                'summary': (it.get('description') or '').strip() or None,
                'extra': {'language': it.get('language'),
                          'forks': it.get('forks_count'),
                          'stars': it.get('stargazers_count')},
            })
        if items:
            result['github'] = items
    except Exception as e:
        log.warning("官方 GitHub 失败: %s", str(e)[:120])

    return result


def fetch_60s():
    """数据源 3：60s 热榜 API。"""
    url = 'https://60s.viki.mee/api/v1/60s/hot'
    r = requests.get(url, timeout=Config.REQUEST_TIMEOUT,
                     headers={'User-Agent': 'Mozilla/5.0'})
    r.raise_for_status()
    data = r.json()
    nodes = data.get('data') or []
    result = {}
    if isinstance(nodes, list):
        for node in nodes:
            code = _norm_alias(node.get('name') or node.get('source'))
            items = node.get('data') or node.get('list') or []
            if code and isinstance(items, list):
                result[code] = items
    return result


SOURCES = [
    ('官方直连', fetch_official),
    ('vvhan', fetch_vvhan),
    ('tenapi', fetch_tenapi),
    ('60s', fetch_60s),
]


# ================================================================ 离线演示数据
def _demo_platforms():
    """
    内置演示数据：当所有在线数据源不可达时使用（页面上会有明确标识）。
    结构：{平台代码: [{'title','url','heat','detail'(可选)}, ...]}
    detail 为该条热点的详细信息，前 5 条提供完整内容，其余为 None。
    """
    return {
        'weibo': [
            {'title': '秋季新品发布会集中亮相 折叠屏成焦点', 'url': 'https://s.weibo.com', 'heat': 4526000,
             'detail': '多家手机厂商在本周集中发布秋季新品，折叠屏成为绝对主角。新一代产品在重量控制上进一步突破，部分机型已接近直板机手感，铰链寿命和折痕控制也有明显改善。业内人士指出，随着铰链、UTG 玻璃等供应链成熟，折叠屏正在从「尝鲜」走向「常用」，价格下探是放量的关键。'},
            {'title': '全国多地气温创下半年新低', 'url': 'https://s.weibo.com', 'heat': 3980000,
             'detail': '受强冷空气影响，全国多地气温创下半年新低，北方部分地区降温幅度超过 12℃。气象部门提醒，未来三天寒意还将加剧，公众需注意防寒保暖。多个城市已提前启动供暖，农业部门也发布指导意见，防范低温对设施农业的影响。'},
            {'title': '国产大模型开源生态持续壮大', 'url': 'https://s.weibo.com', 'heat': 3510000,
             'detail': '近期多家国内科技公司相继开源新一代大模型，覆盖文本、多模态等方向，下载量屡创新高。开源社区围绕这些模型快速衍生出大量微调版本和应用案例，开发者生态初具规模。分析认为，开源策略正在成为国产 AI 扩大影响力的重要路径。'},
            {'title': '城市马拉松热潮背后的全民健身', 'url': 'https://s.weibo.com', 'heat': 2870000,
             'detail': '入秋以来，全国数十个城市相继举办马拉松赛事，报名人数屡创新高，中签率持续走低。跑步从一项小众运动变成城市生活方式，带火了运动装备、文旅消费等相关产业。专家提醒，参赛需科学训练，量力而行。'},
            {'title': '新能源车渗透率再创新高', 'url': 'https://s.weibo.com', 'heat': 2540000,
             'detail': '最新数据显示，国内新能源乘用车零售渗透率再次刷新历史纪录，多家车企月销突破新高。插混与增程车型的增长尤为突出，充电基础设施的完善也让纯电用户的里程焦虑明显缓解。行业预测，明年竞争焦点将转向智能化体验。'},
            {'title': '校园食品安全问题引发关注', 'url': 'https://s.weibo.com', 'heat': 2310000, 'detail': None},
            {'title': '热门综艺收官夜收视夺冠', 'url': 'https://s.weibo.com', 'heat': 1980000, 'detail': None},
            {'title': 'AI 辅助诊疗进入更多三甲医院', 'url': 'https://s.weibo.com', 'heat': 1760000, 'detail': None},
            {'title': '跨省通办事项再扩容', 'url': 'https://s.weibo.com', 'heat': 1520000, 'detail': None},
            {'title': '深夜食堂为何让人念念不忘', 'url': 'https://s.weibo.com', 'heat': 1340000, 'detail': None},
        ],
        'zhihu': [
            {'title': '如何看待下一代折叠屏手机轻薄化趋势？', 'url': 'https://www.zhihu.com', 'heat': 3120000,
             'detail': '答主从工程角度分析：折叠屏轻薄化的核心在于「减法」——更薄的柔性UTG玻璃、更小的水滴铰链、更高能量密度的硅碳负极电池。当厚度逼近直板机，折叠屏的差异化就只剩「一块变两块」的场景价值。有答主提醒，轻薄与散热、影像之间仍需权衡，轻薄不应以牺牲体验为代价。'},
            {'title': '为什么说 Python 是 AI 时代的通用语言？', 'url': 'https://www.zhihu.com', 'heat': 2860000,
             'detail': '高票回答指出，Python 的统治力来自生态而非语言本身：NumPy/PyTorch/TensorFlow 等基础库构成了事实标准，JIT 与 C 扩展弥补了性能短板。更重要的是，AI 研究者用 Python 写论文代码，工程侧用 Python 做服务，语言统一极大降低了从研究到生产的摩擦。'},
            {'title': '普通人有必要学习大模型应用开发吗？', 'url': 'https://www.zhihu.com', 'heat': 2450000,
             'detail': '回答普遍认为，无需精通算法，但理解 Prompt 工程、API 调用与 RAG 基本原理，已经成为新的「办公软件技能」。大量答主分享了用开源模型+向量数据库搭建个人知识库的实践，门槛远低于想象。反对声音则提醒：警惕培训机构的夸大宣传。'},
            {'title': '秋招季如何选择第一份工作？', 'url': 'https://www.zhihu.com', 'heat': 2210000,
             'detail': '多位 HR 与职场答主建议：第一份工作的优先级应是「行业成长性 > 直属领导 > 薪资 > 公司名气」。处于上升期的行业能带来复利式成长，而愿意带人的领导比光环更重要。答主们也提醒，不要用「稳定」作为逃避选择的借口。'},
            {'title': '如何评价开源社区对国产软件的推动？', 'url': 'https://www.zhihu.com', 'heat': 1980000,
             'detail': '答主梳理了国产基础软件的开源浪潮：从数据库、操作系统到 AI 框架，开源让国产软件获得了「被使用、被验证」的机会。社区贡献者也从中培养了大量人才。讨论同时指出，开源不是终点，商业闭环与可持续的社区治理仍是挑战。'},
            {'title': '长期熬夜对身体的影响到底有多大？', 'url': 'https://www.zhihu.com', 'heat': 1870000, 'detail': None},
            {'title': 'MySQL 8.0 有哪些值得关注的新特性？', 'url': 'https://www.zhihu.com', 'heat': 1650000, 'detail': None},
            {'title': '年轻人为什么开始热衷城市漫步？', 'url': 'https://www.zhihu.com', 'heat': 1430000, 'detail': None},
            {'title': '家庭理财如何做到稳健配置？', 'url': 'https://www.zhihu.com', 'heat': 1280000, 'detail': None},
            {'title': '有哪些提升编程效率的好习惯？', 'url': 'https://www.zhihu.com', 'heat': 1150000, 'detail': None},
        ],
        'baidu': [
            {'title': '今日天气：强冷空气继续南下', 'url': 'https://top.baidu.com', 'heat': 4890000,
             'detail': '中央气象台继续发布冷空气预报：未来 48 小时强冷空气将自北向南影响我国中东部大部，气温普遍下降 6～10℃，局地超过 14℃，并伴有 4～6 级偏北风。提醒相关地区做好设施农业加固和防寒准备，公众注意添衣保暖，谨防呼吸道疾病。'},
            {'title': '高铁新线路开通 全程缩至两小时', 'url': 'https://top.baidu.com', 'heat': 4120000,
             'detail': '新建高铁线路今日正式开通运营，设计时速 350 公里，两地间旅行时间由原来的四个多小时压缩至两小时以内。新线路串联起沿线多个中小城市，将显著改善区域出行条件，预计日均发送旅客超过三万人次。'},
            {'title': '医保药品目录调整正式公布', 'url': 'https://top.baidu.com', 'heat': 3760000,
             'detail': '新版国家医保药品目录公布，一批肿瘤、罕见病和慢性病用药被纳入报销范围，另有若干「天价药」通过谈判实现大幅降价。调整后，目录内药品总数增加，患者用药负担进一步减轻。新版目录将于明年 1 月 1 日起正式执行。'},
            {'title': '高校毕业生就业服务专项行动启动', 'url': 'https://top.baidu.com', 'heat': 3210000,
             'detail': '针对 2027 届毕业生的就业服务专项行动启动，将归集发布千万级岗位信息，组织区域性、行业性招聘活动，并对困难毕业生开展结对帮扶。政策同时鼓励校企合作开展见习实训，提升人岗匹配效率。'},
            {'title': '以旧换新政策带动家电消费', 'url': 'https://top.baidu.com', 'heat': 2870000,
             'detail': '消费品以旧换新政策效应持续释放，家电市场销售明显回暖，一级能效产品占比提升。商务部数据显示，活动开展以来累计带动销售额超预期，回收体系同步完善。业内预计，政策将延续至年底并可能扩围至更多品类。'},
            {'title': '多地下调二套房首付比例', 'url': 'https://top.baidu.com', 'heat': 2650000, 'detail': None},
            {'title': '义务教育课后服务将全面提质', 'url': 'https://top.baidu.com', 'heat': 2340000, 'detail': None},
            {'title': '冬季旅游预订量同比增长', 'url': 'https://top.baidu.com', 'heat': 2080000, 'detail': None},
            {'title': '首个国产深海采矿装备海试成功', 'url': 'https://top.baidu.com', 'heat': 1870000, 'detail': None},
            {'title': '反诈宣传走进社区校园', 'url': 'https://top.baidu.com', 'heat': 1620000, 'detail': None},
        ],
        'bilibili': [
            {'title': '【硬核科普】大模型是怎么训练的', 'url': 'https://www.bilibili.com', 'heat': 2680000,
             'detail': 'UP 主用动画拆解了大模型训练全流程：从预训练的海量文本与算力消耗，到监督微调、人类反馈强化学习（RLHF）的对齐阶段，再到推理时的采样策略。视频特别强调了数据质量对模型能力的决定性影响，被网友称为「最好的中文大模型科普」。'},
            {'title': '用代码复刻经典游戏需要几步', 'url': 'https://www.bilibili.com', 'heat': 2230000,
             'detail': '系列视频中，开发者仅用数百行 Python 代码复刻了一代经典游戏的完整玩法：地图生成、碰撞检测、敌人 AI 一应俱全。UP 主逐行讲解并开放源码，弹幕直呼「爷青回」。该系列已成为编程区月度热门。'},
            {'title': '数码区UP主实测新款耳机', 'url': 'https://www.bilibili.com', 'heat': 1980000,
             'detail': 'UP 主对三款热门降噪耳机进行了为期一周的通勤实测，从降噪深度、佩戴舒适度、通话质量三个维度打分，并用专业设备测量了频响曲线。结论是：中端价位已能获得旗舰八成体验，性价比之选易主。'},
            {'title': '校园歌手大赛神仙现场合集', 'url': 'https://www.bilibili.com', 'heat': 1760000,
             'detail': '一年一度的校园歌手大赛落幕，UP 主剪辑了本届「神仙现场」合集：从原创民谣到美声唱腔，多个视频播放量破百万。网友评论「大学的舞台藏龙卧虎」，也有校友在评论区认出台上的歌手如今已是职业音乐人。'},
            {'title': '从零开始手搓一个网站', 'url': 'https://www.bilibili.com', 'heat': 1540000,
             'detail': 'UP 主直播记录了从买服务器、装系统、写前后端到上线的完整过程，最终成品是一个每日热榜聚合网站——正是本项目的灵感来源之一。视频涵盖了 Flask、MySQL、定时任务等完整技术栈，被观众称为「最适合新手的全栈项目实战」。'},
            {'title': '美食纪录片式 Vlog 上线', 'url': 'https://www.bilibili.com', 'heat': 1380000, 'detail': None},
            {'title': '军训名场面盘点', 'url': 'https://www.bilibili.com', 'heat': 1210000, 'detail': None},
            {'title': '程序员的第一台机械键盘', 'url': 'https://www.bilibili.com', 'heat': 1080000, 'detail': None},
            {'title': 'AI 绘画工具横评', 'url': 'https://www.bilibili.com', 'heat': 96000, 'detail': None},
            {'title': '猫咪行为学大揭秘', 'url': 'https://www.bilibili.com', 'heat': 87000, 'detail': None},
        ],
        'douyin': [
            {'title': 'citywalk 路线推荐又更新了', 'url': 'https://www.douyin.com', 'heat': 5210000,
             'detail': '秋季 citywalk 路线合集更新，创作者们推出了「银杏大道」「老城厢」「江畔日落」等主题路线，每条路线标注了最佳拍摄机位和沿途咖啡店。相关话题播放量已破十亿，带动了一批小众街区成为周末热门目的地。'},
            {'title': '大学生手工耿式发明走红', 'url': 'https://www.douyin.com', 'heat': 4620000,
             'detail': '一位大学生制作的「宿舍懒人装置」走红：一张床整合了台灯、手机支架、小风扇和储物格，被称为「宿舍神器」。视频获赞数百万，网友调侃「手工耿后继有人」，也有高校宿舍管理方温馨提醒注意用电安全。'},
            {'title': '广场舞跳出新高度', 'url': 'https://www.douyin.com', 'heat': 3980000,
             'detail': '一段广场舞视频刷屏：阿姨们伴着流行乐曲完成了整齐的舞蹈编排，动作专业、表情管理到位。领队阿姨接受采访说，队伍已成立八年，「跳舞就是要跳出精气神」。视频被年轻网友称为「最强女团排练现场」。'},
            {'title': '挑战一分钟做早餐', 'url': 'https://www.douyin.com', 'heat': 3540000,
             'detail': '一分钟早餐挑战席卷平台，创作者们在 60 秒内完成三明治、燕麦碗、手冲咖啡等组合，节奏紧凑、步骤清晰。营养师提醒：追求效率的同时也要保证蛋白质和膳食纤维，别让早餐只剩碳水。'},
            {'title': '宠物医生暖心救治现场', 'url': 'https://www.douyin.com', 'heat': 3120000,
             'detail': '一段宠物医院监控走红：一只流浪猫被送医时生命体征微弱，医护团队抢救四小时后它缓缓睁开了眼睛。后续视频里，这只猫已被领养并长到了五公斤。评论区数万条留言：「谢谢你们没有放弃它」。'},
            {'title': '返乡创业的年轻人', 'url': 'https://www.douyin.com', 'heat': 2870000, 'detail': None},
            {'title': '校园运动会高光时刻', 'url': 'https://www.douyin.com', 'heat': 2540000, 'detail': None},
            {'title': '传统手艺的新生', 'url': 'https://www.douyin.com', 'heat': 2210000, 'detail': None},
            {'title': '深夜emo歌单又添新歌', 'url': 'https://www.douyin.com', 'heat': 1980000, 'detail': None},
            {'title': '钓鱼佬的快乐你想象不到', 'url': 'https://www.douyin.com', 'heat': 1760000, 'detail': None},
        ],
        'toutiao': [
            {'title': '前三季度经济数据发布', 'url': 'https://www.toutiao.com', 'heat': 5340000,
             'detail': '统计部门发布前三季度经济运行数据：国内生产总值保持稳定增长，高技术制造业投资增速领先，消费市场持续恢复。新动能加快成长，数字经济核心产业增加值占比稳步提升。分析指出，宏观政策效应持续显现，四季度有望延续回升态势。'},
            {'title': '多地优化住房公积金政策', 'url': 'https://www.toutiao.com', 'heat': 4210000,
             'detail': '近期多地密集调整住房公积金政策：提高贷款额度、支持提取支付首付款、推行「商转公」等。政策调整旨在降低购房成本、支持刚性和改善性住房需求。多地还简化了提取流程，实现「租房提取」按月自动到账。'},
            {'title': '商业航天发射任务圆满成功', 'url': 'https://www.toutiao.com', 'heat': 3870000,
             'detail': '今日凌晨，一枚商业运载火箭在发射场成功点火升空，将十多颗卫星准确送入预定轨道。这是国内商业航天今年第 N 次成功发射，发射成本与周期均较去年同期明显优化。产业链人士透露，卫星互联网组网进度正在加快。'},
            {'title': '秋粮收购工作全面展开', 'url': 'https://www.toutiao.com', 'heat': 3450000,
             'detail': '秋粮收购自南向北全面展开，主产区已准备充足仓容和信贷资金。中储粮等主渠道企业挂牌价格保持稳定，部分优质品种价格坚挺。粮食部门提示农户把握售粮节奏，同时严防「打白条」等坑农行为。'},
            {'title': '人工智能产业发展规划出台', 'url': 'https://www.toutiao.com', 'heat': 3120000,
             'detail': '相关部门发布人工智能产业发展行动计划，提出到 2028 年培育若干具有国际竞争力的领军企业，建设一批算力枢纽和开放平台。文件同时强调数据供给、安全治理和人才队伍建设，明确支持大模型在制造、医疗、教育等场景落地。'},
            {'title': '重点城市楼市成交回暖', 'url': 'https://www.toutiao.com', 'heat': 2870000, 'detail': None},
            {'title': '跨境电商助力外贸新动能', 'url': 'https://www.toutiao.com', 'heat': 2540000, 'detail': None},
            {'title': '文旅市场持续升温', 'url': 'https://www.toutiao.com', 'heat': 2210000, 'detail': None},
            {'title': '制造业数字化转型加速', 'url': 'https://www.toutiao.com', 'heat': 1980000, 'detail': None},
            {'title': '县域经济焕发新活力', 'url': 'https://www.toutiao.com', 'heat': 1760000, 'detail': None},
        ],
        'github': [
            {'title': 'trending/python: 每日 Python 趋势榜', 'url': 'https://github.com/trending/python', 'heat': 18500,
             'detail': 'GitHub Python 趋势榜今日看点：一个大模型应用框架凭借「开箱即用的 Agent 编排」登顶，本周新增 3k+ Star；另一个自动化工具集合主打「用自然语言描述即可生成工作流」，同样增长迅猛。AI 应用层项目继续占据榜单半壁江山。'},
            {'title': 'awesome-llm-apps: 大模型应用合集', 'url': 'https://github.com/trending', 'heat': 15200,
             'detail': '该仓库持续收录高质量 LLM 应用，按 RAG、Agent、多模态等场景分类，每个项目附有一句话点评和难度分级。维护者表示本周新增二十余个应用，其中「本地知识库问答」类最受欢迎。适合想快速了解生态全貌的开发者收藏。'},
            {'title': 'mysql-portable: 便携数据库工具', 'url': 'https://github.com/trending', 'heat': 12800,
             'detail': '一个把 MySQL 打包为绿色便携版的小工具引发关注：解压即用、可整体迁移，适合开发演示和环境快速复现。README 中详细记录了从下载、初始化到注册 Windows 服务的完整步骤，踩坑提示相当实用，被多个技术社区转载。'},
            {'title': 'daily-hot-aggregator: 热榜聚合项目', 'url': 'https://github.com/trending', 'heat': 11400,
             'detail': '该项目抓取全网热榜并归档到数据库，提供网页聚合展示，技术栈为 Flask + MySQL + APScheduler。亮点在于多数据源降级策略和每日快照归档设计。作者在 README 里坦诚分享了从「只有标题」到「保存完整详情」的演进过程。'},
            {'title': 'flask-best-practices: Flask 最佳实践', 'url': 'https://github.com/trending', 'heat': 9800,
             'detail': '仓库整理了生产环境 Flask 应用的工程实践：应用工厂模式、配置分层、连接管理、日志规范、定时任务与优雅退出等，每条实践都配有最小可复现代码。适合从「能跑」走向「可维护」的团队参考。'},
            {'title': 'data-visualization: 数据可视化组件', 'url': 'https://github.com/trending', 'heat': 8700, 'detail': None},
            {'title': 'open-source-crawler: 爬虫框架', 'url': 'https://github.com/trending', 'heat': 7600, 'detail': None},
            {'title': 'react-native-ui-kit: 移动端 UI 套件', 'url': 'https://github.com/trending', 'heat': 6500, 'detail': None},
            {'title': 'tiny-compiler: 迷你编译器实现', 'url': 'https://github.com/trending', 'heat': 5900, 'detail': None},
            {'title': 'algorithm-notes: 算法笔记仓库', 'url': 'https://github.com/trending', 'heat': 5200, 'detail': None},
        ],
        'v2ex': [
            {'title': '大家现在用什么方案做每日定时任务？', 'url': 'https://www.v2ex.com', 'heat': 980,
             'detail': '楼主想给爬虫加每日定时执行，纠结于 crontab、systemd timer 还是应用内置 APScheduler。回帖观点分化：服务器派力推 crontab 简单可靠；应用派认为 APScheduler 与业务代码同生命周期更省心；也有人推荐用系统的任务计划程序统一管理。'},
            {'title': '自建热榜聚合站，求拍砖', 'url': 'https://www.v2ex.com', 'heat': 870,
             'detail': '楼主分享了自己用 Flask + MySQL 搭的热榜站，支持每日定时抓取、历史归档和详情页。回帖建议集中在：详情抓取要限流避免被封、热榜数据建议保留原文快照、加上 RSS 输出。楼主表示会逐步补齐详情存储。'},
            {'title': 'MySQL 8.0 移除 --install 参数了？', 'url': 'https://www.v2ex.com', 'heat': 760,
             'detail': '楼主升级到 8.0.46 后发现 mysqld --install 报 unknown option。回帖确认该参数已在新版本移除，注册服务需改用 sc create 或 PowerShell New-Service，并提醒命令行末尾必须带服务名参数，否则 SCM 状态握手会失败。'},
            {'title': '分享一个便携式开发环境思路', 'url': 'https://www.v2ex.com', 'heat': 690,
             'detail': '楼主实践了「整个开发环境放一个目录」：MySQL 绿色版 + Python venv + 项目代码全部集中管理，换机器时整目录拷贝即可恢复。回帖补充了路径硬编码的规避技巧——全部配置用相对路径加环境变量覆盖。'},
            {'title': 'Flask 3.1 升级体验记录', 'url': 'https://www.v2ex.com', 'heat': 620,
             'detail': '楼主记录了从 Flask 2.x 升级到 3.1 的过程：API 兼容性出乎意料地好，主要是依赖版本和若干弃用警告需要清理。新增的若干便利特性让蓝图组织更清爽。回帖建议升级前先用测试覆盖核心路由。'},
            {'title': '求推荐轻量级定时任务方案', 'url': 'https://www.v2ex.com', 'heat': 540, 'detail': None},
            {'title': '用 Python 写爬虫的心得', 'url': 'https://www.v2ex.com', 'heat': 480, 'detail': None},
            {'title': '机械键盘选购纠结中', 'url': 'https://www.v2ex.com', 'heat': 430, 'detail': None},
            {'title': '远程办公两年体验分享', 'url': 'https://www.v2ex.com', 'heat': 390, 'detail': None},
            {'title': '国产数据库使用体验讨论', 'url': 'https://www.v2ex.com', 'heat': 350, 'detail': None},
        ],
        'ithome': [
            {'title': '新一代旗舰芯片发布 性能提升明显', 'url': 'https://www.ithome.com', 'heat': 42000,
             'detail': '新一代旗舰移动芯片正式发布，采用全新制程与架构，官方数据显示 CPU 性能提升超过三成，GPU 能效比显著改善，AI 算力翻倍。首发机型将于下周开售，预计搭载该芯片的平板和笔记本也会在年内亮相。'},
            {'title': '操作系统大版本更新推送', 'url': 'https://www.ithome.com', 'heat': 36500,
             'detail': '桌面操作系统迎来年度大版本更新，新版重构了设置界面，新增 AI 助手快捷入口，文件管理器性能提升明显。更新采用分批推送策略，用户可手动检查更新。企业版将在获得充分验证后开放部署。'},
            {'title': '某品牌宣布终身系统维护承诺', 'url': 'https://www.ithome.com', 'heat': 31200,
             'detail': '一家硬件厂商宣布对在售产品线提供「终身系统维护」，承诺至少五年的安全更新与功能优化。业内认为此举将倒逼行业提升售后标准，也有分析指出长期维护成本最终会反映在产品定价中。'},
            {'title': '国产固态硬盘价格持续下探', 'url': 'https://www.ithome.com', 'heat': 27800,
             'detail': '随着国产存储芯片产能爬坡，固态硬盘价格持续走低，1TB 容量已进入普及价位。渠道数据显示国产品牌份额快速提升，读写速度与稳定性也追平国际大厂同级产品。装机用户成为最大受益群体。'},
            {'title': '折叠屏铰链技术迎来突破', 'url': 'https://www.ithome.com', 'heat': 24500,
             'detail': '供应链传出消息，新一代水滴铰链零件数减少两成，厚度降低同时寿命提升至 50 万次折叠。新技术有望让折叠屏整机减重近 10 克，为「毫米级」轻薄化让出空间，预计明年上半年量产装机。'},
            {'title': '浏览器内核更新 速度提升三成', 'url': 'https://www.ithome.com', 'heat': 21300, 'detail': None},
            {'title': '开源社区发布新版数据库', 'url': 'https://www.ithome.com', 'heat': 18700, 'detail': None},
            {'title': 'AI PC 概念落地 销量攀升', 'url': 'https://www.ithome.com', 'heat': 16200, 'detail': None},
            {'title': '卫星互联网服务开始公测', 'url': 'https://www.ithome.com', 'heat': 13900, 'detail': None},
            {'title': '新能源续航虚标问题调查', 'url': 'https://www.ithome.com', 'heat': 11800, 'detail': None},
        ],
        'sspai': [
            {'title': '效率工具周报：本周值得关注的 8 个更新', 'url': 'https://sspai.com', 'heat': 15800,
             'detail': '本周效率工具动态一览：笔记应用新增白板模块、待办工具上线自然语言识别、密码管理器支持密钥登录、日历应用引入时区智能提示……编辑部挑选了 8 个真正好用的更新，附上上手建议与替代方案对比。'},
            {'title': '我的第二大脑搭建指南', 'url': 'https://sspai.com', 'heat': 13200,
             'detail': '作者分享了一套「输入-整理-输出」闭环的知识管理系统：碎片信息统一进收件箱，每周复盘时用「常青笔记」方式重写，项目资料用数据库管理。文章强调工具是次要的，持续运转的流程才是第二大脑的核心。'},
            {'title': '如何用数据库管理读书笔记', 'url': 'https://sspai.com', 'heat': 11400,
             'detail': '一篇硬核教程：用 MySQL 搭建个人读书笔记库，设计书籍表、笔记表、标签表并建立关联，通过视图统计阅读轨迹。作者认为数据库比笔记软件更适合结构化沉淀，文末附完整建表 SQL，方便读者复现。'},
            {'title': '一台电脑用三年的保养心得', 'url': 'https://sspai.com', 'heat': 9800,
             'detail': '作者记录了让 MacBook 三年后依旧流畅的习惯：每月清理启动项、保持 20% 以上磁盘余量、电池健康随用随充、每年重装一次系统。评论区补充了散热垫与 Type-C 接口防尘等细节，讨论相当务实。'},
            {'title': '自动化让生活更简单', 'url': 'https://sspai.com', 'heat': 8600,
             'detail': '从自动记账到每周五定时归档下载文件夹，作者用十几个自动化流程把重复操作交给电脑。文章按「触发条件-执行动作」逐一拆解，强调「先跑三个月手动流程，确认模式稳定后再自动化」的务实原则。'},
            {'title': '付费订阅到底值不值', 'url': 'https://sspai.com', 'heat': 7400, 'detail': None},
            {'title': '从零搭建个人知识库', 'url': 'https://sspai.com', 'heat': 6300, 'detail': None},
            {'title': '手机摄影快速入门', 'url': 'https://sspai.com', 'heat': 5500, 'detail': None},
            {'title': '年度总结模板分享', 'url': 'https://sspai.com', 'heat': 4800, 'detail': None},
            {'title': '桌面改造全记录', 'url': 'https://sspai.com', 'heat': 4100, 'detail': None},
        ],
    }


# ================================================================ 归一化与保存
def _normalize(raw_items, max_items):
    """把第三方返回的条目统一为内部结构，截取前 max_items 条。"""
    out = []
    for idx, it in enumerate(raw_items[:max_items]):
        if not isinstance(it, dict):
            continue
        title = (it.get('title') or it.get('name') or it.get('desc') or '').strip()
        if not title:
            continue
        url = it.get('url') or it.get('mobile_url') or it.get('link')
        heat, heat_display = _parse_heat(
            it.get('hot') if it.get('hot') is not None else it.get('heat'))
        out.append({
            'title': title,
            'url': url,
            'heat': heat,
            'heat_display': heat_display,
            'summary': (it.get('summary') or it.get('desc') or '').strip() or None,
            'extra': {k: v for k, v in it.items()
                      if k in ('author', 'label', 'tag', 'cover') and v},
        })
    return out


def crawl_and_save(snapshot_date=None, fetch_details=True):
    """
    执行一次完整抓取：依次尝试在线数据源，全部失败则使用演示数据。
    在线数据抓取成功后，会额外为各平台前 N 条热点抓取详细信息（后台进行）。
    返回汇总 dict。
    """
    snapshot_date = snapshot_date or date.today()
    max_items = Config.MAX_ITEMS_PER_PLATFORM
    merged, source_name, status, message = {}, None, None, None

    # ---- 依次尝试在线数据源 ----
    for name, fn in SOURCES:
        start = time.time()
        try:
            raw = fn()
            duration = int((time.time() - start) * 1000)
            normalized = {}
            for code, items in raw.items():
                norm = _normalize(items, max_items)
                if norm:
                    normalized[code] = norm
            if normalized:
                merged, source_name, status = normalized, name, 'success'
                message = f"数据源 {name} 抓取成功"
                for code, items in normalized.items():
                    db.write_log(code, name, 'success', len(items),
                                 message, duration)
                log.info("抓取成功: 源=%s 平台数=%d", name, len(normalized))
                break
            else:
                db.write_log('all', name, 'failed', 0,
                             '数据源返回为空或格式不兼容', duration)
                log.warning("数据源 %s 无有效数据", name)
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            db.write_log('all', name, 'failed', 0, f"{type(e).__name__}: {e}", duration)
            log.warning("数据源 %s 失败: %s", name, e)

    # ---- 全部失败，降级为演示数据 ----
    detail_total = 0
    if not merged:
        source_name, status = 'offline-demo', 'mock'
        message = '所有在线数据源不可达，已使用内置演示数据'
        log.warning(message)
        # 演示数据自带详情信息，一并入库
        for code, items in _demo_platforms().items():
            norm = []
            for it in items:
                heat, heat_display = _parse_heat(it['heat'])
                norm.append({
                    'title': it['title'], 'url': it['url'], 'heat': heat,
                    'heat_display': heat_display, 'summary': None,
                    'detail': it.get('detail'),
                })
            merged[code] = norm

    # ---- 写入数据库 ----
    total = 0
    for code, items in merged.items():
        try:
            if any(it.get('detail') for it in items):
                saved, dcount = db.save_items_with_details(
                    code, items, snapshot_date)
                detail_total += dcount
            else:
                saved = db.save_items(code, items, snapshot_date)
            total += saved
        except Exception as e:
            log.error("保存平台 %s 失败: %s", code, e)
            db.write_log(code, source_name, 'failed', 0, f"保存失败: {e}")

    # ---- 在线数据：后台抓取详细信息（不阻塞本次返回） ----
    if fetch_details and Config.DETAIL_ENABLED and status == 'success':
        threading.Thread(
            target=_fetch_details_async,
            args=(snapshot_date,), daemon=True).start()

    summary = {
        'date': str(snapshot_date),
        'source': source_name,
        'status': status,
        'message': message,
        'platforms': len(merged),
        'items': total,
        'details': detail_total,
    }
    db.write_log('all', source_name or 'none', status or 'failed',
                 total, message)
    log.info("抓取完成: %s", summary)
    return summary


def _fetch_details_async(snapshot_date):
    """后台线程：抓取并保存新闻详情，异常不影响主流程。"""
    try:
        import detail_fetcher
        detail_fetcher.fetch_details_for_date(snapshot_date)
    except Exception:
        log.exception("后台详情抓取失败")
