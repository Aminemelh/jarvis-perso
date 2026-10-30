"""Configuration du cerveau, lue depuis les variables d'environnement (.env)."""

from __future__ import annotations

import os
import re
import sys
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
class MailAccount:
    name: str
    host: str
    user: str
    password: str
    port: int = 993
    folder: str = "INBOX"


@dataclass(frozen=True)
class CalendarSource:
    name: str
    url: str


# Flux d'actualité par défaut (modifiables avec JARVIS_NEWS_FEEDS).
DEFAULT_NEWS_FEEDS = (
    ("Radio-Canada", "https://ici.radio-canada.ca/rss/4159"),
    ("Le Devoir", "https://www.ledevoir.com/rss/manchettes.xml"),
    ("La Presse", "https://www.lapresse.ca/actualites/rss"),
)


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

    # Localisation (météo, recherche web)
    city: str = "Montréal"
    region: str = "Québec"
    country: str = "CA"

    # Outils
    tools_enabled: bool = True
    web_search: bool = True
    mail_accounts: tuple[MailAccount, ...] = ()
    calendars: tuple[CalendarSource, ...] = ()
    news_feeds: tuple[tuple[str, str], ...] = DEFAULT_NEWS_FEEDS
    pc_control: bool = sys.platform == "win32"  # contrôle du PC où tourne le cerveau
    pc_apps: str = ""  # applis supplémentaires : "obs=obs64.exe;notion=notion:"

    # Surveillance en arrière-plan (courriels, rappels de rendez-vous)
    watch_interval_seconds: int = 120
    reminder_minutes: int = 30
    quiet_hours: tuple[int, int] | None = (22, 7)  # pas de voix entre 22 h et 7 h
    briefing_time: tuple[int, int] | None = (7, 30)  # briefing du matin (None = désactivé)
    bill_reminder_days: int = 3  # rappel N jours avant l'échéance d'une facture

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

    @property
    def watch_state_file(self) -> Path:
        return self.data_dir / "watch_state.json"

    @property
    def planner_file(self) -> Path:
        return self.data_dir / "planner.json"

    @property
    def inbox_file(self) -> Path:
        return self.data_dir / "pending_notifications.jsonl"


def _mail_accounts(environ: dict[str, str]) -> tuple[MailAccount, ...]:
    """JARVIS_MAIL_<NOM>_HOST / _USER / _PASSWORD [/ _PORT / _FOLDER], un compte par <NOM>."""
    names = sorted({m.group(1) for k in environ if (m := re.fullmatch(r"JARVIS_MAIL_([A-Z0-9]+)_HOST", k))})
    accounts = []
    for n in names:
        get = lambda field, default="": environ.get(f"JARVIS_MAIL_{n}_{field}", default)  # noqa: E731
        if get("HOST") and get("USER") and get("PASSWORD"):
            accounts.append(MailAccount(
                name=n.lower(), host=get("HOST"), user=get("USER"), password=get("PASSWORD"),
                port=int(get("PORT", "993")), folder=get("FOLDER", "INBOX"),
            ))
    return tuple(accounts)


def _calendars(environ: dict[str, str]) -> tuple[CalendarSource, ...]:
    """JARVIS_CALENDAR_<NOM>_URL : adresse iCal secrète (Google Agenda, Outlook, iCloud…)."""
    found = []
    for key, value in sorted(environ.items()):
        m = re.fullmatch(r"JARVIS_CALENDAR_([A-Z0-9]+)_URL", key)
        if m and value:
            found.append(CalendarSource(m.group(1).lower(), value))
    return tuple(found)


def _clock(value: str | None, default: tuple[int, int] | None) -> tuple[int, int] | None:
    """« 07:30 » -> (7, 30) ; « off » -> None."""
    if value is None:
        return default
    value = value.strip().lower()
    if not value or value in {"off", "none", "non"}:
        return None
    hour, _, minute = value.replace("h", ":").partition(":")
    return int(hour), int(minute or 0)


def _feeds(value: str | None) -> tuple[tuple[str, str], ...]:
    """« Nom|https://…, Autre|https://… » ; vide = flux par défaut."""
    if not value or not value.strip():
        return DEFAULT_NEWS_FEEDS
    feeds = []
    for part in value.split(","):
        name, _, url = part.strip().partition("|")
        if url.startswith(("http://", "https://")):
            feeds.append((name.strip() or url, url.strip()))
    return tuple(feeds) or DEFAULT_NEWS_FEEDS


def _hours(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return (22, 7)
    value = value.strip()
    if not value or value.lower() in {"off", "none", "non"}:
        return None
    start, end = value.split("-")
    return int(start), int(end)


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
        city=env("JARVIS_CITY", "Montréal"),
        region=env("JARVIS_REGION", "Québec"),
        country=env("JARVIS_COUNTRY", "CA"),
        tools_enabled=_bool(env("JARVIS_TOOLS"), True),
        web_search=_bool(env("JARVIS_WEB_SEARCH"), True),
        mail_accounts=_mail_accounts(dict(os.environ)),
        calendars=_calendars(dict(os.environ)),
        news_feeds=_feeds(env("JARVIS_NEWS_FEEDS")),
        pc_control=_bool(env("JARVIS_PC_CONTROL"), sys.platform == "win32"),
        pc_apps=env("JARVIS_PC_APPS", ""),
        watch_interval_seconds=int(env("JARVIS_WATCH_INTERVAL", "120")),
        reminder_minutes=int(env("JARVIS_REMINDER_MINUTES", "30")),
        quiet_hours=_hours(env("JARVIS_QUIET_HOURS")),
        briefing_time=_clock(env("JARVIS_BRIEFING_TIME"), (7, 30)),
        bill_reminder_days=int(env("JARVIS_BILL_REMINDER_DAYS", "3")),
        max_audio_seconds=int(env("JARVIS_MAX_AUDIO_SECONDS", "30")),
        max_text_chars=int(env("JARVIS_MAX_TEXT_CHARS", "2000")),
    )
