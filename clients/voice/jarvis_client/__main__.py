"""Client vocal Jarvis pour Windows, Linux et Raspberry Pi (voiture, salon…).

  python -m jarvis_client                 # utilise le fichier .env
  python -m jarvis_client --list-devices  # liste micros et haut-parleurs

Modes de réveil (JARVIS_WAKE) :
  local   openWakeWord détecte « Hey Jarvis » sur l'appareil (recommandé : rien ne part
          sur le réseau tant que le mot n'est pas dit)
  server  chaque phrase entendue est envoyée, le serveur ne répond qu'à « Jarvis… »
  key     appuie sur Entrée pour parler (test, PC)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import deque
from urllib.parse import urlparse

import numpy as np
import websockets
from dotenv import load_dotenv

from .audio import SAMPLE_RATE, Microphone, Speaker, Vad

FOLLOW_UP_SECONDS = 6.0  # après une réponse, on peut enchaîner sans redire « Jarvis »


def env_device(name: str) -> str | int | None:
    value = os.environ.get(name)
    if not value:
        return None
    return int(value) if value.isdigit() else value


class LocalWakeWord:
    def __init__(self, model_name: str, threshold: float):
        import openwakeword.utils
        from openwakeword.model import Model

        try:
            self.model = Model(wakeword_models=[model_name], inference_framework="onnx")
        except Exception:  # modèles absents : on les télécharge une fois
            openwakeword.utils.download_models([model_name])
            self.model = Model(wakeword_models=[model_name], inference_framework="onnx")
        self.threshold = threshold

    def detected(self, pcm: np.ndarray) -> bool:
        scores = self.model.predict(pcm)
        return max(scores.values(), default=0.0) >= self.threshold

    def reset(self) -> None:
        self.model.reset()


class VoiceClient:
    def __init__(self, url: str, token: str, wake_mode: str, wake: LocalWakeWord | None,
                 mic: Microphone, speaker: Speaker):
        self.url = url
        self.token = token
        self.wake_mode = wake_mode
        self.wake = wake
        self.mic = mic
        self.speaker = speaker
        self.vad = Vad()
        self.ws = None
        self.recording = False
        self.preroll: deque[np.ndarray] = deque(maxlen=4)  # ~320 ms avant la parole
        self.follow_up_until = 0.0
        self.pending_audio = False
        self.drop_up_to = 0
        self.current_turn = 0
        self.key_pressed = asyncio.Event()

    async def run(self) -> None:
        delay = 1
        while True:
            try:
                async with websockets.connect(self.url, max_size=4 << 20, ping_interval=20) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({"type": "auth", "token": self.token, "client": "voice/0.1"}))
                    hello = json.loads(await ws.recv())
                    if hello.get("type") != "auth_ok":
                        raise SystemExit("Authentification refusée.")
                    print(f"Connecté au cerveau en tant que « {hello['device']} ».")
                    delay = 1
                    await asyncio.gather(self._receive(ws), self._listen(ws))
            except websockets.ConnectionClosed as e:
                if e.rcvd and e.rcvd.code == 4401:
                    raise SystemExit("Token refusé par le serveur (JARVIS_TOKEN).")
                print(f"Connexion perdue ({e}). Nouvelle tentative dans {delay} s…")
            except OSError as e:
                print(f"Serveur injoignable ({e}). Nouvelle tentative dans {delay} s…")
            self.recording = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    # ---- réception ----

    async def _receive(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                if self.pending_audio:
                    self.pending_audio = False
                    self.speaker.play(raw)
                continue
            msg = json.loads(raw)
            kind = msg.get("type")
            turn = msg.get("turn_id")
            if kind == "transcript":
                self.current_turn = turn or self.current_turn
                print(f"\nVous   : {msg['text']}")
            elif kind == "sentence":
                stale = turn is not None and turn <= self.drop_up_to
                self.pending_audio = bool(msg.get("audio")) and not stale
                if not stale:
                    print(f"Jarvis : {msg['text']}")
            elif kind == "wake":
                self.follow_up_until = time.monotonic() + FOLLOW_UP_SECONDS + 2
            elif kind == "reply_end":
                self.follow_up_until = time.monotonic() + FOLLOW_UP_SECONDS
            elif kind == "error" and not turn:
                print(f"[serveur] {msg.get('message')}")

    # ---- écoute ----

    async def _listen(self, ws) -> None:
        while True:
            pcm = await self.mic.queue.get()
            if self.recording:
                await ws.send(pcm.tobytes())
                if self.vad.push(pcm) == "end":
                    await self._end_recording(ws)
                continue

            self.preroll.append(pcm)
            follow_up = time.monotonic() < self.follow_up_until and not self.speaker.playing

            if self.wake_mode == "local" and self.wake and self.wake.detected(pcm):
                self.wake.reset()
                self.preroll.clear()  # on n'envoie pas « Hey Jarvis » lui-même
                await self._interrupt(ws)
                self.vad.force_start()
                await self._start_recording(ws, wake_word=False)
                print("\n(écoute…)")
            elif self.wake_mode == "key" and self.key_pressed.is_set():
                self.key_pressed.clear()
                await self._interrupt(ws)
                self.vad.force_start()
                await self._start_recording(ws, wake_word=False)
                print("(écoute…)")
            else:
                event = self.vad.push(pcm)
                if event == "start" and (follow_up or self.wake_mode == "server"):
                    await self._start_recording(ws, wake_word=not follow_up)

    async def _start_recording(self, ws, wake_word: bool) -> None:
        self.recording = True
        await ws.send(json.dumps({"type": "audio_start", "sample_rate": SAMPLE_RATE,
                                  "encoding": "pcm_s16le", "wake_word": wake_word}))
        for block in self.preroll:
            await ws.send(block.tobytes())
        self.preroll.clear()

    async def _end_recording(self, ws) -> None:
        self.recording = False
        self.follow_up_until = 0
        await ws.send(json.dumps({"type": "audio_end"}))

    async def _interrupt(self, ws) -> None:
        self.drop_up_to = max(self.drop_up_to, self.current_turn)
        self.speaker.stop()
        await ws.send(json.dumps({"type": "cancel"}))


async def read_keys(event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    while True:
        await loop.run_in_executor(None, sys.stdin.readline)
        event.set()


async def amain(args) -> None:
    url = os.environ.get("JARVIS_SERVER", "ws://127.0.0.1:8765/ws")
    token = os.environ.get("JARVIS_TOKEN", "")
    if not token:
        raise SystemExit("JARVIS_TOKEN manquant. Crée un token sur le serveur : python -m jarvis_brain token add <appareil>")
    host = urlparse(url).hostname or ""
    # Via Tailscale (100.x.y.z ou *.ts.net), le tunnel WireGuard chiffre déjà le trafic.
    tailscale = host.startswith("100.") or host.endswith(".ts.net")
    if url.startswith("ws://") and host not in {"127.0.0.1", "localhost"} and not tailscale:
        print("Attention : connexion non chiffrée (ws://). Utilise wss:// ou Tailscale hors de ton réseau local.")

    wake_mode = os.environ.get("JARVIS_WAKE", "local")
    wake = None
    if wake_mode == "local":
        wake = LocalWakeWord(os.environ.get("JARVIS_WAKE_MODEL", "hey_jarvis"),
                             float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5")))
        print("Dites « Hey Jarvis » pour parler.")
    elif wake_mode == "server":
        print("Dites « Jarvis, … » pour parler.")
    else:
        print("Appuyez sur Entrée pour parler.")

    loop = asyncio.get_running_loop()
    mic = Microphone(loop, env_device("JARVIS_INPUT_DEVICE"))
    speaker = Speaker(env_device("JARVIS_OUTPUT_DEVICE"))
    client = VoiceClient(url, token, wake_mode, wake, mic, speaker)
    mic.start()
    tasks = [client.run()]
    if wake_mode == "key":
        tasks.append(read_keys(client.key_pressed))
    try:
        await asyncio.gather(*tasks)
    finally:
        mic.stop()


def main() -> int:
    parser = argparse.ArgumentParser(prog="jarvis_client")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()
    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return 0
    load_dotenv(args.env)
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
