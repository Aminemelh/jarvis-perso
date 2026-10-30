"""Connexions OAuth (PKCE), Spotify et ajout à Google Agenda, avec de faux serveurs."""

import asyncio
import base64
import dataclasses
import hashlib
import json
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import jarvis_brain.oauth as oauth_mod
from fakes import FakeSession
from jarvis_brain.config import Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub
from jarvis_brain.oauth import GOOGLE, SPOTIFY, OAuthClient, OAuthError, pkce_pair
from jarvis_brain.services import Services
from jarvis_brain.sources.gcal import GoogleCalendarWriter
from jarvis_brain.sources.spotify import SpotifyClient, SpotifyError
from jarvis_brain.tools import ToolBox, ToolContext
from jarvis_brain.tools.builtin import build_tools


def run(coro):
    return asyncio.run(coro)


def stored_client(tmp_path, provider=SPOTIFY, handler=None, expired=False):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler or (lambda r: httpx.Response(500))))
    client = OAuthClient(provider, "client-id", "", tmp_path, http=http)
    client._store({"access_token": "ancien", "refresh_token": "rt", "expires_at": time.time() + (-10 if expired else 3600)})
    return client


# ---------------------------------------------------------------- OAuth

def test_pkce_challenge_matches_verifier():
    verifier, challenge = pkce_pair()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert 43 <= len(verifier) <= 128


def test_authorize_url_has_pkce_state_and_local_redirect(tmp_path):
    url = OAuthClient(GOOGLE, "cid", "secret", tmp_path).authorize_url("etat", "defi")
    q = parse_qs(urlsplit(url).query)
    assert q["redirect_uri"] == ["http://127.0.0.1:8888/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["state"] == ["etat"] and q["access_type"] == ["offline"]
    assert q["scope"] == ["https://www.googleapis.com/auth/calendar.events"]  # rien de plus que les événements


def test_expired_token_is_refreshed_and_saved(tmp_path):
    def handler(req):
        body = parse_qs(req.content.decode())
        assert body["grant_type"] == ["refresh_token"] and body["refresh_token"] == ["rt"]
        return httpx.Response(200, json={"access_token": "neuf", "expires_in": 3600})

    client = stored_client(tmp_path, handler=handler, expired=True)
    assert run(client.access_token()) == "neuf"
    saved = json.loads(client.path.read_text())
    assert saved["refresh_token"] == "rt" and saved["access_token"] == "neuf"
    assert run(client.access_token()) == "neuf"  # plus de requête : encore valide


def test_callback_rejects_forged_state(monkeypatch):
    monkeypatch.setattr(oauth_mod.webbrowser, "open", lambda url: None)

    def visit(path):
        time.sleep(0.3)
        try:
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # pas de proxy pour 127.0.0.1
            direct.open(f"http://127.0.0.1:8888{path}", timeout=5)
        except Exception:  # noqa: BLE001 - réponse 400 attendue
            pass

    threading.Thread(target=visit, args=("/callback?code=vole&state=faux",)).start()
    with pytest.raises(OAuthError, match="state invalide"):
        oauth_mod._wait_for_code("https://auth.example", "vrai", timeout=5)

    threading.Thread(target=visit, args=("/callback?code=abc&state=vrai",)).start()
    assert oauth_mod._wait_for_code("https://auth.example", "vrai", timeout=5) == "abc"


# ---------------------------------------------------------------- Spotify

class FakeSpotify:
    def __init__(self, active=True, devices=(), premium=True):
        self.active, self.devices, self.premium = active, list(devices), premium
        self.calls = []

    def __call__(self, req: httpx.Request):
        path = req.url.path.replace("/v1", "")
        self.calls.append((req.method, path, dict(req.url.params), req.content.decode() or None))
        assert req.headers["authorization"] == "Bearer ancien"
        if path == "/search":
            return httpx.Response(200, json={"tracks": {"items": [
                {"name": "One More Time", "uri": "spotify:track:1", "artists": [{"name": "Daft Punk"}]}]}})
        if path == "/me/player/devices":
            return httpx.Response(200, json={"devices": self.devices})
        if not self.premium:
            return httpx.Response(403, json={"error": {"reason": "PREMIUM_REQUIRED"}})
        if not self.active and "device_id" not in req.url.params:
            return httpx.Response(404, json={"error": {"reason": "NO_ACTIVE_DEVICE"}})
        return httpx.Response(204)


def spotify(tmp_path, fake, wake=None):
    oauth = stored_client(tmp_path)
    return SpotifyClient(oauth, http=httpx.AsyncClient(transport=httpx.MockTransport(fake)), wake_player=wake)


def test_play_a_song(tmp_path):
    fake = FakeSpotify()
    assert run(spotify(tmp_path, fake).play("one more time")) == "Je lance One More Time de Daft Punk."
    method, path, _, body = fake.calls[-1]
    assert (method, path) == ("PUT", "/me/player/play") and json.loads(body) == {"uris": ["spotify:track:1"]}


def test_no_active_player_opens_spotify_on_pc_then_plays(tmp_path):
    fake = FakeSpotify(active=False)
    woke = []

    async def wake():
        woke.append(True)
        fake.devices = [{"id": "pc-123", "type": "Computer"}]
        return True

    run(spotify(tmp_path, fake, wake).control("next"))
    assert woke and fake.calls[-1][2] == {"device_id": "pc-123"}


def test_premium_and_no_device_errors(tmp_path):
    with pytest.raises(SpotifyError, match="Premium"):
        run(spotify(tmp_path, FakeSpotify(premium=False)).control("pause"))
    with pytest.raises(SpotifyError, match="Aucun lecteur"):
        run(spotify(tmp_path, FakeSpotify(active=False)).control("pause"))


# ---------------------------------------------------------------- Google Agenda

def test_calendar_add_tool(tmp_path):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"id": "e1", "htmlLink": "https://calendar.google.com/e1"})

    writer = GoogleCalendarWriter(stored_client(tmp_path, GOOGLE), "America/Toronto",
                                  http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    services = Services(calendar_writer=writer)
    hub = DeviceHub(settings, ConversationStore(settings.history_file))
    box = ToolBox(build_tools(settings, services), ToolContext(settings, services, hub, FakeSession()))

    res = run(box.run("t", "calendar_add", {"title": "Dentiste", "start": "2026-11-05T14:00",
                                            "location": "Clinique du Plateau", "reminder_minutes": 60}))
    assert "Dentiste" in res["content"] and "2026-11-05 14:00" in res["content"]
    body = sent[-1]
    assert body["start"] == {"dateTime": "2026-11-05T14:00:00-05:00", "timeZone": "America/Toronto"}
    assert body["end"]["dateTime"] == "2026-11-05T15:00:00-05:00"
    assert body["reminders"]["overrides"] == [{"method": "popup", "minutes": 60}]

    run(box.run("t", "calendar_add", {"title": "Déménagement", "start": "2026-11-07", "all_day": True}))
    assert sent[-1]["start"] == {"date": "2026-11-07"} and sent[-1]["end"] == {"date": "2026-11-08"}

    bad = run(box.run("t", "calendar_add", {"title": "x", "start": "2026-11-05T14:00", "end": "2026-11-05T13:00"}))
    assert bad["is_error"]


def test_tools_absent_until_connected(tmp_path):
    tools = build_tools(dataclasses.replace(Settings(), data_dir=tmp_path), Services())
    assert "music_play" not in tools and "calendar_add" not in tools
