"""Images, actualités, affichage et pilotage de l'écran."""

import asyncio
import dataclasses

import httpx
import pytest

from fakes import FakeSession
from jarvis_brain.config import Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub
from jarvis_brain.prompts import build_system
from jarvis_brain.services import Services
from jarvis_brain.sources.images import ImageService, extract_images, sniff_image
from jarvis_brain.sources.news import NewsClient, parse_feed
from jarvis_brain.sources.web import FetchError, SafeFetcher
from jarvis_brain.tools import ToolBox, ToolContext
from jarvis_brain.tools.builtin import build_tools

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>Info</title>
<item><title>Tempete sur Montreal</title><link>https://news.example/tempete</link>
  <description>Des vents violents.</description><pubDate>Thu, 29 Oct 2026 08:00:00 -0400</pubDate>
  <media:content url="https://img.example/small.jpg" medium="image" width="300"/>
  <media:content url="https://img.example/big.jpg" medium="image" width="1200"/></item>
<item><title>Election municipale</title><link>https://news.example/election</link>
  <description>&lt;p&gt;Resultats &lt;img src="https://img.example/vote.png"&gt;&lt;/p&gt;</description>
  <pubDate>Thu, 29 Oct 2026 07:00:00 -0400</pubDate></item>
<item><title>Hockey : victoire</title><link>https://news.example/hockey</link>
  <description>Le CH gagne.</description><pubDate>Thu, 29 Oct 2026 06:00:00 -0400</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
<entry><title>Article Atom</title><link rel="alternate" href="https://atom.example/a"/>
<link rel="enclosure" type="image/jpeg" href="https://img.example/atom.jpg"/>
<summary>Resume</summary><updated>2026-10-29T10:00:00Z</updated></entry></feed>"""

ARTICLE = b"""<html><head><title>Hockey</title>
<meta property="og:image" content="/photos/hockey.jpg"></head>
<body><img src="/logo.svg"><img src="/photos/but.jpg" alt="Le but" width="800"><img src="/pix.gif" width="1"></body></html>"""


async def public_resolver(host):
    return {"localhost": ["127.0.0.1"], "intranet.example": ["10.0.0.5"]}.get(host, ["93.184.216.34"])


def mock_web():
    def handler(req: httpx.Request):
        url = str(req.url)
        routes = {
            "https://feed.example/rss": (200, "application/rss+xml", RSS),
            "https://feed.example/atom": (200, "application/atom+xml", ATOM),
            "https://news.example/hockey": (200, "text/html", ARTICLE),
            "https://img.example/big.jpg": (200, "image/jpeg", JPEG),
            "https://img.example/vote.png": (200, "image/png", PNG),
            "https://img.example/fake.jpg": (200, "image/jpeg", b"<svg onload=alert(1)>"),
            "https://news.example/hockey/photos/hockey.jpg": (200, "image/jpeg", JPEG),
            "https://news.example/photos/hockey.jpg": (200, "image/jpeg", JPEG),
            "https://img.example/huge.jpg": (200, "image/jpeg", JPEG + b"\x00" * (4 * 1024 * 1024)),
        }
        if url == "https://img.example/redirect.jpg":
            return httpx.Response(302, headers={"location": "http://intranet.example/secret.jpg"})
        if url not in routes:
            return httpx.Response(404)
        status, ctype, body = routes[url]
        return httpx.Response(status, headers={"content-type": ctype}, content=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return SafeFetcher(http=http, resolver=public_resolver)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- sécurité (SSRF)

@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/x", "http://127.0.0.1/admin", "http://localhost:8765/health",
    "http://192.168.0.1/", "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http://10.1.2.3/",
    "http://intranet.example/", "https://user:pass@example.com/", "https://example.com:8443/",
])
def test_fetcher_refuses_internal_or_odd_addresses(url):
    with pytest.raises(FetchError):
        run(mock_web().check_url(url))


def test_redirect_to_internal_address_is_refused():
    with pytest.raises(FetchError, match="interne"):
        run(mock_web().fetch("https://img.example/redirect.jpg", 10_000))


def test_images_are_sniffed_and_size_limited():
    images = ImageService(mock_web())
    assert run(images.data_url("https://img.example/big.jpg")).startswith("data:image/jpeg;base64,")
    with pytest.raises(FetchError, match="image"):
        run(images.data_url("https://img.example/fake.jpg"))  # se dit JPEG, mais c'est du SVG
    with pytest.raises(FetchError, match="volumineux"):
        run(images.data_url("https://img.example/huge.jpg"))
    assert sniff_image(b"<svg></svg>") is None


# ---------------------------------------------------------------- actualités

def test_parse_rss_and_atom_images():
    rss = parse_feed(RSS, "Info")
    assert [a.title for a in rss] == ["Tempete sur Montreal", "Election municipale", "Hockey : victoire"]
    assert rss[0].image_url == "https://img.example/big.jpg"  # la plus grande
    assert rss[1].image_url == "https://img.example/vote.png"  # <img> dans la description
    assert rss[1].summary == "Resultats"
    assert rss[2].image_url == ""
    atom = parse_feed(ATOM, "Atom")
    assert atom[0].link == "https://atom.example/a" and atom[0].image_url == "https://img.example/atom.jpg"


def test_news_fills_missing_image_from_article_page():
    fetcher = mock_web()
    news = NewsClient((("Info", "https://feed.example/rss"), ("Panne", "https://feed.example/absent")),
                      fetcher, ImageService(fetcher))
    articles, errors = run(news.latest("hockey", 3))
    assert errors == ["Panne"]
    assert len(articles) == 1 and articles[0].image_url == "https://news.example/photos/hockey.jpg"


def test_extract_images_skips_icons():
    found = extract_images(ARTICLE.decode(), "https://news.example/hockey")
    assert [f["url"] for f in found] == ["https://news.example/photos/hockey.jpg", "https://news.example/photos/but.jpg"]


# ---------------------------------------------------------------- affichage et zoom

def make_box(tmp_path, session=None):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    fetcher = mock_web()
    images = ImageService(fetcher)
    services = Services(images=images, news=NewsClient((("Info", "https://feed.example/rss"),), fetcher, images))
    hub = DeviceHub(settings, ConversationStore(settings.history_file))
    session = session or FakeSession("pc")
    hub.register(session)
    return ToolBox(build_tools(settings, services), ToolContext(settings, services, hub, session)), session, settings


def test_show_two_news_photos_then_zoom_on_first(tmp_path):
    box, pc, settings = make_box(tmp_path)
    res = run(box.run("t", "get_news", {"count": 2}))
    assert "big.jpg" in res["content"]

    res = run(box.run("t", "show_on_screen", {"title": "Actualité", "items": [
        {"title": "Tempête sur Montréal", "image_url": "https://img.example/big.jpg"},
        {"title": "Élection municipale", "image_url": "https://img.example/vote.png"},
    ]}))
    assert not res.get("is_error"), res
    assert "1. Tempête sur Montréal (image)" in res["content"]
    card = pc.sent[-1]["card"]
    assert card["layout"] == "gallery"
    assert card["items"][0]["image"].startswith("data:image/jpeg")
    assert "image_url" not in card["items"][0]  # l'adresse d'origine ne part pas vers le navigateur

    # Le modèle sait ce qui est à l'écran au tour suivant.
    context = build_system(settings, "pc", pc.screen)[1]["text"]
    assert "1. Tempête sur Montréal (image); 2. Élection municipale (image)" in context

    res = run(box.run("t", "screen_control", {"action": "zoom", "item": 1}))
    assert "plein écran" in res["content"]
    assert pc.sent[-1] == {"type": "screen", "action": "zoom", "item": 1}
    assert run(box.run("t", "screen_control", {"action": "zoom", "item": 5}))["is_error"]
    run(box.run("t", "screen_control", {"action": "close"}))
    assert pc.sent[-1]["action"] == "close"


def test_broken_image_is_reported_but_card_still_shown(tmp_path):
    box, pc, _ = make_box(tmp_path)
    res = run(box.run("t", "show_on_screen", {"title": "Photos", "items": [
        {"title": "Bonne", "image_url": "https://img.example/big.jpg"},
        {"title": "Interne", "image_url": "http://192.168.0.1/cam.jpg"},
    ]}))
    assert "Images introuvables" in res["content"] and "Interne" in res["content"]
    items = pc.sent[-1]["card"]["items"]
    assert "image" in items[0] and "image" not in items[1]


def test_zoom_without_anything_on_screen(tmp_path):
    box, *_ = make_box(tmp_path)
    assert run(box.run("t", "screen_control", {"action": "zoom", "item": 1}))["is_error"]
