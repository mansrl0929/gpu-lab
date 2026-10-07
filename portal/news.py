"""Research news feeds. Read-only RSS fetching with an in-memory cache.

Images are proxied through the portal so the page keeps its strict image policy and
browsers never talk to the news CDNs directly.
"""
import asyncio
import html
import ipaddress
import re
import socket
import time
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

CACHE_SECONDS = 1800
TIMEOUT = 12
PER_CATEGORY = 18
THUMBNAILS_TO_FETCH = PER_CATEGORY

# 국내는 언론사 RSS를 직접 읽습니다. 구글 뉴스 링크는 실제 기사 주소를 알 수 없어
# 썸네일이 전부 구글 로고로 나옵니다.
KOREAN_FEEDS = [
    'https://www.yna.co.kr/rss/news.xml',
    'https://www.yna.co.kr/rss/society.xml',
    'https://rss.etnews.com/Section901.xml',
    'https://rss.etnews.com/Section902.xml',
    'https://feeds.feedburner.com/zdkorea',
    'https://www.hankyung.com/feed/it',
    'https://rss.donga.com/science.xml',
    'https://www.khan.co.kr/rss/rssdata/science_news.xml',
    'https://www.hani.co.kr/rss/science/',
    'https://www.hani.co.kr/rss/',
]

OUTLETS = {
    'www.yna.co.kr': '연합뉴스', 'rss.etnews.com': '전자신문', 'www.etnews.com': '전자신문',
    'feeds.feedburner.com': 'ZDNet코리아', 'zdnet.co.kr': 'ZDNet코리아',
    'www.hankyung.com': '한국경제', 'rss.donga.com': '동아사이언스', 'www.donga.com': '동아일보',
    'www.khan.co.kr': '경향신문', 'www.hani.co.kr': '한겨레',
    'phys.org': 'phys.org', 'www.esa.int': 'ESA',
}

REGIONS = [
    {'id': 'world', 'name': '해외', 'categories': [
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
    ]},
    {'id': 'korea', 'name': '국내', 'categories': [
        {'id': 'polar-kr', 'name': '극지 · 해빙', 'note': '북극·남극, 해빙, 극지연구소',
         'feeds': KOREAN_FEEDS,
         'keywords': ['해빙', '북극', '남극', '극지', '빙하', '빙상', '영구동토', '그린란드',
                      '아라온', '쇄빙', '남극세종', '기후변화']},
        {'id': 'remote-kr', 'name': '원격탐사 · 위성', 'note': '지구관측, 위성영상, 국토위성',
         'feeds': KOREAN_FEEDS,
         'keywords': ['위성', '원격탐사', '지구관측', '국토위성', '아리랑', '천리안', '차세대중형위성',
                      '영상레이더', '관측 영상', '항공영상', '누리호'],
         'exclude': ['위성방송', '위성도시']},
        {'id': 'ai-kr', 'name': 'AI · 머신러닝', 'note': '국내 인공지능 연구와 산업',
         'feeds': KOREAN_FEEDS,
         'keywords': ['인공지능', 'ai', '머신러닝', '딥러닝', '거대언어모델', 'llm', '생성형',
                      '파운데이션 모델', '초거대']},
    ]},
]

MAX_IMAGE_BYTES = 6 * 1024 * 1024
THUMBNAIL_WIDTH = 400


def shrink(data, kind):
    """Serve a card-sized picture. Full-resolution news photos make the page slow."""
    try:
        from io import BytesIO
        from PIL import Image
        image = Image.open(BytesIO(data))
        if image.width <= THUMBNAIL_WIDTH:
            return data, kind
        image.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 2), Image.LANCZOS)
        buffer = BytesIO()
        image.convert('RGB').save(buffer, 'JPEG', quality=78, optimize=True)
        return buffer.getvalue(), 'image/jpeg'
    except Exception:
        return data, kind
_cache = {'at': 0.0, 'data': None}
_lock = asyncio.Lock()


def _text(value):
    """Strip tags and entities so a feed summary reads as plain text."""
    without_tags = re.sub(r'<[^>]+>', ' ', value or '')
    return re.sub(r'\s+', ' ', html.unescape(without_tags)).strip()


def _upgrade(url):
    """phys.org ships a 2 KB thumbnail in the feed; the 800 px version is the same path."""
    return url.replace('/csz/news/tmb/', '/csz/news/800a/') if url else url


def allowed_image(url):
    """Any public https image may be proxied; private and loopback addresses never are."""
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host == 'localhost' or host.endswith(('.local', '.internal')):
        return False
    try:
        ipaddress.ip_address(host)
        return False  # 숫자 주소는 받지 않습니다.
    except ValueError:
        pass
    try:
        for *_, address in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(address[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (socket.gaierror, ValueError, IndexError):
        # 이름을 못 찾으면 어차피 연결도 실패합니다. 여기서 모든 이미지를 막지는 않습니다.
        return '.' in host
    return True


def _image(item):
    for tag in ('{http://search.yahoo.com/mrss/}thumbnail', '{http://search.yahoo.com/mrss/}content', 'enclosure'):
        for node in item.iter(tag):
            url = node.get('url')
            if url and urlparse(url).scheme == 'https':
                return _upgrade(url)
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
        # 구글 뉴스 제목은 "제목 - 매체" 형식이라 매체를 떼어 출처로 씁니다.
        outlet = source
        if source == 'news.google.com' and ' - ' in title:
            title, outlet = title.rsplit(' - ', 1)
        items.append({'title': title, 'link': link, 'source': outlet,
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
                                    headers={'User-Agent': 'Mozilla/5.0 (compatible; gpu-lab-portal/1.0)'})
        response.raise_for_status()
        host = urlparse(url).hostname or url
        return parse_feed(response.text, OUTLETS.get(host, host))
    except Exception:
        return []


THUMBNAIL_PATTERNS = (
    r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
)


async def _thumbnail(client, item, limit):
    """Feeds without images still have one on the article page, whatever CDN hosts it."""
    async with limit:
        try:
            page = await client.get(item['link'], timeout=8, follow_redirects=True,
                                    headers={'User-Agent': 'Mozilla/5.0 (compatible; gpu-lab-portal/1.0)'})
            for pattern in THUMBNAIL_PATTERNS:
                found = re.search(pattern, page.text)
                if found and found.group(1).startswith('https://'):
                    item['image'] = html.unescape(found.group(1))
                    return
        except Exception:
            pass


async def collect():
    """Every region with its categories and articles. Cached, so a page refresh costs nothing."""
    async with _lock:
        if _cache['data'] and time.monotonic()-_cache['at'] < CACHE_SECONDS:
            return _cache['data']
        urls = sorted({url for region in REGIONS for category in region['categories'] for url in category['feeds']})
        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(*(_fetch(client, url) for url in urls))
            by_url = dict(zip(urls, results))
            regions, missing = [], []
            for region in REGIONS:
                categories = []
                for category in region['categories']:
                    seen, items = set(), []
                    for url in category['feeds']:
                        for item in by_url[url]:
                            if item['link'] in seen or not _matches(item, category['keywords'], category.get('exclude', ())):
                                continue
                            seen.add(item['link'])
                            items.append(item)
                    items = items[:PER_CATEGORY]
                    missing += [item for item in items if not item['image']][:THUMBNAILS_TO_FETCH]
                    categories.append({'id': category['id'], 'name': category['name'],
                                       'note': category['note'], 'items': items})
                regions.append({'id': region['id'], 'name': region['name'], 'categories': categories})
            limit = asyncio.Semaphore(8)
            await asyncio.gather(*(_thumbnail(client, item, limit) for item in missing))
        data = {'regions': regions, 'fetched_at': time.time()}
        if any(category['items'] for region in regions for category in region['categories']):
            _cache.update(at=time.monotonic(), data=data)
        return data


