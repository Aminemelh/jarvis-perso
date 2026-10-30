"""Surveillance en arrière-plan : Jarvis te prévient sans que tu demandes.

Toutes les 15 secondes : les rappels que tu lui as demandés.
Toutes les quelques minutes :
- nouveaux courriels (hors infolettres) -> « Nouveau courriel de X : objet » ;
  les factures sont signalées en priorité ;
- rendez-vous qui commence bientôt -> rappel ;
- facture suivie qui arrive à échéance -> rappel (J-3, veille, jour même, retard) ;
- le matin -> briefing du jour.
L'état (courriels déjà signalés, rappels déjà faits) est conservé dans
data/watch_state.json pour ne jamais répéter une notification après un redémarrage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .briefing import _amount, build_briefing, when_due
from .config import Settings
from .hub import DeviceHub
from .services import Services
from .sources.mail import MailSummary

log = logging.getLogger(__name__)

MAX_SEEN = 2000  # mémoire bornée des identifiants déjà signalés
FAST_SECONDS = 15  # fréquence de vérification des rappels


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Watcher:
    def __init__(self, settings: Settings, services: Services, hub: DeviceHub):
        self.settings = settings
        self.services = services
        self.hub = hub
        self.tz = ZoneInfo(settings.timezone)
        self.state = self._load()

    # ---- état persistant ----

    def _load(self) -> dict:
        path = self.settings.watch_state_file
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("watch_state.json illisible, on repart de zéro")
        return {"mail_seen": {}, "reminded": [], "briefing_date": ""}

    def _save(self) -> None:
        path = self.settings.watch_state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        tmp.replace(path)

    # ---- boucle ----

    async def run(self) -> None:
        log.info("Surveillance active : %d compte(s) courriel, %d agenda(s)",
                 len(self.services.mail), len(self.settings.calendars))
        last_full = float("-inf")
        while True:
            try:
                full = time.monotonic() - last_full >= self.settings.watch_interval_seconds
                await self.tick(full=full)
                if full:
                    last_full = time.monotonic()
            except Exception:  # noqa: BLE001 - la surveillance ne doit jamais s'arrêter
                log.exception("Erreur pendant la surveillance")
            await asyncio.sleep(FAST_SECONDS)

    async def tick(self, now: datetime | None = None, full: bool = True) -> None:
        now = now or datetime.now(self.tz)
        await self._check_reminders(now)
        if full:
            # Chaque source est isolée : une boîte courriel en panne ne bloque pas les rappels.
            for check in [*(self._check_mail(c) for c in self.services.mail),
                          self._check_events(now) if self.services.calendar else None,
                          self._check_bills(now) if self.services.planner else None,
                          self._check_briefing(now)]:
                if check is None:
                    continue
                try:
                    await check
                except Exception:  # noqa: BLE001
                    log.exception("Erreur pendant la surveillance")
        self._save()

    # ---- rappels demandés par l'utilisateur ----

    async def _check_reminders(self, now: datetime) -> None:
        if not self.services.planner:
            return
        for r in await self.services.planner.pop_due_reminders(now):
            await self.hub.notify(f"{self.settings.user_title}, vous m'avez demandé de vous rappeler : {r.text}",
                                  {"title": "Rappel", "items": [{"title": r.text}]}, kind="reminder")

    # ---- factures suivies ----

    async def _check_bills(self, now: datetime) -> None:
        today = now.date()
        for bill in self.services.planner.unpaid_bills():
            days = (date.fromisoformat(bill.due_date) - today).days
            if days < 0:
                stage = "retard"
            elif days == 0:
                stage = "j0"
            elif days == 1:
                stage = "j-1"
            elif days <= self.settings.bill_reminder_days:
                stage = f"j-{self.settings.bill_reminder_days}"
            else:
                continue
            if stage in bill.reminded:
                continue
            amount = _amount(bill)
            text = (f"Facture {bill.payee}{' de ' + amount if amount else ''} : "
                    f"{when_due(date.fromisoformat(bill.due_date), today)}.")
            await self.hub.notify(text, {"title": "Facture à payer", "items": [
                {"title": bill.payee, "subtitle": f"{amount} · échéance {bill.due_date}".strip(" ·")}]},
                kind="bill")
            await self.services.planner.record_bill_reminder(bill.id, stage)

    # ---- briefing du matin ----

    async def _check_briefing(self, now: datetime) -> None:
        when = self.settings.briefing_time
        if not when or self.state.get("briefing_date") == now.date().isoformat():
            return
        start = now.replace(hour=when[0], minute=when[1], second=0, microsecond=0)
        # Seulement le matin : si le PC démarre l'après-midi, pas de « bonjour » à 15 h.
        if not (start <= now < start.replace(hour=12, minute=0) if when[0] < 12 else start <= now):
            return
        self.state["briefing_date"] = now.date().isoformat()
        text, card = await build_briefing(self.settings, self.services, now)
        await self.hub.notify(text, card, kind="briefing")

    # ---- courriels ----

    async def _check_mail(self, client) -> None:
        name = client.account.name
        uids = await asyncio.to_thread(client.search_uids, True, 2)
        seen: list[str] = self.state["mail_seen"].get(name)
        if seen is None:
            # Premier passage : on mémorise l'existant sans rien annoncer.
            self.state["mail_seen"][name] = uids[-MAX_SEEN:]
            return
        seen_set = set(seen)
        new = [u for u in uids if u not in seen_set]
        if not new:
            return
        self.state["mail_seen"][name] = (seen + new)[-MAX_SEEN:]
        mails = await asyncio.to_thread(client.fetch_summaries, new[-10:], 200)
        important = [m for m in mails if not m.bulk or m.looks_like_bill]
        if important:
            await self._announce_mail(important)

    async def _announce_mail(self, mails: list[MailSummary]) -> None:
        bills = [m for m in mails if m.looks_like_bill]
        if len(mails) == 1:
            m = mails[0]
            kind = "une facture" if m.looks_like_bill else "un nouveau courriel"
            text = f"Vous avez reçu {kind} de {_short(m.sender, 40)} : {_short(m.subject)}."
        else:
            text = f"Vous avez reçu {len(mails)} nouveaux courriels"
            if bills:
                text += f", dont {len(bills)} facture{'s' if len(bills) > 1 else ''}"
            senders = ", ".join(dict.fromkeys(_short(m.sender, 30) for m in mails[:3]))
            text += f", notamment de {senders}."
        card = {
            "title": "Nouveaux courriels",
            "items": [{"title": m.subject, "subtitle": m.sender + (" · facture" if m.looks_like_bill else ""),
                       "detail": m.snippet} for m in mails],
        }
        await self.hub.notify(text, card, kind="mail")

    # ---- rendez-vous ----

    async def _check_events(self, now: datetime) -> None:
        horizon = now + timedelta(minutes=self.settings.reminder_minutes)
        events = await self.services.calendar.events(now, horizon)
        reminded: list[str] = self.state["reminded"]
        for ev in events:
            start = datetime.fromisoformat(ev.start)
            if ev.all_day or ev.id in reminded or not (now <= start <= horizon):
                continue
            minutes = max(1, round((start - now).total_seconds() / 60))
            where = f", {_short(ev.location, 60)}" if ev.location else ""
            text = f"Rappel : {_short(ev.title)} dans {minutes} minutes{where}."
            card = {"title": "Rendez-vous", "items": [{
                "title": ev.title, "subtitle": start.strftime("%H h %M") + where, "detail": ev.description}]}
            await self.hub.notify(text, card, kind="reminder")
            reminded.append(ev.id)
        self.state["reminded"] = reminded[-500:]
