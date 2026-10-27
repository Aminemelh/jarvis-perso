"""Tokens par appareil.

Chaque appareil (PC, voiture, télé, téléphone) a son propre token. On ne stocke
jamais le token en clair : seulement son empreinte SHA-256. Un appareil perdu ou
volé se révoque sans toucher aux autres.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

TOKEN_PREFIX = "jv_"
DEVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Device:
    name: str
    token_sha256: str
    created_at: float


class DeviceRegistry:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> list[Device]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return [Device(**d) for d in raw.get("devices", [])]

    def _save(self, devices: list[Device]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"devices": [asdict(d) for d in devices]}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:  # Windows : chmod partiel, sans conséquence
            pass

    def list(self) -> list[Device]:
        with self._lock:
            return self._load()

    def add(self, name: str) -> str:
        """Crée un appareil et renvoie son token en clair (affiché une seule fois)."""
        if not DEVICE_NAME_RE.match(name):
            raise ValueError(
                "Nom d'appareil invalide : minuscules, chiffres, '-' ou '_', 32 caractères max."
            )
        with self._lock:
            devices = self._load()
            if any(d.name == name for d in devices):
                raise ValueError(f"L'appareil '{name}' existe déjà. Révoque-le d'abord.")
            token = TOKEN_PREFIX + secrets.token_urlsafe(32)
            devices.append(Device(name, hash_token(token), time.time()))
            self._save(devices)
            return token

    def revoke(self, name: str) -> bool:
        with self._lock:
            devices = self._load()
            kept = [d for d in devices if d.name != name]
            if len(kept) == len(devices):
                return False
            self._save(kept)
            return True

    def verify(self, token: str) -> str | None:
        """Renvoie le nom de l'appareil si le token est valide, sinon None."""
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
            return None
        digest = hash_token(token)
        match: str | None = None
        # On compare avec toutes les entrées, en temps constant, sans sortir tôt.
        for device in self.list():
            if hmac.compare_digest(digest, device.token_sha256):
                match = device.name
        return match


class AuthRateLimiter:
    """Bloque une IP après trop d'échecs d'authentification (anti force brute)."""

    def __init__(self, max_failures: int = 5, window_seconds: float = 60.0):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, ip: str, now: float) -> list[float]:
        stamps = [t for t in self._failures.get(ip, []) if now - t < self.window]
        self._failures[ip] = stamps
        return stamps

    def blocked(self, ip: str) -> bool:
        with self._lock:
            return len(self._recent(ip, time.monotonic())) >= self.max_failures

    def fail(self, ip: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(ip, now).append(now)

    def success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
