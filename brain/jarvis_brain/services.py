"""Les sources d'information branchées au cerveau (courriels, agendas, météo, actualités, images)."""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from .config import Settings
from .planner import Planner
from .sources.calendar import CalendarClient
from .sources.images import ImageService
from .sources.mail import MailClient
from .sources.news import NewsClient
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

    def mail_client(self, account: str) -> MailClient:
        for client in self.mail:
            if client.account.name == account:
                return client
        raise LookupError(f"Compte courriel inconnu : {account}")


def create_services(settings: Settings) -> Services:
    tz = ZoneInfo(settings.timezone)
    fetcher = SafeFetcher()
    images = ImageService(fetcher)
    return Services(
        mail=[MailClient(a) for a in settings.mail_accounts],
        calendar=CalendarClient(settings.calendars, tz) if settings.calendars else None,
        weather=WeatherClient(settings.timezone),
        planner=Planner(settings.planner_file),
        images=images,
        news=NewsClient(settings.news_feeds, fetcher, images) if settings.news_feeds else None,
    )
