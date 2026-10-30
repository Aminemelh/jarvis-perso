"""Actualités via flux RSS/Atom (gratuit, sans clé), avec image pour chaque article.

L'image vient du flux (media:content, media:thumbnail, enclosure, <img> du
résumé) ou, à défaut, de la page de l'article (balise og:image).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime

import httpx

from .images import ImageService, og_image
from .web import FetchError, SafeFetcher

log = logging.getLogger(__name__)

MAX_FEED_BYTES = 2 * 1024 * 1024
_NS = {
    "media": "http://search.yahoo.com/mrss/",
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
}
_IMG_IN_HTML = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: str
    image_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _clean(raw: str, limit: int) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", raw)).split())[:limit]


def _date(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return raw


def _image_of(item: ET.Element, description_html: str) -> str:
    candidates = []
    for tag in ("media:content", "media:thumbnail", "media:group/media:content"):
        for el in item.findall(tag, _NS):
            url = el.get("url", "")
            medium = el.get("medium", "") or el.get("type", "")
            if url and (not medium or "image" in medium):
                candidates.append((int(el.get("width", "0") or 0), url))
    if candidates:
        return max(candidates)[1]  # la plus grande
    for el in item.findall("enclosure"):
        if el.get("type", "").startswith("image") and el.get("url"):
            return el.get("url")
    for el in item.findall("atom:link", _NS):
        if el.get("rel") == "enclosure" and el.get("type", "").startswith("image"):
            return el.get("href", "")
    m = _IMG_IN_HTML.search(description_html)
    return html.unescape(m.group(1)) if m else ""


def parse_feed(xml: bytes, source: str, limit: int = 30) -> list[Article]:
    root = ET.fromstring(xml)  # expat récent : protégé contre les « billion laughs »
    articles: list[Article] = []
    items = root.findall("./channel/item") or root.findall("atom:entry", _NS)
    for item in items[:limit]:
        if item.tag.endswith("entry"):  # Atom
            link_el = next((l for l in item.findall("atom:link", _NS) if l.get("rel", "alternate") == "alternate"), None)
            link = link_el.get("href", "") if link_el is not None else ""
            desc = _text(item.find("atom:summary", _NS)) or _text(item.find("atom:content", _NS))
            title = _text(item.find("atom:title", _NS))
            published = _text(item.find("atom:published", _NS)) or _text(item.find("atom:updated", _NS))
        else:  # RSS 2.0
            link = _text(item.find("link"))
            desc = _text(item.find("description")) or _text(item.find("content:encoded", _NS))
            title = _text(item.find("title"))
            published = _text(item.find("pubDate"))
        if not title:
            continue
        articles.append(Article(
            title=_clean(title, 200), summary=_clean(desc, 400), link=link.strip(), source=source,
            published=_date(published), image_url=_image_of(item, desc),
        ))
    return articles


class NewsClient:
    def __init__(self, feeds: tuple[tuple[str, str], ...], fetcher: SafeFetcher, images: ImageService,
                 cache_seconds: int = 600):
        self.feeds = feeds
        self.fetcher = fetcher
        self.images = images
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, list[Article]]] = {}

    async def _feed(self, name: str, url: str) -> list[Article]:
        cached = self._cache.get(url)
        if cached and time.monotonic() - cached[0] < self.cache_seconds:
            return cached[1]
        fetched = await self.fetcher.fetch(url, MAX_FEED_BYTES, accept="application/rss+xml,application/atom+xml,text/xml")
        articles = await asyncio.to_thread(parse_feed, fetched.body, name)
        self._cache[url] = (time.monotonic(), articles)
        return articles

    async def latest(self, query: str | None = None, count: int = 5) -> tuple[list[Article], list[str]]:
        results = await asyncio.gather(*(self._feed(n, u) for n, u in self.feeds), return_exceptions=True)
        articles, errors = [], []
        for (name, _), res in zip(self.feeds, results):
            if isinstance(res, Exception):
                log.warning("Flux %s indisponible : %s", name, res)
                errors.append(name)
            else:
                articles.extend(res)
        if query:
            words = [w for w in query.lower().split() if len(w) > 2]
            articles = [a for a in articles if any(w in (a.title + " " + a.summary).lower() for w in words)]
        # Alterner les sources (pas cinq articles du même journal), puis les plus récents.
        articles.sort(key=lambda a: a.published, reverse=True)
        picked = _interleave(articles, count)
        await asyncio.gather(*(self._fill_image(a) for a in picked if not a.image_url))
        return picked, errors

    async def _fill_image(self, article: Article) -> None:
        try:
            final_url, page = await self.images.fetch_page(article.link)
            article.image_url = og_image(page, final_url) or ""
        except (FetchError, httpx.HTTPError) as e:
            log.info("Pas d'image pour %s : %s", article.link, e)


def _interleave(articles: list[Article], count: int) -> list[Article]:
    by_source: dict[str, list[Article]] = {}
    for a in articles:
        by_source.setdefault(a.source, []).append(a)
    picked: list[Article] = []
    while len(picked) < count and any(by_source.values()):
        for items in by_source.values():
            if items and len(picked) < count:
                picked.append(items.pop(0))
    return picked
