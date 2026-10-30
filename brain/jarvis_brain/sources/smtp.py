"""Envoi de courriels (réponses) en SMTP chiffré, uniquement après confirmation vocale."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from ..config import MailAccount

ADDRESS_RE = r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[a-zA-Z]{2,}$"


def build_message(account: MailAccount, to: str, subject: str, body: str, sender_name: str = "",
                  in_reply_to: str = "", references: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, account.user)) if sender_name else account.user
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=account.user.split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip()
    msg.set_content(body)
    return msg


def send(account: MailAccount, msg: EmailMessage) -> None:
    context = ssl.create_default_context()  # certificat du serveur vérifié
    if account.smtp_port == 465:
        with smtplib.SMTP_SSL(account.smtp_host, 465, context=context, timeout=20) as smtp:
            smtp.login(account.user, account.password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(account.user, account.password)
            smtp.send_message(msg)
