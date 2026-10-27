"""Mot de réveil vérifié sur la transcription.

Les clients sans détecteur local (un navigateur, une télé) envoient chaque phrase
entendue avec `wake_word: true`. Le serveur la transcrit et ne répond que si elle
commence par « Jarvis ». Les clients avec openWakeWord (PC, Raspberry Pi)
détectent le mot localement et n'ont pas besoin de ce filtre.
"""

from __future__ import annotations

import re
import unicodedata

# Whisper transcrit « Jarvis » de plusieurs façons selon l'accent et le bruit.
_VARIANTS = r"(?:dj|j|g|ch)ar(?:v|w|b)(?:i|is|isse|ice|y|ys|e)"
_PREFIX = r"^(?:(?:hey|he|eh|ok|okay|dis|salut|bonjour|allo)[\s,]+)?"  # texte déjà sans accents


def _normalize(text: str) -> tuple[str, list[int]]:
    """Minuscules sans accents, avec pour chaque caractère son index dans le texte d'origine."""
    chars: list[str] = []
    index: list[int] = []
    for i, c in enumerate(text):
        for d in unicodedata.normalize("NFKD", c.lower()):
            if not unicodedata.combining(d):
                chars.append(d)
                index.append(i)
    return "".join(chars), index


def strip_wake_word(text: str, name: str = "jarvis") -> str | None:
    """Renvoie la demande sans le mot de réveil, ou None s'il est absent.

    « Jarvis, quelle heure est-il ? » -> « quelle heure est-il ? »
    « Jarvis. » -> « » (réveil seul : le client écoute la suite)
    """
    text = text.strip()
    variants = _VARIANTS if name.lower() == "jarvis" else re.escape(_normalize(name)[0])
    pattern = re.compile(_PREFIX + variants + r"\b[\s,.!?:;-]*")
    normalized, index = _normalize(text)
    match = pattern.match(normalized)
    if not match:
        return None
    if match.end() >= len(normalized):
        return ""
    return text[index[match.end()]:].strip()
