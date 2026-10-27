"""Transcription locale et gratuite avec faster-whisper."""

from __future__ import annotations

import logging
import threading

import numpy as np

from ..audio import resample_int16
from ..config import Settings

log = logging.getLogger(__name__)

WHISPER_RATE = 16_000


class WhisperSTT:
    def __init__(self, settings: Settings):
        from faster_whisper import WhisperModel  # import lourd, chargé à la demande

        log.info("Chargement de Whisper '%s' (%s, %s)…", settings.whisper_model,
                 settings.whisper_device, settings.whisper_compute_type)
        self.model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
        self.language = settings.language
        self._lock = threading.Lock()  # un modèle = une transcription à la fois

    def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        samples = np.frombuffer(pcm16, dtype=np.int16)
        if sample_rate != WHISPER_RATE:
            samples = resample_int16(samples, sample_rate, WHISPER_RATE)
        audio = samples.astype(np.float32) / 32768.0
        with self._lock:
            segments, _info = self.model.transcribe(
                audio,
                language=self.language,
                beam_size=1,
                vad_filter=True,  # ignore les silences et le bruit de fond
                condition_on_previous_text=False,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
