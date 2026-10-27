"""Agenda via l'adresse iCal secrète (Google Agenda, Outlook, iCloud), en lecture seule."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dtime, timedelta, tzinfo

import httpx
import icalendar
import recurring_ical_events

from ..config import CalendarSource


@dataclass
class Event:
    id: str
    calendar: str
    title: str
    start: str  # ISO 8601
    end: str
    all_day: bool
    location: str
    description: str

    def to_dict(self) -> dict:
        return asdict(self)


def _as_datetime(value: date | datetime, tz: tzinfo) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)
    return datetime.combine(value, dtime.min, tzinfo=tz)


def parse_events(ics: bytes | str, calendar: str, start: datetime, end: datetime, tz: tzinfo) -> list[Event]:
    cal = icalendar.Calendar.from_ical(ics)
    events = []
    for comp in recurring_ical_events.of(cal).between(start, end):
        dtstart = comp.get("DTSTART").dt
        dtend_prop = comp.get("DTEND")
        dtend = dtend_prop.dt if dtend_prop else dtstart
        all_day = not isinstance(dtstart, datetime)
        s, e = _as_datetime(dtstart, tz), _as_datetime(dtend, tz)
        events.append(Event(
            id=f"{calendar}:{comp.get('UID', '')}:{s.isoformat()}",
            calendar=calendar,
            title=str(comp.get("SUMMARY", "(sans titre)")),
            start=s.isoformat(),
            end=e.isoformat(),
            all_day=all_day,
            location=str(comp.get("LOCATION", "")),
            description=str(comp.get("DESCRIPTION", ""))[:500],
        ))
    events.sort(key=lambda ev: ev.start)
    return events


class CalendarClient:
    """Télécharge les agendas (avec un cache de quelques minutes) et filtre les événements."""

    def __init__(self, sources: tuple[CalendarSource, ...], tz: tzinfo,
                 http: httpx.AsyncClient | None = None, cache_seconds: int = 300):
        self.sources = sources
        self.tz = tz
        self.http = http or httpx.AsyncClient(timeout=20, follow_redirects=True)
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, bytes]] = {}

    async def _fetch(self, source: CalendarSource) -> bytes:
        cached = self._cache.get(source.name)
        if cached and time.monotonic() - cached[0] < self.cache_seconds:
            return cached[1]
        url = source.url.replace("webcal://", "https://", 1)
        r = await self.http.get(url)
        r.raise_for_status()
        self._cache[source.name] = (time.monotonic(), r.content)
        return r.content

    async def events(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        async def one(source: CalendarSource) -> list[Event]:
            ics = await self._fetch(source)
            return await asyncio.to_thread(parse_events, ics, source.name, start, end, self.tz)

        results = await asyncio.gather(*(one(s) for s in self.sources))
        events = sorted((e for r in results for e in r), key=lambda ev: ev.start)
        if query:
            q = query.lower()
            events = [e for e in events if q in e.title.lower() or q in e.location.lower()
                      or q in e.description.lower()]
        return events

    async def next_events(self, now: datetime, limit: int = 1, query: str | None = None,
                          horizon_days: int = 120) -> list[Event]:
        events = await self.events(now, now + timedelta(days=horizon_days), query)
        upcoming = [e for e in events if datetime.fromisoformat(e.end) > now]
        return upcoming[:limit]
