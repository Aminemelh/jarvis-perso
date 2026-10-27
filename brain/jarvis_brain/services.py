"""Les sources d'information branchées au cerveau (courriels, agendas, météo)."""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from .config import Settings
from .sources.calendar import CalendarClient
from .sources.mail import MailClient
from .sources.weather import WeatherClient


@dataclass
class Services:
    mail: list[MailClient] = field(default_factory=list)
    calendar: CalendarClient | None = None
    weather: WeatherClient | None = None

    def mail_client(self, account: str) -> MailClient:
        for client in self.mail:
            if client.account.name == account:
                return client
        raise LookupError(f"Compte courriel inconnu : {account}")


def create_services(settings: Settings) -> Services:
    tz = ZoneInfo(settings.timezone)
    return Services(
        mail=[MailClient(a) for a in settings.mail_accounts],
        calendar=CalendarClient(settings.calendars, tz) if settings.calendars else None,
        weather=WeatherClient(settings.timezone),
    )
