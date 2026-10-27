"""Micro, haut-parleur et détection de parole pour le client vocal."""

from __future__ import annotations

import asyncio
import io
import queue
import threading
import wave

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
CHUNK = 1280  # 80 ms : la taille attendue par openWakeWord


class Vad:
    """Détecte le début et la fin de la parole à partir du volume (RMS)."""

    def __init__(self, end_silence_chunks: int = 10, max_chunks: int = 190):
        self.floor = 300.0
        self.speaking = False
        self.heard = False
        self.loud = 0
        self.quiet = 0
        self.length = 0
        self.end_silence_chunks = end_silence_chunks  # 10 x 80 ms = 0,8 s
        self.max_chunks = max_chunks  # ~15 s

    def push(self, pcm: np.ndarray) -> str | None:
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if pcm.size else 0.0
        start_level = max(self.floor * 3, 600.0)
        stop_level = max(self.floor * 2, 400.0)
        if not self.speaking:
            self.floor = self.floor * 0.95 + rms * 0.05
            self.loud = self.loud + 1 if rms > start_level else 0
            if self.loud >= 2:
                self.force_start()
                self.heard = True
                return "start"
            return None
        self.length += 1
        if rms > start_level:
            self.heard = True
        self.quiet = self.quiet + 1 if rms < stop_level else 0
        max_quiet = self.end_silence_chunks if self.heard else 50  # 4 s pour commencer à parler
        if self.quiet >= max_quiet or self.length >= self.max_chunks:
            self.speaking = False
            self.loud = 0
            return "end"
        return None

    def force_start(self) -> None:
        self.speaking = True
        self.heard = False
        self.quiet = 0
        self.length = 0


class Microphone:
    """Envoie des blocs PCM int16 de 80 ms dans une file asyncio."""

    def __init__(self, loop: asyncio.AbstractEventLoop, device: str | int | None = None):
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=200)
        self._loop = loop

        def callback(indata, frames, time_info, status):  # thread audio
            block = indata[:, 0].copy()
            loop.call_soon_threadsafe(self._put, block)

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=CHUNK, device=device, callback=callback,
        )

    def _put(self, block: np.ndarray) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # on jette le plus ancien plutôt que de prendre du retard
        self.queue.put_nowait(block)

    def start(self) -> None:
        self.stream.start()

    def stop(self) -> None:
        self.stream.stop()
        self.stream.close()


class Speaker:
    """Joue les WAV reçus les uns après les autres, dans un thread dédié."""

    def __init__(self, device: str | int | None = None):
        self.device = device
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._stop = threading.Event()
        self._busy = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def playing(self) -> bool:
        return self._busy.is_set() or not self._queue.empty()

    def play(self, wav_bytes: bytes) -> None:
        self._queue.put(wav_bytes)

    def stop(self) -> None:
        """Coupe la phrase en cours et vide la file (l'utilisateur interrompt Jarvis)."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._stop.set()

    def _run(self) -> None:
        while True:
            data = self._queue.get()
            if data is None:
                return
            self._stop.clear()
            self._busy.set()
            try:
                with wave.open(io.BytesIO(data)) as wav:
                    rate = wav.getframerate()
                    channels = wav.getnchannels()
                    frames = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
                frames = frames.reshape(-1, channels)
                with sd.OutputStream(samplerate=rate, channels=channels, dtype="int16", device=self.device) as out:
                    step = rate // 10
                    for i in range(0, len(frames), step):
                        if self._stop.is_set():
                            break
                        out.write(frames[i:i + step])
            except Exception as e:  # noqa: BLE001 - un WAV corrompu ne doit pas tuer le client
                print(f"[audio] lecture impossible : {e}")
            finally:
                self._busy.clear()
