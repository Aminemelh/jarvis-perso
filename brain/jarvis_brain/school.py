"""UQAM : remises Moodle et horaire de cours, à partir des agendas iCal.

Moodle exporte un agenda iCal (Calendrier -> Exporter -> URL) qui contient les
dates de remise des travaux, les quiz et les examens. Il suffit de l'ajouter
comme n'importe quel agenda : JARVIS_CALENDAR_MOODLE_URL=…
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from .sources.calendar import Event

SCHOOL_HINTS = ("moodle", "uqam", "cours", "ecole", "universite", "school")

_DEADLINE = re.compile(
    r"\b(remise|remettre|a rendre|due|echeance|date limite|examen|exam|intra|final|quiz|test|"
    r"evaluation|travail|devoir|tp\d*|labo|projet|presentation|ferme|closes)\b", re.I)
# Moodle crée aussi des événements « … s'ouvre » : ce ne sont pas des échéances.
_OPENS = re.compile(r"\b(s.ouvre|ouverture|opens|disponible|available)\b", re.I)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def is_school_calendar(name: str, configured: tuple[str, ...] = ()) -> bool:
    name = name.lower()
    if configured:
        return name in configured
    return any(h in name for h in SCHOOL_HINTS)


def is_deadline(event: Event, configured: tuple[str, ...] = ()) -> bool:
    if not is_school_calendar(event.calendar, configured):
        return False
    title = _plain(event.title)
    if _OPENS.search(title):
        return False
    # Dans l'agenda Moodle, tout ce qui n'est pas une ouverture est une échéance.
    return "moodle" in event.calendar.lower() or bool(_DEADLINE.search(title))


def due_phrase(event: Event, today: date) -> str:
    when = datetime.fromisoformat(event.start)
    days = (when.date() - today).days
    hour = "" if event.all_day else (f" à {when.hour} h {when.minute:02d}" if when.minute else f" à {when.hour} h")
    if days < 0:
        return "date passée"
    if days == 0:
        return "aujourd'hui" + hour
    if days == 1:
        return "demain" + hour
    return f"dans {days} jours ({when.day}/{when.month}){hour}"
