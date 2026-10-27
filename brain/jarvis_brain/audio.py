"""Petits utilitaires audio."""

from __future__ import annotations

import io
import wave

import numpy as np


def resample_int16(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Rééchantillonnage linéaire, suffisant pour de la voix destinée au STT."""
    if src_rate == dst_rate or samples.size == 0:
        return samples
    duration = samples.size / src_rate
    n_out = int(round(duration * dst_rate))
    x_old = np.linspace(0.0, duration, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, duration, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, samples.astype(np.float32)).astype(np.int16)


def silent_wav(seconds: float = 0.2, sample_rate: int = 22_050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * int(seconds * sample_rate))
    return buf.getvalue()
