"""Faux services pour tester sans réseau, sans compte courriel ni clé API."""

from __future__ import annotations

import re
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone

from jarvis_brain.audio import silent_wav


def make_mail(sender: str, subject: str, body: str, bulk: bool = False, when: datetime | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "moi@example.com"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(when or datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc))
    if bulk:
        msg["List-Unsubscribe"] = "<mailto:unsub@example.com>"
    msg.set_content(body)
    return bytes(msg)


class FakeIMAP:
    """Imite imaplib.IMAP4 juste assez pour MailClient."""

    def __init__(self, mails: dict[str, tuple[bytes, bool]]):
        self.mails = mails  # uid -> (message brut, lu ?)
        self.readonly = None
        self.commands: list[tuple] = []

    def select(self, folder, readonly=False):
        self.readonly = readonly
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.commands.append((command, *args))
        if command == "SEARCH":
            uids = sorted(self.mails, key=int)
            if "UNSEEN" in args:
                uids = [u for u in uids if not self.mails[u][1]]
            if "SUBJECT" in args:
                kw = args[args.index("SUBJECT") + 1].strip('"').lower()
                uids = [u for u in uids if kw in self._subject(u).lower()]
            return "OK", [" ".join(uids).encode()]
        if command == "FETCH":
            data = []
            for uid in args[0].split(","):
                if uid in self.mails:
                    raw, seen = self.mails[uid]
                    flags = "\\Seen" if seen else ""
                    data.append((f"1 (UID {uid} FLAGS ({flags}) BODY[] {{{len(raw)}}}".encode(), raw))
                    data.append(b")")
            return "OK", data
        raise AssertionError(f"commande inattendue {command}")

    def _subject(self, uid: str) -> str:
        m = re.search(rb"^Subject: (.*)$", self.mails[uid][0], re.M)
        return m.group(1).decode() if m else ""

    def logout(self):
        return "BYE", []


class FakeSTT:
    def __init__(self, text="quelle heure est-il"):
        self.text = text

    def transcribe(self, pcm16, sample_rate):
        return self.text


class FakeTTS:
    def synthesize(self, text):
        return silent_wav(0.05)


class FakeSession:
    """Session minimale : enregistre ce qui lui est envoyé."""

    def __init__(self, device="pc", has_display=True):
        self.device = device
        self.has_display = has_display
        self.last_active = 0.0
        self.sent: list[dict] = []
        self.notifications: list[tuple] = []
        self.current_turn = 1
        self.pending_confirmations: dict = {}

    async def send(self, msg, audio=None):
        self.sent.append(msg)

    async def push_notification(self, text, card, kind, speak):
        self.notifications.append((text, card, kind, speak))
