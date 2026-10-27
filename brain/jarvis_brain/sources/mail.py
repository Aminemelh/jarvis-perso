"""Lecture des courriels en IMAP, en lecture seule.

Jarvis ne peut ni envoyer, ni supprimer, ni marquer comme lu : la boîte est
ouverte en lecture seule (EXAMINE) et les messages sont lus avec BODY.PEEK.
Le contenu d'un courriel est une donnée non fiable (risque d'injection de
prompt) : il est renvoyé tel quel au modèle, jamais exécuté.
"""

from __future__ import annotations

import email
import email.policy
import html
import imaplib
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Callable

from ..config import MailAccount

# Mots qui signalent une facture ou un paiement à faire (sujet du courriel).
BILL_KEYWORDS = [
    "facture", "invoice", "paiement", "payment", "bill", "releve", "relevé", "statement",
    "echeance", "échéance", "montant du", "montant dû", "solde", "renouvellement", "prelevement",
]

_IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def imap_date(d: date) -> str:
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


@dataclass
class MailSummary:
    id: str  # "<compte>:<uid>"
    account: str
    sender: str
    sender_email: str
    subject: str
    date: str
    snippet: str
    unread: bool
    bulk: bool  # infolettre / envoi de masse
    looks_like_bill: bool

    def to_dict(self) -> dict:
        return asdict(self)


_BILL_RE = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in BILL_KEYWORDS) + r")\b", re.IGNORECASE)


def looks_like_bill(subject: str) -> bool:
    # Mots entiers : « solde » est une facture, « Soldes -50 % » est une publicité.
    return bool(_BILL_RE.search(subject))


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(raw)


def body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        content = html_to_text(content)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", content)).strip()


def _flags(meta: bytes) -> set[str]:
    m = re.search(rb"FLAGS \(([^)]*)\)", meta)
    return set(m.group(1).decode().split()) if m else set()


def _uid(meta: bytes) -> str:
    m = re.search(rb"UID (\d+)", meta)
    return m.group(1).decode() if m else "?"


class MailClient:
    def __init__(self, account: MailAccount,
                 connect: Callable[[MailAccount], imaplib.IMAP4] | None = None):
        self.account = account
        self._connect = connect or self._default_connect

    @staticmethod
    def _default_connect(account: MailAccount) -> imaplib.IMAP4:
        conn = imaplib.IMAP4_SSL(account.host, account.port, timeout=20)  # TLS obligatoire
        conn.login(account.user, account.password)
        return conn

    def _session(self) -> imaplib.IMAP4:
        conn = self._connect(self.account)
        typ, _ = conn.select(self.account.folder, readonly=True)
        if typ != "OK":
            conn.logout()
            raise RuntimeError(f"Dossier {self.account.folder!r} introuvable sur {self.account.name}")
        return conn

    def search_uids(self, unread_only: bool, days: int, subject_keywords: list[str] | None = None,
                    sender: str | None = None) -> list[str]:
        criteria: list[str] = ["SINCE", imap_date(date.today() - timedelta(days=max(days, 1)))]
        if unread_only:
            criteria.append("UNSEEN")
        if sender:
            criteria += ["FROM", _quote(sender)]
        conn = self._session()
        try:
            if subject_keywords:
                uids: set[str] = set()
                for kw in subject_keywords:
                    ascii_kw = kw.encode("ascii", "ignore").decode()
                    if not ascii_kw:
                        continue
                    typ, data = conn.uid("SEARCH", *criteria, "SUBJECT", _quote(ascii_kw))
                    if typ == "OK" and data and data[0]:
                        uids.update(data[0].decode().split())
                return sorted(uids, key=int)
            typ, data = conn.uid("SEARCH", *criteria)
            return data[0].decode().split() if typ == "OK" and data and data[0] else []
        finally:
            _logout(conn)

    def fetch_summaries(self, uids: list[str], snippet_chars: int = 300) -> list[MailSummary]:
        if not uids:
            return []
        conn = self._session()
        try:
            typ, data = conn.uid("FETCH", ",".join(uids), "(UID FLAGS BODY.PEEK[])")
            if typ != "OK":
                return []
            out = []
            for item in data:
                if not isinstance(item, tuple):
                    continue
                meta, raw = item
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                out.append(self._summary(_uid(meta), _flags(meta), msg, snippet_chars))
            out.sort(key=lambda m: m.date, reverse=True)
            return out
        finally:
            _logout(conn)

    def read(self, uid: str, max_chars: int = 4000) -> dict:
        conn = self._session()
        try:
            typ, data = conn.uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])")
            item = next((d for d in data if isinstance(d, tuple)), None) if typ == "OK" else None
            if item is None:
                raise LookupError(f"Courriel {uid} introuvable.")
            msg = email.message_from_bytes(item[1], policy=email.policy.default)
            summary = self._summary(uid, _flags(item[0]), msg, 0).to_dict()
            summary.pop("snippet")
            summary["body"] = body_text(msg)[:max_chars]
            return summary
        finally:
            _logout(conn)

    def _summary(self, uid: str, flags: set[str], msg: EmailMessage, snippet_chars: int) -> MailSummary:
        name, addr = parseaddr(str(msg.get("From", "")))
        subject = str(msg.get("Subject", "(sans objet)")).strip()
        try:
            when = parsedate_to_datetime(str(msg.get("Date"))).isoformat()
        except (TypeError, ValueError):
            when = datetime.now().isoformat()
        bulk = bool(msg.get("List-Unsubscribe")) or str(msg.get("Precedence", "")).lower() in {"bulk", "list"}
        return MailSummary(
            id=f"{self.account.name}:{uid}",
            account=self.account.name,
            sender=name or addr,
            sender_email=addr,
            subject=subject,
            date=when,
            snippet=body_text(msg)[:snippet_chars] if snippet_chars else "",
            unread="\\Seen" not in flags,
            bulk=bulk,
            looks_like_bill=looks_like_bill(subject),
        )


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "").replace('"', "") + '"'


def _logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass
