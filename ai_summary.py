# -*- coding: utf-8 -*-
"""
AI 今日综述生成。

优先级：配置了大模型（OpenAI 兼容接口，环境变量）则调用它生成综述；
无 API Key / 网络不可达时，回退到基于趋势 + 共振 + 情绪的启发式模板，
保证离线、零依赖环境下也能稳定产出「每日洞察」文本。
"""
import json
import logging
import os
import urllib.request

from config import Config

log = logging.getLogger('app.ai_summary')


def _llm_config():
    return {
        'api_key': os.environ.get('LLM_API_KEY')
        or getattr(Config, 'LLM_API_KEY', ''),
        'base_url': (os.environ.get('LLM_BASE_URL')
                     or getattr(Config, 'LLM_BASE_URL',
                                'https://api.openai.com/v1')).rstrip('/'),
        'model': os.environ.get('LLM_MODEL')
        or getattr(Config, 'LLM_MODEL', 'gpt-4o-mini'),
    }


def _call_llm(prompt):
    """调用 OpenAI 兼容 chat 接口；失败返回 None（触发启发式回退）。"""
    cfg = _llm_config()
    if not cfg['api_key']:
        return None
    body = json.dumps({
        'model': cfg['model'],
        'messages': [
            {'role': 'system',
             'content': '你是资深互联网热点编辑，请用简体中文输出一段客观、精炼、'
                        '有信息量的每日热点综述，不超过 220 字，不编造数据。'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.6,
        'max_tokens': 600,
    }).encode('utf-8')
    req = urllib.request.Request(
        cfg['base_url'] + '/chat/completions', data=body,
        headers={'Authorization': f"Bearer {cfg['api_key']}",
                 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return data['choices'][0]['message']['content'].strip()
    except Exception as e:  # 网络/鉴权/解析异常 -> 回退
        log.warning('LLM 综述调用失败，回退启发式：%s', e)
        return None


def _build_prompt(date, topics, resonance, sentiment):
    lines = [f"以下是 {date} 的全平台热榜分析素材，请据此写一段每日热点综述："]
    c = sentiment.get('counts', {})
    lines.append(f"情绪分布：正面 {c.get('positive',0)} 条、中性 "
                 f"{c.get('neutral',0)} 条、负面 {c.get('negative',0)} 条。")
    top = topics[:6]
    if top:
        lines.append("持续在榜话题（在榜天数/阶段）：" + "；".join(
            f"{t['title']}（{t['days']}天/{t['lifecycle']['cn']}）"
            for t in top))
    res = resonance[:4]
    if res:
        lines.append("跨平台共振话题：" + "、".join(
            f"{r['title']}（{r['platform_count']}平台）" for r in res))
    pos = sentiment.get('positive', [])[:3]
    neg = sentiment.get('negative', [])[:3]
    if pos:
        lines.append("正面热点：" + "、".join(i['title'] for i in pos))
    if neg:
        lines.append("负面/争议热点：" + "、".join(i['title'] for i in neg))
    return "\n".join(lines)


def _heuristic(date, topics, resonance, sentiment):
    """离线启发式综述（不依赖任何外部服务）。"""
    c = sentiment.get('counts', {})
    parts = []
    parts.append(
        f"{date} 共监测到 {sentiment.get('total', 0)} 条热点，"
        f"情绪面正面 {c.get('positive',0)} 条、中性 {c.get('neutral',0)} 条、"
        f"负面 {c.get('negative',0)} 条。")
    rising = [t for t in topics if t['lifecycle']['label'] in ('rising', 'peak')]
    declining = [t for t in topics if t['lifecycle']['label'] == 'declining']
    if rising:
        parts.append(
            "持续升温的话题包括："
            + "、".join(f"《{t['title']}》（在榜{t['days']}天，"
                       f"{t['lifecycle']['cn']}）" for t in rising[:5]) + "。")
    if declining:
        parts.append(
            "热度回落的话题："
            + "、".join(t['title'] for t in declining[:4]) + "。")
    if resonance:
        parts.append(
            "跨平台共振、多端同步发酵的有："
            + "、".join(f"《{r['title']}》（{r['platform_count']}个平台）"
                       for r in resonance[:4]) + "。")
    pos = sentiment.get('positive', [])[:3]
    neg = sentiment.get('negative', [])[:3]
    if pos:
        parts.append("值得关注的正向事件：" + "、".join(i['title'] for i in pos) + "。")
    if neg:
        parts.append("需留意的负面/争议舆情：" + "、".join(i['title'] for i in neg) + "。")
    return "".join(parts)


def generate_summary(date, topics=None, resonance=None, sentiment=None):
    """
    生成每日综述。topics/resonance/sentiment 由调用方传入（避免重复查库）。
    返回 {'text': str, 'source': 'llm'|'heuristic'}。
    """
    topics = topics or []
    resonance = resonance or []
    sentiment = sentiment or {'counts': {}, 'total': 0}
    prompt = _build_prompt(date, topics, resonance, sentiment)
    text = _call_llm(prompt)
    if text:
        return {'text': text, 'source': 'llm'}
    return {'text': _heuristic(date, topics, resonance, sentiment),
            'source': 'heuristic'}
