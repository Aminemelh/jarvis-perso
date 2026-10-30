"""Notifications « Web Push » : une vraie notification sur le téléphone, page fermée.

Le navigateur s'abonne auprès de son service de notifications (Google pour
Chrome/Android, Mozilla pour Firefox, Microsoft pour Edge, Apple pour Safari)
et nous transmet l'adresse d'abonnement. Le cerveau y envoie un message
chiffré de bout en bout (seul ton navigateur peut le lire), signé avec notre
clé VAPID (data/vapid.pem, générée au premier lancement).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization

log = logging.getLogger(__name__)

# On n'envoie qu'aux services de notification connus : un appareil (même
# authentifié) ne peut pas faire envoyer des requêtes vers n'importe quel site.
PUSH_HOSTS = (
    "fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
    ".notify.windows.com", ".push.apple.com",
)


def allowed_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    return parts.scheme == "https" and parts.port in (None, 443) and any(
        host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


@dataclass
class Subscription:
    device: str
    endpoint: str
    p256dh: str
    auth: str

    def info(self) -> dict:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


class PushService:
    def __init__(self, data_dir: Path, contact: str = "mailto:jarvis@localhost"):
        from py_vapid import Vapid

        self.key_file = data_dir / "vapid.pem"
        self.subs_file = data_dir / "push_subscriptions.json"
        self.contact = contact
        self._lock = threading.Lock()
        data_dir.mkdir(parents=True, exist_ok=True)
        if self.key_file.exists():
            self.vapid = Vapid.from_file(str(self.key_file))
        else:
            self.vapid = Vapid()
            self.vapid.generate_keys()
            self.vapid.save_key(str(self.key_file))
            try:
                self.key_file.chmod(0o600)
            except OSError:
                pass
        self.subs: list[Subscription] = self._load()

    @property
    def public_key(self) -> str:
        """Clé publique au format attendu par pushManager.subscribe (base64url, point non compressé)."""
        raw = self.vapid.public_key.public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def _load(self) -> list[Subscription]:
        if not self.subs_file.exists():
            return []
        try:
            return [Subscription(**s) for s in json.loads(self.subs_file.read_text(encoding="utf-8"))]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save(self) -> None:
        tmp = self.subs_file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(s) for s in self.subs], indent=2), encoding="utf-8")
        tmp.replace(self.subs_file)

    def subscribe(self, device: str, subscription: dict) -> None:
        endpoint = str(subscription.get("endpoint", ""))
        keys = subscription.get("keys") or {}
        p256dh, auth = str(keys.get("p256dh", "")), str(keys.get("auth", ""))
        if not allowed_endpoint(endpoint) or not (20 <= len(p256dh) <= 200 and 10 <= len(auth) <= 100):
            raise ValueError("Abonnement aux notifications invalide.")
        with self._lock:
            self.subs = [s for s in self.subs if s.endpoint != endpoint]
            self.subs.append(Subscription(device, endpoint, p256dh, auth))
            self.subs = self.subs[-20:]
            self._save()

    def remove_device(self, device: str) -> None:
        with self._lock:
            self.subs = [s for s in self.subs if s.device != device]
            self._save()

    def devices(self) -> set[str]:
        return {s.device for s in self.subs}

    async def send(self, devices: set[str], title: str, body: str, tag: str = "jarvis") -> int:
        targets = [s for s in self.subs if s.device in devices]
        if not targets:
            return 0
        payload = json.dumps({"title": title, "body": body[:400], "tag": tag}, ensure_ascii=False)
        results = await asyncio.gather(*(asyncio.to_thread(self._send_one, s, payload) for s in targets))
        return sum(results)

    def _send_one(self, sub: Subscription, payload: str) -> bool:
        from pywebpush import WebPushException, webpush

        try:
            webpush(sub.info(), data=payload, vapid_private_key=self.vapid,
                    vapid_claims={"sub": self.contact}, ttl=3600, timeout=10)
            return True
        except WebPushException as e:
            status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):  # abonnement expiré : on l'oublie
                with self._lock:
                    self.subs = [s for s in self.subs if s.endpoint != sub.endpoint]
                    self._save()
            log.warning("Notification push refusée (%s) pour %s", status, sub.device)
        except Exception:  # noqa: BLE001
            log.exception("Envoi de notification push impossible")
        return False
