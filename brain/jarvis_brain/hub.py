"""Le hub : sait quels appareils sont connectés et lequel tu as utilisé en dernier.

C'est lui qui permet à Jarvis de « te suivre » : une notification est dite sur
l'appareil le plus récemment utilisé, affichée sur tous ceux qui ont un écran,
et mise en attente si aucun appareil n'est connecté.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .config import Settings

if TYPE_CHECKING:
    from .conversation import ConversationStore
    from .push import PushService
    from .session import DeviceSession

log = logging.getLogger(__name__)


def in_quiet_hours(hours: tuple[int, int] | None, now: datetime) -> bool:
    if not hours:
        return False
    start, end = hours
    h = now.hour
    return start <= h < end if start < end else (h >= start or h < end)


PUSH_TITLES = {"mail": "Nouveau courriel", "reminder": "Rappel", "bill": "Facture à payer",
               "briefing": "Briefing du jour", "digest": "Pendant votre absence"}


class DeviceHub:
    def __init__(self, settings: Settings, store: ConversationStore, push: PushService | None = None):
        self.settings = settings
        self.store = store
        self.push = push
        self._sessions: set[DeviceSession] = set()

    # ---- connexions ----

    def register(self, session: DeviceSession) -> None:
        self._sessions.add(session)

    def unregister(self, session: DeviceSession) -> None:
        self._sessions.discard(session)

    def connected(self) -> list[DeviceSession]:
        return sorted(self._sessions, key=lambda s: s.last_active, reverse=True)

    def find(self, device: str) -> DeviceSession | None:
        device = device.lower().strip()
        return next((s for s in self.connected() if s.device == device), None)

    # ---- notifications ----

    async def notify(self, text: str, card: dict | None = None, kind: str = "info") -> str:
        """Prévient l'utilisateur. Renvoie le nom de l'appareil qui a parlé, ou « attente »."""
        now = datetime.now(ZoneInfo(self.settings.timezone))
        await self.store.append_note("jarvis", f"[Notification] {text}")
        sessions = self.connected()
        await self._push(text, kind, sessions)
        if not sessions:
            self._queue(text, card, kind)
            return "attente"
        quiet = in_quiet_hours(self.settings.quiet_hours, now)
        # Parle sur l'appareil le plus récemment utilisé dont la page est affichée.
        speaker = next((s for s in sessions if getattr(s, "visible", True)), sessions[0])
        for s in sessions:
            await s.push_notification(text, card, kind, speak=(s is speaker and not quiet))
        return speaker.device

    async def _push(self, text: str, kind: str, sessions: list[DeviceSession]) -> None:
        """Notification système sur les appareils abonnés dont la page n'est pas affichée."""
        if not self.push:
            return
        watching = {s.device for s in sessions if getattr(s, "visible", True)}
        targets = self.push.devices() - watching
        if targets:
            await self.push.send(targets, PUSH_TITLES.get(kind, "Jarvis"), text, tag=kind)

    def _queue(self, text: str, card: dict | None, kind: str) -> None:
        path: Path = self.settings.inbox_file
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "text": text, "card": card, "kind": kind},
                               ensure_ascii=False) + "\n")

    async def deliver_pending(self, session: DeviceSession) -> None:
        """À la connexion d'un appareil : « Pendant votre absence… »."""
        path: Path = self.settings.inbox_file
        if not path.exists():
            return
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        path.unlink()
        if not lines:
            return
        texts = [n["text"] for n in lines[-10:]]
        intro = "Pendant votre absence : " if len(texts) > 1 else ""
        card = {"title": "Pendant votre absence", "items": [{"title": t} for t in texts]}
        quiet = in_quiet_hours(self.settings.quiet_hours, datetime.now(ZoneInfo(self.settings.timezone)))
        await session.push_notification(intro + " ".join(texts), card, "digest", speak=not quiet)
