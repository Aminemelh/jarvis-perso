"""Google Agenda en écriture : ajouter un événement (« mets dentiste jeudi à 14 h »).

La lecture continue de passer par l'adresse iCal (aucune connexion nécessaire) ;
seule la création d'événements utilise l'API, avec un jeton limité aux événements.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from ..oauth import OAuthClient

API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class CalendarWriteError(RuntimeError):
    pass


class GoogleCalendarWriter:
    def __init__(self, oauth: OAuthClient, timezone: str, http: httpx.AsyncClient | None = None):
        self.oauth = oauth
        self.timezone = timezone
        self.http = http or httpx.AsyncClient(timeout=15)

    async def create(self, title: str, start: datetime | date, end: datetime | date | None = None,
                     location: str = "", description: str = "", reminder_minutes: int | None = None) -> dict:
        if isinstance(start, datetime):
            end = end or start + timedelta(hours=1)
            when = {"start": {"dateTime": start.isoformat(), "timeZone": self.timezone},
                    "end": {"dateTime": end.isoformat(), "timeZone": self.timezone}}
        else:
            end = end or start + timedelta(days=1)
            when = {"start": {"date": start.isoformat()}, "end": {"date": end.isoformat()}}
        body = {"summary": title, "location": location, "description": description, **when}
        if reminder_minutes is not None:
            body["reminders"] = {"useDefault": False,
                                 "overrides": [{"method": "popup", "minutes": reminder_minutes}]}
        token = await self.oauth.access_token()
        r = await self.http.post(API, json=body, headers={"Authorization": f"Bearer {token}"})
        if r.status_code not in (200, 201):
            raise CalendarWriteError(f"Google Agenda a refusé l'événement (HTTP {r.status_code}).")
        data = r.json()
        return {"id": data.get("id"), "link": data.get("htmlLink"), "title": title}
