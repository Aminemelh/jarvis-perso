"""Découpe le texte streamé par le LLM en phrases, pour lancer la voix au plus vite."""

from __future__ import annotations

import re

# Fin de phrase : . ! ? … suivis d'un espace. On évite de couper sur
# les abréviations courantes et les nombres décimaux (3.5).
_END = re.compile(r"([.!?…]+)(\s+)")
_ABBREVIATIONS = {"m.", "mme.", "mlle.", "dr.", "st.", "ste.", "etc.", "ex.", "p.", "vs.", "mr.", "no."}


class SentenceSplitter:
    def __init__(self, min_chars: int = 12, max_chars: int = 300):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        out: list[str] = []
        start = 0
        for match in _END.finditer(self._buffer):
            end = match.end(1)
            candidate = self._buffer[start:end].strip()
            last_word = candidate.rsplit(" ", 1)[-1].lower()
            if last_word in _ABBREVIATIONS or len(candidate) < self.min_chars:
                continue
            out.append(candidate)
            start = match.end()
        self._buffer = self._buffer[start:]
        # Phrase interminable sans ponctuation : on coupe à une virgule ou un espace.
        while len(self._buffer) > self.max_chars:
            cut = max(self._buffer.rfind(", ", 0, self.max_chars), self._buffer.rfind(" ", 0, self.max_chars))
            if cut <= 0:
                cut = self.max_chars
            out.append(self._buffer[: cut + 1].strip())
            self._buffer = self._buffer[cut + 1 :]
        return [s for s in out if s]

    def flush(self) -> list[str]:
        rest = self._buffer.strip()
        self._buffer = ""
        return [rest] if rest else []


_MARKDOWN = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"\*([^*]+)\*"), r"\1"),
    (re.compile(r"^#+\s*", re.M), ""),
    (re.compile(r"^\s*[-*•]\s+", re.M), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
]


def clean_for_speech(text: str) -> str:
    """Retire le Markdown que le modèle pourrait produire malgré la consigne."""
    for pattern, repl in _MARKDOWN:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()
