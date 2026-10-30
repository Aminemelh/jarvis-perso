"""Spotify : chercher et lancer de la musique, piloter la lecture (Spotify Premium requis pour piloter)."""

from __future__ import annotations

from typing import Awaitable, Callable

import httpx

from ..oauth import OAuthClient

API = "https://api.spotify.com/v1"
KINDS = ("track", "playlist", "album", "artist")


class SpotifyError(RuntimeError):
    pass


class SpotifyClient:
    def __init__(self, oauth: OAuthClient, http: httpx.AsyncClient | None = None,
                 wake_player: Callable[[], Awaitable[bool]] | None = None):
        self.oauth = oauth
        self.http = http or httpx.AsyncClient(timeout=15)
        self.wake_player = wake_player  # ex. ouvrir l'appli Spotify sur le PC si aucun lecteur n'est actif

    async def _call(self, method: str, path: str, **kw) -> httpx.Response:
        token = await self.oauth.access_token()
        r = await self.http.request(method, API + path, headers={"Authorization": f"Bearer {token}"}, **kw)
        if r.status_code == 403 and "PREMIUM" in r.text.upper():
            raise SpotifyError("Piloter la lecture demande un compte Spotify Premium.")
        if r.status_code == 401:
            raise SpotifyError("Connexion Spotify expirée : python -m jarvis_brain connect spotify")
        return r

    async def search(self, query: str, kind: str = "track", limit: int = 5) -> list[dict]:
        r = await self._call("GET", "/search", params={"q": query, "type": kind, "limit": limit, "market": "from_token"})
        r.raise_for_status()
        items = (r.json().get(kind + "s") or {}).get("items") or []
        out = []
        for it in items:
            if not it:
                continue
            artist = ", ".join(a["name"] for a in it.get("artists", [])[:2]) or (it.get("owner") or {}).get("display_name", "")
            out.append({"name": it.get("name"), "by": artist, "uri": it.get("uri"), "kind": kind})
        return out

    async def devices(self) -> list[dict]:
        r = await self._call("GET", "/me/player/devices")
        r.raise_for_status()
        return r.json().get("devices", [])

    async def _player(self, method: str, path: str, **kw) -> None:
        """Commande de lecture ; si aucun lecteur n'est actif, en réveille un et réessaie."""
        r = await self._call(method, "/me/player" + path, **kw)
        if r.status_code == 404:
            device_id = await self._pick_device()
            params = dict(kw.pop("params", {}) or {}, device_id=device_id)
            r = await self._call(method, "/me/player" + path, params=params, **kw)
        if r.status_code >= 400:
            raise SpotifyError(f"Spotify a refusé la commande (HTTP {r.status_code}).")

    async def _pick_device(self) -> str:
        found = await self.devices()
        if not found and self.wake_player and await self.wake_player():
            found = await self.devices()
        if not found:
            raise SpotifyError("Aucun lecteur Spotify ouvert : lance Spotify sur ton PC ou ton téléphone.")
        computer = [d for d in found if d.get("type") == "Computer"]
        return (computer or found)[0]["id"]

    async def play(self, query: str | None = None, kind: str = "track") -> str:
        if not query:
            await self._player("PUT", "/play")
            return "Lecture reprise."
        results = await self.search(query, kind, 1)
        if not results:
            raise SpotifyError(f"Rien trouvé sur Spotify pour « {query} ».")
        item = results[0]
        body = {"uris": [item["uri"]]} if kind == "track" else {"context_uri": item["uri"]}
        await self._player("PUT", "/play", json=body)
        return f"Je lance {item['name']}" + (f" de {item['by']}" if item["by"] else "") + "."

    async def control(self, action: str, value: int | None = None) -> str:
        if action == "pause":
            await self._player("PUT", "/pause")
            return "Pause."
        if action == "next":
            await self._player("POST", "/next")
            return "Morceau suivant."
        if action == "previous":
            await self._player("POST", "/previous")
            return "Morceau précédent."
        if action == "volume":
            await self._player("PUT", "/volume", params={"volume_percent": max(0, min(100, value or 50))})
            return f"Volume Spotify à {value} %."
        if action == "shuffle":
            await self._player("PUT", "/shuffle", params={"state": "true" if value else "false"})
            return "Lecture aléatoire " + ("activée." if value else "désactivée.")
        raise SpotifyError(f"Action inconnue : {action}")

    async def now_playing(self) -> dict:
        r = await self._call("GET", "/me/player/currently-playing")
        if r.status_code == 204 or not r.content:
            return {"playing": False}
        data = r.json()
        item = data.get("item") or {}
        return {"playing": data.get("is_playing", False), "title": item.get("name"),
                "by": ", ".join(a["name"] for a in item.get("artists", [])[:3]),
                "album": (item.get("album") or {}).get("name")}
