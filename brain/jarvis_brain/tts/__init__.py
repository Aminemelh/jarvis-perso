"""Text-to-speech. Tous exposent `synthesize(text) -> bytes` (fichier WAV complet, bloquant)."""

from __future__ import annotations

from typing import Protocol

from ..config import Settings


class TTS(Protocol):
    def synthesize(self, text: str) -> bytes | None: ...


class NoTTS:
    """Aucune voix côté serveur : le client affiche le texte ou utilise sa propre voix."""

    def synthesize(self, text: str) -> bytes | None:
        return None


def create_tts(settings: Settings) -> TTS:
    if settings.tts_backend == "piper":
        from .piper import PiperTTS

        return PiperTTS(settings)
    if settings.tts_backend == "none":
        return NoTTS()
    raise ValueError(f"JARVIS_TTS inconnu : {settings.tts_backend!r} (piper | none)")
