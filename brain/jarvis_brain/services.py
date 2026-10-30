"""Les sources d'information branchées au cerveau (courriels, agendas, météo, actualités, images)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable
from zoneinfo import ZoneInfo

from .config import Settings
from .oauth import GOOGLE, SPOTIFY, OAuthClient
from .pc import PCBackend, default_backend, parse_apps
from .planner import Planner
from .sources.calendar import CalendarClient
from .sources.gcal import GoogleCalendarWriter
from .sources.images import ImageService
from .sources.mail import MailClient
from .sources import smtp
from .sources.news import NewsClient
from .sources.spotify import SpotifyClient
from .sources.weather import WeatherClient
from .sources.web import SafeFetcher


@dataclass
class Services:
    mail: list[MailClient] = field(default_factory=list)
    calendar: CalendarClient | None = None
    weather: WeatherClient | None = None
    planner: Planner | None = None
    images: ImageService | None = None
    news: NewsClient | None = None
    pc: PCBackend | None = None
    pc_apps: dict[str, str] = field(default_factory=dict)
    mail_sender: Callable = smtp.send  # remplaçable dans les tests
    spotify: SpotifyClient | None = None
    calendar_writer: GoogleCalendarWriter | None = None

    def senders(self) -> list[MailClient]:
        return [c for c in self.mail if c.account.smtp_host and c.account.send_enabled]

    def mail_client(self, account: str) -> MailClient:
        for client in self.mail:
            if client.account.name == account:
                return client
        raise LookupError(f"Compte courriel inconnu : {account}")


def create_services(settings: Settings) -> Services:
    tz = ZoneInfo(settings.timezone)
    fetcher = SafeFetcher()
    images = ImageService(fetcher)
    pc = default_backend() if settings.pc_control else None
    apps = parse_apps(settings.pc_apps)

    async def wake_spotify() -> bool:
        """Aucun lecteur Spotify actif : on ouvre l'appli sur le PC et on lui laisse le temps de démarrer."""
        if pc is None:
            return False
        try:
            await asyncio.to_thread(pc.open, apps.get("spotify", "spotify:"))
        except RuntimeError:
            return False
        await asyncio.sleep(5)
        return True

    spotify_auth = OAuthClient(SPOTIFY, settings.spotify_client_id, "", settings.data_dir)
    google_auth = OAuthClient(GOOGLE, settings.google_client_id, settings.google_client_secret, settings.data_dir)
    return Services(
        mail=[MailClient(a) for a in settings.mail_accounts],
        calendar=CalendarClient(settings.calendars, tz) if settings.calendars else None,
        weather=WeatherClient(settings.timezone),
        planner=Planner(settings.planner_file),
        images=images,
        news=NewsClient(settings.news_feeds, fetcher, images) if settings.news_feeds else None,
        pc=pc,
        pc_apps=apps,
        spotify=SpotifyClient(spotify_auth, wake_player=wake_spotify) if spotify_auth.connected else None,
        calendar_writer=GoogleCalendarWriter(google_auth, settings.timezone) if google_auth.connected else None,
    )
