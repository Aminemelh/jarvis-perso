"""Images : téléchargement vérifié, conversion pour l'écran, et repérage des images d'une page.

Les images sont téléchargées par le cerveau puis envoyées au client dans la
fiche (data:URL). Le navigateur ne contacte jamais le site d'origine : pas de
pistage, et la politique de sécurité de la page (CSP) reste stricte.
"""

from __future__ import annotations

import base64
import html
import re
from collections import OrderedDict
from urllib.parse import urljoin

from .web import FetchError, SafeFetcher

MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_PAGE_BYTES = 800 * 1024

# Signatures binaires : on ne fait pas confiance à l'en-tête Content-Type.
_MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def sniff_image(data: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return None  # SVG et autres formats refusés


class ImageService:
    def __init__(self, fetcher: SafeFetcher, cache_size: int = 40):
        self.fetcher = fetcher
        self._cache: OrderedDict[str, str] = OrderedDict()
        self.cache_size = cache_size

    async def data_url(self, url: str) -> str:
        if url in self._cache:
            self._cache.move_to_end(url)
            return self._cache[url]
        fetched = await self.fetcher.fetch(url, MAX_IMAGE_BYTES, accept="image/avif,image/webp,image/*")
        mime = sniff_image(fetched.body)
        if not mime:
            raise FetchError("Ce lien n'est pas une image reconnue (JPEG, PNG, GIF, WebP, AVIF).")
        data = f"data:{mime};base64," + base64.b64encode(fetched.body).decode("ascii")
        self._cache[url] = data
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return data

    async def fetch_page(self, url: str) -> tuple[str, str]:
        fetched = await self.fetcher.fetch(url, MAX_PAGE_BYTES, accept="text/html,application/xhtml+xml")
        if "html" not in fetched.content_type and not fetched.body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            raise FetchError("Ce lien n'est pas une page web.")
        return fetched.url, fetched.body.decode("utf-8", errors="replace")

    async def page_images(self, url: str, limit: int = 8) -> dict:
        final_url, page = await self.fetch_page(url)
        return {"url": final_url, "title": page_title(page), "images": extract_images(page, final_url, limit)}


_META_IMAGE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:og:image(?::url)?|twitter:image)[\"'][^>]*>", re.I)
_CONTENT = re.compile(r"content=[\"']([^\"']+)[\"']", re.I)
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r"\b(src|data-src|alt|width|height)=[\"']([^\"']*)[\"']", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SKIP = re.compile(r"(logo|icon|sprite|pixel|avatar|badge|button|spacer|tracking|\.svg)", re.I)


def page_title(page: str) -> str:
    m = _TITLE.search(page)
    return html.unescape(" ".join(m.group(1).split()))[:200] if m else ""


def og_image(page: str, base: str) -> str | None:
    for tag in _META_IMAGE.findall(page):
        m = _CONTENT.search(tag)
        if m:
            return urljoin(base, html.unescape(m.group(1)))
    return None


def extract_images(page: str, base: str, limit: int = 8) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    main = og_image(page, base)
    if main:
        found.append({"url": main, "alt": page_title(page), "main": True})
        seen.add(main)
    for tag in _IMG.findall(page):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
        src = attrs.get("src") or attrs.get("data-src") or ""
        if not src or src.startswith("data:") or _SKIP.search(src):
            continue
        if any(attrs.get(k, "").isdigit() and int(attrs[k]) < 120 for k in ("width", "height")):
            continue  # vignettes et icônes
        absolute = urljoin(base, html.unescape(src))
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append({"url": absolute, "alt": html.unescape(attrs.get("alt", ""))[:200]})
        if len(found) >= limit:
            break
    return found
