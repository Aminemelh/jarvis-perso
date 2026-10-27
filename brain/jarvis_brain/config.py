"""Configuration du cerveau, lue depuis les variables d'environnement (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "oui"}


def _path(value: str | None, default: str) -> Path:
    return Path(value or default).expanduser()


@dataclass(frozen=True)
class Settings:
    # Réseau
    host: str = "127.0.0.1"
    port: int = 8765
    tls_cert: Path | None = None
    tls_key: Path | None = None

    # Données (tokens des appareils, historique, mémoire)
    data_dir: Path = Path("data")

    # Personnalité
    assistant_name: str = "Jarvis"
    user_title: str = "Monsieur"
    language: str = "fr"
    timezone: str = "America/Toronto"

    # LLM
    llm_backend: str = "claude"  # claude | echo
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"
    claude_max_tokens: int = 2048
    claude_fallbacks: bool = True
    history_turns: int = 20

    # Speech-to-text
    stt_backend: str = "whisper"  # whisper | none
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # Text-to-speech
    tts_backend: str = "piper"  # piper | none
    piper_voice: Path = Path("models/piper/fr_FR-tom-medium.onnx")
    piper_length_scale: float = 1.0

    # Limites (anti-abus)
    max_audio_seconds: int = 30
    max_text_chars: int = 2000
    auth_timeout_seconds: float = 5.0

    @property
    def devices_file(self) -> Path:
        return self.data_dir / "devices.json"

    @property
    def history_file(self) -> Path:
        return self.data_dir / "conversation.jsonl"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"


def load_settings(env_file: str | os.PathLike[str] | None = ".env") -> Settings:
    if env_file:
        load_dotenv(env_file, override=False)
    env = os.environ.get
    cert = env("JARVIS_TLS_CERT")
    key = env("JARVIS_TLS_KEY")
    return Settings(
        host=env("JARVIS_HOST", "127.0.0.1"),
        port=int(env("JARVIS_PORT", "8765")),
        tls_cert=Path(cert) if cert else None,
        tls_key=Path(key) if key else None,
        data_dir=_path(env("JARVIS_DATA_DIR"), "data"),
        assistant_name=env("JARVIS_NAME", "Jarvis"),
        user_title=env("JARVIS_USER_TITLE", "Monsieur"),
        language=env("JARVIS_LANGUAGE", "fr"),
        timezone=env("JARVIS_TIMEZONE", "America/Toronto"),
        llm_backend=env("JARVIS_LLM", "claude"),
        claude_model=env("JARVIS_CLAUDE_MODEL", "claude-opus-5-5"),
        claude_effort=env("JARVIS_CLAUDE_EFFORT", "low"),
        claude_max_tokens=int(env("JARVIS_CLAUDE_MAX_TOKENS", "2048")),
        claude_fallbacks=_bool(env("JARVIS_CLAUDE_FALLBACKS"), True),
        history_turns=int(env("JARVIS_HISTORY_TURNS", "20")),
        stt_backend=env("JARVIS_STT", "whisper"),
        whisper_model=env("JARVIS_WHISPER_MODEL", "small"),
        whisper_device=env("JARVIS_WHISPER_DEVICE", "cpu"),
        whisper_compute_type=env("JARVIS_WHISPER_COMPUTE", "int8"),
        tts_backend=env("JARVIS_TTS", "piper"),
        piper_voice=_path(env("JARVIS_PIPER_VOICE"), "models/piper/fr_FR-tom-medium.onnx"),
        piper_length_scale=float(env("JARVIS_PIPER_LENGTH_SCALE", "1.0")),
        max_audio_seconds=int(env("JARVIS_MAX_AUDIO_SECONDS", "30")),
        max_text_chars=int(env("JARVIS_MAX_TEXT_CHARS", "2000")),
    )
