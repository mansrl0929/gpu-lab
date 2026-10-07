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

GOOGLE = 'https://news.google.com/rss/search?q={}&hl=ko&gl=KR&ceid=KR:ko'

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
         'feeds': [GOOGLE.format('%EB%B6%81%EA%B7%B9+%ED%95%B4%EB%B9%99+OR+%EB%82%A8%EA%B7%B9+OR+%EA%B7%B9%EC%A7%80%EC%97%B0%EA%B5%AC%EC%86%8C')],
         'keywords': []},
        {'id': 'remote-kr', 'name': '원격탐사 · 위성', 'note': '지구관측, 위성영상, 국토위성',
         'feeds': [GOOGLE.format('%EC%9B%90%EA%B2%A9%ED%83%90%EC%82%AC+OR+%EC%9C%84%EC%84%B1%EC%98%81%EC%83%81+OR+%EC%A7%80%EA%B5%AC%EA%B4%80%EC%B8%A1%EC%9C%84%EC%84%B1')],
         'keywords': []},
        {'id': 'ai-kr', 'name': 'AI · 머신러닝', 'note': '국내 인공지능 연구와 산업',
         'feeds': [GOOGLE.format('%EC%9D%B8%EA%B3%B5%EC%A7%80%EB%8A%A5+%EC%97%B0%EA%B5%AC+OR+AI+%EB%AA%A8%EB%8D%B8')],
         'keywords': []},
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
        return parse_feed(response.text, urlparse(url).hostname or url)
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


