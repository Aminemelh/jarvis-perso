"""Une connexion d'appareil : reçoit la voix ou le texte, répond phrase par phrase.

Déroulé d'un tour :
  audio -> STT -> texte -> LLM (streaming) -> découpage en phrases -> TTS -> client
La synthèse vocale d'une phrase se fait pendant que le LLM écrit la suivante,
donc la voix commence dès la première phrase.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from .config import Settings
from .conversation import ConversationStore, to_messages
from .llm import LLM
from .llm.claude import LLMError
from .prompts import build_system
from .sentences import SentenceSplitter, clean_for_speech
from .stt import STT
from .tts import TTS
from .vision import PhotoError, decode_photo, image_block
from .wakeword import strip_wake_word

if TYPE_CHECKING:
    from .tools import ToolBox

log = logging.getLogger(__name__)

SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]
ToolBoxFactory = Callable[["DeviceSession"], "ToolBox | None"]

SUPPORTED_ENCODINGS = {"pcm_s16le"}


@dataclass
class Engines:
    llm: LLM
    stt: STT
    tts: TTS


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class DeviceSession:
    _turn_ids = itertools.count(1)

    def __init__(self, device: str, settings: Settings, engines: Engines,
                 store: ConversationStore, send_json: SendJson, send_bytes: SendBytes,
                 toolbox_factory: ToolBoxFactory | None = None, push=None):
        self.device = device
        self.settings = settings
        self.engines = engines
        self.store = store
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._send_lock = asyncio.Lock()
        self._turn: asyncio.Task | None = None
        self._audio: bytearray | None = None
        self._audio_rate = 16_000
        self._audio_wake = False
        self._wake_checks: set[asyncio.Task] = set()
        self._background: set[asyncio.Task] = set()
        self.toolbox_factory = toolbox_factory
        self.push = push
        self.visible = True  # la page est-elle affichée ? (sinon : notification push)
        self.current_turn = 0  # numéro du dernier tour de parole de l'utilisateur
        self.has_camera = False
        self.pending_photo: tuple[bytes, str] | None = None  # photo jointe à la prochaine question
        self._camera_requests: dict[str, asyncio.Future] = {}
        self.pending_confirmations: dict[str, tuple] = {}
        self.want_audio = True
        self.has_display = False
        self.screen: dict | None = None  # ce qui est affiché sur cet appareil (pour « zoom sur la première »)
        self.last_active = time.monotonic()

    # ---- envoi (un verrou garantit que le JSON et son audio restent collés) ----

    async def send(self, msg: dict, audio: bytes | None = None) -> None:
        async with self._send_lock:
            await self._send_json(msg)
            if audio is not None:
                await self._send_bytes(audio)

    # ---- messages entrants ----

    async def on_json(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind in {"text", "audio_start"}:
            self.last_active = time.monotonic()  # c'est ici que l'utilisateur se trouve
        if kind == "text":
            text = str(msg.get("text", "")).strip()
            if not text:
                return
            if len(text) > self.settings.max_text_chars:
                raise ProtocolError("too_long", "Message trop long.")
            self.start_turn(text=text)
        elif kind == "audio_start":
            if msg.get("encoding", "pcm_s16le") not in SUPPORTED_ENCODINGS:
                raise ProtocolError("bad_audio", "Encodage audio non pris en charge (pcm_s16le attendu).")
            rate = int(msg.get("sample_rate", 16_000))
            if not 8_000 <= rate <= 48_000:
                raise ProtocolError("bad_audio", "Fréquence d'échantillonnage invalide.")
            self._audio_wake = bool(msg.get("wake_word", False))
            if not self._audio_wake:
                await self.cancel()  # l'utilisateur reparle : on coupe Jarvis (barge-in)
            self._audio = bytearray()
            self._audio_rate = rate
        elif kind == "audio_end":
            if self._audio is None:
                raise ProtocolError("bad_audio", "audio_end sans audio_start.")
            pcm, self._audio = bytes(self._audio), None
            if len(pcm) < self._audio_rate * 2 * 0.3:  # moins de 0,3 s : ignoré
                await self.send({"type": "reply_end", "turn_id": None, "text": "", "cancelled": True})
                return
            if self._audio_wake:
                # On ne coupe pas Jarvis tant qu'on ne sait pas si c'est à lui qu'on parle.
                task = asyncio.create_task(self._check_wake(pcm, self._audio_rate))
                self._wake_checks.add(task)
                task.add_done_callback(self._wake_checks.discard)
            else:
                self.start_turn(pcm=pcm, sample_rate=self._audio_rate)
        elif kind == "cancel":
            await self.cancel()
        elif kind == "config":
            self.want_audio = bool(msg.get("audio_replies", True))
        elif kind == "visibility":
            self.visible = bool(msg.get("visible", True))
            if self.visible:
                self.last_active = time.monotonic()
        elif kind == "photo":
            # Photo prise avec le bouton 📷 : elle accompagne la prochaine question.
            try:
                self.pending_photo = decode_photo(msg.get("data", ""))
            except PhotoError as e:
                raise ProtocolError("bad_photo", str(e)) from e
            await self.send({"type": "photo_ok"})
            if msg.get("question"):
                self.last_active = time.monotonic()
                self.start_turn(text=str(msg["question"])[: self.settings.max_text_chars])
        elif kind == "camera_result":
            future = self._camera_requests.pop(str(msg.get("request_id", "")), None)
            if future and not future.done():
                if msg.get("error"):
                    future.set_exception(PhotoError(str(msg["error"])[:200]))
                else:
                    try:
                        future.set_result(decode_photo(msg.get("data", "")))
                    except PhotoError as e:
                        future.set_exception(e)
        elif kind == "push_subscribe":
            if not self.push:
                raise ProtocolError("push_unavailable", "Notifications push indisponibles sur ce serveur.")
            sub = msg.get("subscription")
            try:
                self.push.subscribe(self.device, sub if isinstance(sub, dict) else {})
            except ValueError as e:
                raise ProtocolError("bad_subscription", str(e)) from e
            await self.send({"type": "push_ok"})
        elif kind == "ping":
            await self.send({"type": "pong"})
        else:
            raise ProtocolError("unknown_type", f"Type de message inconnu : {kind!r}")

    async def on_bytes(self, data: bytes) -> None:
        if self._audio is None:
            raise ProtocolError("bad_audio", "Audio reçu sans audio_start.")
        limit = self.settings.max_audio_seconds * self._audio_rate * 2
        if len(self._audio) + len(data) > limit:
            self._audio = None
            raise ProtocolError("too_long", f"Audio trop long (max {self.settings.max_audio_seconds} s).")
        self._audio.extend(data)

    # ---- gestion des tours ----

    def start_turn(self, text: str | None = None, pcm: bytes | None = None, sample_rate: int = 16_000) -> None:
        if self._turn and not self._turn.done():
            self._turn.cancel()
        turn_id = next(self._turn_ids)
        self.current_turn = turn_id
        self._turn = asyncio.create_task(self._run_turn(turn_id, text, pcm, sample_rate))

    async def cancel(self) -> None:
        task = self._turn
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def close(self) -> None:
        for task in list(self._wake_checks) + list(self._background):
            task.cancel()
        for future in self._camera_requests.values():
            if not future.done():
                future.set_exception(PhotoError("Appareil déconnecté."))
        await self.cancel()

    async def request_photo(self, timeout: float = 30.0) -> tuple[bytes, str]:
        """Demande à cet appareil de prendre une photo (« Jarvis, regarde ça »)."""
        request_id = secrets.token_hex(4)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._camera_requests[request_id] = future
        await self.send({"type": "camera", "action": "capture", "request_id": request_id})
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as e:
            raise PhotoError("La caméra n'a pas répondu.") from e
        finally:
            self._camera_requests.pop(request_id, None)

    # ---- notifications spontanées (courriel reçu, rappel de rendez-vous…) ----

    async def push_notification(self, text: str, card: dict | None, kind: str, speak: bool) -> None:
        await self.send({"type": "notify", "kind": kind, "text": text, "card": card if self.has_display else None})
        if speak:
            task = asyncio.create_task(self._speak_notification(text))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _speak_notification(self, text: str) -> None:
        # On ne coupe pas une réponse en cours : on attend qu'elle se termine.
        if self._turn and not self._turn.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._turn), timeout=120)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        audio = None
        if self.want_audio:
            try:
                audio = await asyncio.to_thread(self.engines.tts.synthesize, clean_for_speech(text))
            except Exception:  # noqa: BLE001
                log.exception("Synthèse de la notification impossible")
        await self.send({"type": "sentence", "turn_id": None, "index": 0, "text": text,
                         "audio": audio is not None, "format": "wav"}, audio)

    async def _check_wake(self, pcm: bytes, rate: int) -> None:
        try:
            heard = await asyncio.to_thread(self.engines.stt.transcribe, pcm, rate)
        except Exception:  # noqa: BLE001
            log.exception("Transcription (mot de réveil) impossible")
            return
        request = strip_wake_word(heard, self.settings.assistant_name) if heard else None
        if request is None:
            await self.send({"type": "ignored", "text": heard})
            return
        if request:
            self.start_turn(text=request)
            return
        # « Jarvis » tout seul : on répond « Oui ? » et le client écoute la suite sans mot de réveil.
        await self.cancel()
        ack = f"Oui, {self.settings.user_title} ?"
        audio = await asyncio.to_thread(self.engines.tts.synthesize, ack) if self.want_audio else None
        await self.send({"type": "wake", "text": ack})
        await self.send({"type": "sentence", "turn_id": None, "index": 0, "text": ack,
                         "audio": audio is not None, "format": "wav"}, audio)

    async def _run_turn(self, turn_id: int, text: str | None, pcm: bytes | None, rate: int) -> None:
        reply_parts: list[str] = []
        user_text = text or ""
        cancelled = False
        try:
            if pcm is not None:
                await self.send({"type": "state", "state": "transcribing", "turn_id": turn_id})
                user_text = await asyncio.to_thread(self.engines.stt.transcribe, pcm, rate)
                if not user_text:
                    await self.send({"type": "transcript", "turn_id": turn_id, "text": ""})
                    await self.send({"type": "reply_end", "turn_id": turn_id, "text": "", "cancelled": True})
                    return
            await self.send({"type": "transcript", "turn_id": turn_id, "text": user_text})
            await self.send({"type": "state", "state": "thinking", "turn_id": turn_id})
            photo, self.pending_photo = self.pending_photo, None
            await self._respond(turn_id, user_text, reply_parts, photo)
            if photo:
                user_text += " [avec une photo]"
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as e:  # noqa: BLE001 - on veut toujours répondre quelque chose
            message = str(e) if isinstance(e, LLMError) else "Désolé, une erreur interne est survenue."
            if not isinstance(e, LLMError):
                log.exception("Erreur pendant le tour %s", turn_id)
            await self._speak_error(turn_id, message)
            reply_parts.append(message)
        finally:
            full = "".join(reply_parts).strip()
            if user_text and full:
                saved = full + (" [interrompu]" if cancelled else "")
                await self.store.append_exchange(self.device, user_text, saved)
            try:
                await asyncio.shield(self.send(
                    {"type": "reply_end", "turn_id": turn_id, "text": full, "cancelled": cancelled}
                ))
            except Exception:  # noqa: BLE001 - connexion déjà fermée
                pass

    async def _respond(self, turn_id: int, user_text: str, reply_parts: list[str],
                       photo: tuple[bytes, str] | None = None) -> None:
        system = build_system(self.settings, self.device, self.screen)
        messages = to_messages(self.store.snapshot(), user_text)
        if photo:
            messages[-1]["content"] = [image_block(*photo), {"type": "text", "text": user_text}]
        toolbox = self.toolbox_factory(self) if self.toolbox_factory else None
        splitter = SentenceSplitter()
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak_worker(turn_id, sentences))
        try:
            async for delta in self.engines.llm.stream(system, messages, toolbox):
                reply_parts.append(delta)
                await self.send({"type": "reply_delta", "turn_id": turn_id, "text": delta})
                for sentence in splitter.feed(delta):
                    sentences.put_nowait(sentence)
            for sentence in splitter.flush():
                sentences.put_nowait(sentence)
            sentences.put_nowait(None)
            await speaker
        finally:
            if not speaker.done():
                speaker.cancel()

    async def _speak_worker(self, turn_id: int, queue: asyncio.Queue[str | None]) -> None:
        index = 0
        while (sentence := await queue.get()) is not None:
            spoken = clean_for_speech(sentence)
            if not spoken:
                continue
            audio = None
            if self.want_audio:
                audio = await asyncio.to_thread(self.engines.tts.synthesize, spoken)
            await self.send(
                {"type": "sentence", "turn_id": turn_id, "index": index, "text": spoken,
                 "audio": audio is not None, "format": "wav"},
                audio,
            )
            index += 1

    async def _speak_error(self, turn_id: int, message: str) -> None:
        audio = None
        if self.want_audio:
            try:
                audio = await asyncio.to_thread(self.engines.tts.synthesize, message)
            except Exception:  # noqa: BLE001
                audio = None
        await self.send({"type": "error", "turn_id": turn_id, "code": "turn_failed", "message": message})
        await self.send(
            {"type": "sentence", "turn_id": turn_id, "index": 0, "text": message,
             "audio": audio is not None, "format": "wav"},
            audio,
        )
