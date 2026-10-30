"""Journal d'audit : connexions, échecs, et chaque action de Jarvis (data/audit.jsonl).

Les contenus sensibles (texte des courriels, photos, mots de passe) ne sont
jamais écrits : seulement le nom de l'outil, l'appareil et un résumé court.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
REDACTED = {"body", "data", "password", "token", "fact", "text", "items", "confirmation_id", "subscription"}


def summarize(args: dict) -> dict:
    out = {}
    for key, value in (args or {}).items():
        if key in REDACTED:
            out[key] = "…"
        elif isinstance(value, str):
            out[key] = value[:80]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = "…"
    return out


class AuditLog:
    def __init__(self, path: Path, keep_in_memory: int = 500):
        self.path = path
        self._lock = threading.Lock()
        self.recent: deque[dict] = deque(maxlen=keep_in_memory)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-keep_in_memory:]:
                try:
                    self.recent.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    def record(self, event: str, **fields) -> None:
        entry = {"ts": time.time(), "event": event, **fields}
        with self._lock:
            self.recent.append(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                self.path.replace(self.path.with_name("audit-1.jsonl"))  # rotation simple
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def query(self, since: float = 0.0, events: set[str] | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            items = [e for e in self.recent if e["ts"] >= since and (not events or e["event"] in events)]
        return items[-limit:]
