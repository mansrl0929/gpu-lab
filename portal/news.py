"""Research news feeds. Read-only RSS fetching with an in-memory cache.

Images are proxied through the portal so the page keeps its strict image policy and
browsers never talk to the news CDNs directly.
"""
import asyncio
import html
import re
import time
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

CACHE_SECONDS = 1800
TIMEOUT = 12
PER_CATEGORY = 18

# (제목, 설명, 피드 목록, 필터 키워드). 키워드가 없으면 피드 전체를 씁니다.
CATEGORIES = [
    {'id': 'polar', 'name': '극지 · 해빙', 'note': '해빙, 빙하, 북극·남극 관측',
     'feeds': ['https://phys.org/rss-feed/earth-news/earth-sciences/',
               'https://phys.org/rss-feed/earth-news/environment/'],
     'keywords': ['sea ice', 'ice sheet', 'iceberg', 'glacier', 'arctic', 'antarctic',
                  'permafrost', 'polar', 'greenland', 'snow', 'melt']},
    {'id': 'remote', 'name': '원격탐사 · 위성', 'note': '지구관측 위성, SAR, 영상 분석',
     'feeds': ['https://phys.org/rss-feed/space-news/',
               'https://phys.org/rss-feed/earth-news/earth-sciences/',
               'https://www.esa.int/rssfeed/Our_Activities/Observing_the_Earth'],
     'keywords': ['satellite', 'remote sensing', 'earth observation', 'radar', 'sar ',
                  'lidar', 'imagery', 'sentinel', 'landsat', 'mapping', 'aerial'],
     'exclude': ['galaxy', 'black hole', 'exoplanet', 'supernova', 'asteroid', 'brown dwarf',
                 'nebula', 'star system', 'telescope array']},
    {'id': 'ai', 'name': 'AI · 머신러닝', 'note': '모델, 학습 기법, 응용 연구',
     'feeds': ['https://phys.org/rss-feed/technology-news/machine-learning-ai/'],
     'keywords': []},
]

ALLOWED_IMAGE_HOSTS = {'phys.org', 'scx1.b-cdn.net', 'scx2.b-cdn.net', 'www.esa.int', 'esa.int'}
_cache = {'at': 0.0, 'data': None}
_lock = asyncio.Lock()


def _text(value):
    """Strip tags and entities so a feed summary reads as plain text."""
    without_tags = re.sub(r'<[^>]+>', ' ', value or '')
    return re.sub(r'\s+', ' ', html.unescape(without_tags)).strip()


def _image(item):
    for tag in ('{http://search.yahoo.com/mrss/}thumbnail', '{http://search.yahoo.com/mrss/}content', 'enclosure'):
        for node in item.iter(tag):
            url = node.get('url')
            if url and urlparse(url).hostname in ALLOWED_IMAGE_HOSTS:
                return url
    return None


def parse_feed(body, source):
    items = []
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return items
    for item in root.iter('item'):
        title = _text(item.findtext('title'))
        link = (item.findtext('link') or '').strip()
        if not title or not link:
            continue
        items.append({'title': title, 'link': link, 'source': source,
                      'summary': _text(item.findtext('description'))[:320],
                      'published': (item.findtext('pubDate') or '').strip(),
                      'image': _image(item)})
    return items


def _matches(item, keywords, exclude=()):
    haystack = f"{item['title']} {item['summary']}".lower()
    if any(word in haystack for word in exclude):
        return False
    return not keywords or any(word in haystack for word in keywords)


async def _fetch(client, url):
    try:
        response = await client.get(url, timeout=TIMEOUT, follow_redirects=True,
                                    headers={'User-Agent': 'gpu-lab-portal/1.0'})
        response.raise_for_status()
        return parse_feed(response.text, urlparse(url).hostname or url)
    except Exception:
        return []


async def collect():
    """Return every category with its articles. Cached, so a page refresh costs nothing."""
    async with _lock:
        if _cache['data'] and time.monotonic()-_cache['at'] < CACHE_SECONDS:
            return _cache['data']
        urls = sorted({url for category in CATEGORIES for url in category['feeds']})
        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(*(_fetch(client, url) for url in urls))
        by_url = dict(zip(urls, results))
        categories = []
        for category in CATEGORIES:
            seen, items = set(), []
            for url in category['feeds']:
                for item in by_url[url]:
                    if item['link'] in seen or not _matches(item, category['keywords'], category.get('exclude', ())):
                        continue
                    seen.add(item['link'])
                    items.append(item)
            categories.append({'id': category['id'], 'name': category['name'],
                               'note': category['note'], 'items': items[:PER_CATEGORY]})
        data = {'categories': categories, 'fetched_at': time.time()}
        if any(category['items'] for category in categories):
            _cache.update(at=time.monotonic(), data=data)
        return data


def allowed_image(url):
    parsed = urlparse(url)
    return parsed.scheme == 'https' and parsed.hostname in ALLOWED_IMAGE_HOSTS
