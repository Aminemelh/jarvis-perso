"""Connexion OAuth (Spotify, Google) : tu autorises Jarvis sur la page officielle, une seule fois.

Jarvis ne voit jamais ton mot de passe. Il reçoit un jeton limité à ce que tu
as accepté (ex. « gérer la lecture Spotify », « créer des événements »),
révocable à tout moment depuis ton compte. Flux « Authorization Code + PKCE »
avec redirection vers ton propre PC (127.0.0.1), comme les applications de bureau.

  python -m jarvis_brain connect spotify
  python -m jarvis_brain connect google
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

REDIRECT_PORT = 8888
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"


@dataclass(frozen=True)
class Provider:
    name: str
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    extra_auth_params: tuple[tuple[str, str], ...] = ()


SPOTIFY = Provider(
    "spotify", "https://accounts.spotify.com/authorize", "https://accounts.spotify.com/api/token",
    ("user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing"),
)
GOOGLE = Provider(
    "google", "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
    ("https://www.googleapis.com/auth/calendar.events",),
    (("access_type", "offline"), ("prompt", "consent")),
)
PROVIDERS = {"spotify": SPOTIFY, "google": GOOGLE}


class OAuthError(RuntimeError):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class OAuthClient:
    def __init__(self, provider: Provider, client_id: str, client_secret: str, data_dir: Path,
                 http: httpx.AsyncClient | None = None):
        self.provider = provider
        self.client_id = client_id
        self.client_secret = client_secret
        self.path = data_dir / "oauth" / f"{provider.name}.json"
        self.http = http or httpx.AsyncClient(timeout=20)
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return bool(self.client_id) and self.path.exists()

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _store(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def _token_body(self, extra: dict) -> dict:
        body = {"client_id": self.client_id, **extra}
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return body

    async def _token_request(self, body: dict) -> dict:
        r = await self.http.post(self.provider.token_url, data=self._token_body(body))
        if r.status_code != 200:
            raise OAuthError(f"{self.provider.name} a refusé le jeton (HTTP {r.status_code}). "
                             f"Reconnecte : python -m jarvis_brain connect {self.provider.name}")
        data = r.json()
        data["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
        return data

    async def access_token(self) -> str:
        """Jeton valide, renouvelé automatiquement quand il expire."""
        async with self._lock:
            if not self.path.exists():
                raise OAuthError(f"{self.provider.name} n'est pas connecté : python -m jarvis_brain connect "
                                 f"{self.provider.name}")
            tokens = self._load()
            if tokens.get("expires_at", 0) > time.time():
                return tokens["access_token"]
            fresh = await self._token_request({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
            fresh.setdefault("refresh_token", tokens["refresh_token"])  # Google ne le renvoie pas toujours
            self._store(fresh)
            return fresh["access_token"]

    # ---- connexion initiale (ligne de commande, sur le PC) ----

    def authorize_url(self, state: str, challenge: str) -> str:
        params = {
            "client_id": self.client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
            "scope": " ".join(self.provider.scopes), "state": state,
            "code_challenge_method": "S256", "code_challenge": challenge,
            **dict(self.provider.extra_auth_params),
        }
        return f"{self.provider.auth_url}?{urlencode(params)}"

    def connect_interactive(self, timeout: float = 300) -> None:
        if not self.client_id:
            raise OAuthError("Identifiant d'application manquant dans .env (voir docs/CONNECTIONS.md).")
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(16)
        code = _wait_for_code(self.authorize_url(state, challenge), state, timeout)
        tokens = asyncio.run(self._token_request({
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        }))
        if "refresh_token" not in tokens:
            raise OAuthError("Aucun jeton de renouvellement reçu : réessaie.")
        self._store(tokens)


def _wait_for_code(url: str, state: str, timeout: float) -> str:
    result: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = parse_qs(urlsplit(self.path).query)
            if urlsplit(self.path).path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            if query.get("state", [""])[0] != state:  # protection contre la falsification (CSRF)
                result["error"] = "state invalide"
            elif "error" in query:
                result["error"] = query["error"][0]
            else:
                result["code"] = query.get("code", [""])[0]
            ok = "code" in result
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = "Jarvis est connecté. Tu peux fermer cet onglet." if ok else "Connexion refusée ou invalide."
            self.wfile.write(f"<!doctype html><meta charset=utf-8><p style='font:20px sans-serif'>{message}".encode())
            done.set()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)  # écoute uniquement en local
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"\nOuvre cette page si le navigateur ne s'ouvre pas tout seul :\n{url}\n")
    webbrowser.open(url)
    try:
        if not done.wait(timeout):
            raise OAuthError("Délai dépassé : aucune autorisation reçue.")
    finally:
        server.shutdown()
    if "code" not in result:
        raise OAuthError(f"Autorisation refusée : {result.get('error')}")
    return result["code"]
