"""Historique de conversation partagé entre tous les appareils.

Un seul fil de discussion : ce que tu dis dans la voiture, Jarvis s'en souvient
devant la télé. L'historique est conservé en JSONL (une ligne par message).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str
    device: str
    ts: float


class ConversationStore:
    def __init__(self, path: Path, max_turns: int = 20):
        self.path = path
        self.max_turns = max_turns
        self._turns: list[Turn] = []
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for line in lines[-self.max_turns * 2 :]:
            try:
                self._turns.append(Turn(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue

    def snapshot(self) -> list[Turn]:
        return list(self._turns[-self.max_turns * 2 :])

    async def append_exchange(self, device: str, user_text: str, assistant_text: str) -> None:
        """Ajoute la question et la réponse ensemble, pour garder l'alternance user/assistant."""
        now = time.time()
        turns = [Turn("user", user_text, device, now), Turn("assistant", assistant_text, device, now)]
        async with self._lock:
            self._turns.extend(turns)
            self._turns = self._turns[-self.max_turns * 2 :]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                for t in turns:
                    f.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")

    async def append_note(self, device: str, text: str) -> None:
        """Ce que Jarvis a dit de lui-même (notification), pour pouvoir en reparler ensuite."""
        turn = Turn("assistant", text, device, time.time())
        async with self._lock:
            self._turns.append(turn)
            self._turns = self._turns[-self.max_turns * 2 :]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")

    async def clear(self) -> None:
        async with self._lock:
            self._turns.clear()
            if self.path.exists():
                archived = self.path.with_name(f"conversation-{int(time.time())}.jsonl")
                self.path.replace(archived)


def to_messages(turns: list[Turn], user_text: str) -> list[dict]:
    """Convertit l'historique au format de l'API Messages, avec la nouvelle question à la fin.

    Seul le texte des tours précédents est rejoué (pas leurs blocs de réflexion ni
    leurs appels d'outils) : chaque requête repart d'un historique propre, ce qui
    reste valide même si le prompt système change d'un tour à l'autre.
    """
    messages = [{"role": t.role, "content": t.text} for t in turns if t.text]
    # L'API exige que le premier message soit "user".
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    messages.append({"role": "user", "content": user_text})
    return messages
