"""Speech-to-text. Tous exposent `transcribe(pcm16: bytes, sample_rate: int) -> str` (bloquant)."""

from __future__ import annotations

from typing import Protocol

from ..config import Settings


class STT(Protocol):
    def transcribe(self, pcm16: bytes, sample_rate: int) -> str: ...


class NoSTT:
    """Aucun STT : le serveur n'accepte que du texte."""

    def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        raise RuntimeError("La reconnaissance vocale est désactivée sur le serveur (JARVIS_STT=none).")


def create_stt(settings: Settings) -> STT:
    if settings.stt_backend == "whisper":
        from .whisper import WhisperSTT

        return WhisperSTT(settings)
    if settings.stt_backend == "none":
        return NoSTT()
    raise ValueError(f"JARVIS_STT inconnu : {settings.stt_backend!r} (whisper | none)")
