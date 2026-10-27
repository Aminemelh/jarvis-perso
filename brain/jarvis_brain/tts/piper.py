"""Voix locale et gratuite avec Piper."""

from __future__ import annotations

import io
import logging
import threading
import wave

from ..config import Settings

log = logging.getLogger(__name__)


class PiperTTS:
    def __init__(self, settings: Settings):
        from piper import PiperVoice, SynthesisConfig

        path = settings.piper_voice
        if not path.exists():
            raise FileNotFoundError(
                f"Voix Piper introuvable : {path}. Télécharge-la avec :\n"
                f"  python -m piper.download_voices {path.stem} --download-dir {path.parent}"
            )
        log.info("Chargement de la voix Piper %s…", path.name)
        self.voice = PiperVoice.load(path)
        self.config = SynthesisConfig(length_scale=settings.piper_length_scale)
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> bytes | None:
        buf = io.BytesIO()
        with self._lock, wave.open(buf, "wb") as wav:
            self.voice.synthesize_wav(text, wav, syn_config=self.config)
        return buf.getvalue()
