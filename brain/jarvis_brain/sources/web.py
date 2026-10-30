"""Téléchargement sûr de pages et d'images du web.

Les adresses viennent du modèle ou de pages web : elles ne sont pas fiables.
Sans précaution, « affiche l'image http://192.168.0.1/admin » ferait lire au
cerveau une page de ton réseau local (attaque SSRF). Donc :
- http/https uniquement, ports 80 et 443, pas d'identifiants dans l'adresse ;
- l'adresse IP de destination doit être publique (ni locale, ni privée, ni
  réservée), vérifiée à chaque redirection ;
- taille et durée limitées.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; JarvisPerso/0.3)"
MAX_REDIRECTS = 3

Resolver = Callable[[str], Awaitable[list[str]]]


class FetchError(Exception):
    pass


@dataclass
class Fetched:
    url: str
    content_type: str
    body: bytes


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class SafeFetcher:
    def __init__(self, http: httpx.AsyncClient | None = None, resolver: Resolver = system_resolver,
                 timeout: float = 10.0):
        self.http = http or httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                                              headers={"User-Agent": USER_AGENT})
        self.resolver = resolver

    async def check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise FetchError("Seules les adresses http et https sont autorisées.")
        if parts.username or parts.password:
            raise FetchError("Adresse avec identifiants refusée.")
        if parts.port not in (None, 80, 443):
            raise FetchError("Port non autorisé.")
        host = parts.hostname or ""
        if not host:
            raise FetchError("Adresse invalide.")
        try:
            addresses = [host] if _is_ip(host) else await self.resolver(host)
        except OSError as e:
            raise FetchError(f"Nom de domaine introuvable : {host}") from e
        if not addresses:
            raise FetchError(f"Nom de domaine introuvable : {host}")
        for addr in addresses:
            if not ipaddress.ip_address(addr).is_global:
                raise FetchError("Adresse interne ou réservée refusée.")

    async def fetch(self, url: str, max_bytes: int, accept: str = "*/*") -> Fetched:
        for _ in range(MAX_REDIRECTS + 1):
            await self.check_url(url)
            async with self.http.stream("GET", url, headers={"Accept": accept}) as r:
                if r.is_redirect:
                    location = r.headers.get("location")
                    if not location:
                        raise FetchError("Redirection sans destination.")
                    url = urljoin(url, location)
                    continue
                if r.status_code != 200:
                    raise FetchError(f"Le site a répondu {r.status_code}.")
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError("Fichier trop volumineux.")
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise FetchError("Fichier trop volumineux.")
                content_type = r.headers.get("content-type", "").split(";")[0].strip().lower()
                return Fetched(str(r.url), content_type, bytes(body))
        raise FetchError("Trop de redirections.")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
