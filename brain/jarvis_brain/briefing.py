"""Briefing du matin : météo, agenda du jour, courriels, factures et rappels.

Il est composé sans appeler Claude : instantané et gratuit. Chaque source est
facultative, et une source en panne n'empêche pas le reste du briefing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from .config import Settings
from .planner import Bill
from .services import Services

log = logging.getLogger(__name__)


def _hm(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return f"{dt.hour} h {dt.minute:02d}" if dt.minute else f"{dt.hour} h"


def _amount(b: Bill) -> str:
    if b.amount is None:
        return ""
    value = f"{b.amount:.2f}".replace(".", ",").replace(",00", "")
    return f"{value} $" if b.currency in {"CAD", "USD"} else f"{value} {b.currency}"


def when_due(due: date, today: date) -> str:
    days = (due - today).days
    if days < 0:
        return f"en retard de {-days} jour{'s' if -days > 1 else ''}"
    if days == 0:
        return "à payer aujourd'hui"
    if days == 1:
        return "à payer demain"
    return f"à payer dans {days} jours"


async def build_briefing(settings: Settings, services: Services, now: datetime) -> tuple[str, dict]:
    today = now.date()
    parts: list[str] = []
    items: list[dict] = []
    greeting = "Bonjour" if now.hour < 18 else "Bonsoir"
    parts.append(f"{greeting}, {settings.user_title}.")

    async def safe(coro):
        try:
            return await coro
        except Exception:  # noqa: BLE001
            log.warning("Briefing : une source n'a pas répondu", exc_info=True)
            return None

    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    weather, events = await asyncio.gather(
        safe(services.weather.forecast(settings.city, 1)) if services.weather else asyncio.sleep(0),
        safe(services.calendar.events(start, start + timedelta(days=1))) if services.calendar else asyncio.sleep(0),
    )

    if weather and weather.get("jours"):
        d = weather["jours"][0]
        line = f"Aujourd'hui à {settings.city} : {d['conditions']}, de {round(d['min_c'])} à {round(d['max_c'])} degrés"
        if (d.get("probabilite_pluie_pct") or 0) >= 50:
            line += ", prévoyez un parapluie"
        parts.append(line + ".")
        items.append({"title": "Météo", "subtitle": f"{d['conditions']}, {round(d['min_c'])}° à {round(d['max_c'])}°"})

    if events is not None and services.calendar:
        upcoming = [e for e in events if e.all_day or datetime.fromisoformat(e.end) > now]
        if upcoming:
            first = upcoming[0]
            n = len(upcoming)
            text = f"Vous avez {n} rendez-vous aujourd'hui" if n > 1 else "Vous avez un rendez-vous aujourd'hui"
            text += f", le premier : {first.title}" + ("" if first.all_day else f" à {_hm(first.start)}")
            parts.append(text + ".")
            for e in upcoming:
                items.append({"title": e.title, "subtitle": "Toute la journée" if e.all_day else _hm(e.start),
                              "detail": e.location})
        else:
            parts.append("Votre agenda est libre aujourd'hui.")

    if services.mail:
        counts = await safe(_count_important_unread(services))
        if counts:
            unread, bills = counts
            if unread:
                text = f"{unread} courriel{'s' if unread > 1 else ''} non lu{'s' if unread > 1 else ''} vous attend{'ent' if unread > 1 else ''}"
                if bills:
                    text += f", dont {bills} facture{'s' if bills > 1 else ''}"
                parts.append(text[0].upper() + text[1:] + ".")
                items.append({"title": "Courriels", "subtitle": f"{unread} non lu(s)"})

    if services.planner:
        soon = [b for b in services.planner.unpaid_bills()
                if date.fromisoformat(b.due_date) <= today + timedelta(days=7)]
        for b in soon:
            due = date.fromisoformat(b.due_date)
            amount = _amount(b)
            parts.append(f"Facture {b.payee}{' de ' + amount if amount else ''}, {when_due(due, today)}.")
            items.append({"title": f"Facture {b.payee}", "subtitle": f"{amount} · {when_due(due, today)}".strip(" ·")})
        todays = [r for r in services.planner.pending_reminders()
                  if datetime.fromisoformat(r.due).date() == today]
        if todays:
            parts.append(f"Vous avez {len(todays)} rappel{'s' if len(todays) > 1 else ''} prévu{'s' if len(todays) > 1 else ''} aujourd'hui.")
            for r in todays:
                items.append({"title": r.text, "subtitle": f"Rappel à {_hm(r.due)}"})

    if len(parts) == 1:
        parts.append("Rien de particulier à signaler aujourd'hui.")
    card = {"title": f"Briefing du {today.day}/{today.month}", "items": items}
    return " ".join(parts), card


async def _count_important_unread(services: Services) -> tuple[int, int]:
    unread = bills = 0
    for client in services.mail:
        uids = await asyncio.to_thread(client.search_uids, True, 2)
        mails = await asyncio.to_thread(client.fetch_summaries, uids[-30:], 0)
        for m in mails:
            if not m.bulk or m.looks_like_bill:
                unread += 1
                bills += int(m.looks_like_bill)
    return unread, bills
