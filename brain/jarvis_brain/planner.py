"""Rappels et factures suivies, conservés dans data/planner.json.

Ce sont les seules données que Jarvis écrit lui-même (avec la mémoire) : elles
restent sur ton PC et n'agissent sur aucun service extérieur.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path


@dataclass
class Reminder:
    id: str
    text: str
    due: str  # ISO 8601 avec fuseau horaire
    device: str = ""  # appareil qui l'a demandé (vide = le dernier utilisé)
    done: bool = False


@dataclass
class Bill:
    id: str
    payee: str
    due_date: str  # AAAA-MM-JJ
    amount: float | None = None
    currency: str = "CAD"
    email_id: str = ""
    paid: bool = False
    reminded: list[str] = field(default_factory=list)  # étapes déjà rappelées : "j-3", "j-1", "j0", "retard"


def _new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(3)}"


class Planner:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self.reminders: list[Reminder] = []
        self.bills: list[Bill] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.reminders = [Reminder(**r) for r in raw.get("reminders", [])]
        self.bills = [Bill(**b) for b in raw.get("bills", [])]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {"reminders": [asdict(r) for r in self.reminders], "bills": [asdict(b) for b in self.bills]}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---- rappels ----

    async def add_reminder(self, text: str, due: datetime, device: str = "") -> Reminder:
        async with self._lock:
            r = Reminder(_new_id("r"), text, due.isoformat(), device)
            self.reminders.append(r)
            # On ne garde que les 200 derniers rappels terminés.
            done = [x for x in self.reminders if x.done]
            if len(done) > 200:
                drop = {x.id for x in done[:-200]}
                self.reminders = [x for x in self.reminders if x.id not in drop]
            self._save()
            return r

    def pending_reminders(self) -> list[Reminder]:
        return sorted((r for r in self.reminders if not r.done), key=lambda r: r.due)

    async def cancel_reminder(self, reminder_id: str) -> bool:
        async with self._lock:
            before = len(self.reminders)
            self.reminders = [r for r in self.reminders if not (r.id == reminder_id and not r.done)]
            changed = len(self.reminders) != before
            if changed:
                self._save()
            return changed

    async def pop_due_reminders(self, now: datetime) -> list[Reminder]:
        async with self._lock:
            due = [r for r in self.reminders if not r.done and datetime.fromisoformat(r.due) <= now]
            for r in due:
                r.done = True
            if due:
                self._save()
            return due

    # ---- factures ----

    async def add_bill(self, payee: str, due_date: date, amount: float | None, currency: str,
                       email_id: str = "") -> tuple[Bill, bool]:
        """Ajoute une facture, sauf si la même (fournisseur + échéance) est déjà suivie."""
        async with self._lock:
            for b in self.bills:
                if b.payee.lower() == payee.lower() and b.due_date == due_date.isoformat():
                    if amount is not None:
                        b.amount = amount
                    self._save()
                    return b, False
            bill = Bill(_new_id("f"), payee, due_date.isoformat(), amount, currency, email_id)
            self.bills.append(bill)
            self._save()
            return bill, True

    def unpaid_bills(self) -> list[Bill]:
        return sorted((b for b in self.bills if not b.paid), key=lambda b: b.due_date)

    async def mark_paid(self, bill_id: str) -> Bill | None:
        async with self._lock:
            for b in self.bills:
                if b.id == bill_id:
                    b.paid = True
                    self._save()
                    return b
            return None

    async def record_bill_reminder(self, bill_id: str, stage: str) -> None:
        async with self._lock:
            for b in self.bills:
                if b.id == bill_id and stage not in b.reminded:
                    b.reminded.append(stage)
            self._save()
