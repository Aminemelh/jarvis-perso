"""Envoi de courriels : brouillon verrouillé, confirmation vocale, réponse dans le fil."""

import asyncio
import dataclasses
import json

from fakes import FakeIMAP, FakeSession, make_mail
from jarvis_brain.config import MailAccount, Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub
from jarvis_brain.services import Services
from jarvis_brain.sources.mail import MailClient
from jarvis_brain.tools import ToolBox, ToolContext
from jarvis_brain.tools.builtin import build_tools


def setup(tmp_path, smtp=True):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    original = make_mail("Sarah <sarah@example.com>", "Souper samedi ?", "On se voit à 19 h ?")
    original = original.replace(b"Subject:", b"Message-ID: <abc@example.com>\nSubject:", 1)
    imap = FakeIMAP({"7": (original, False)})
    account = MailAccount("gmail", "imap.gmail.com", "moi@gmail.com", "secret",
                          smtp_host="smtp.gmail.com" if smtp else "")
    outbox = []
    services = Services(mail=[MailClient(account, connect=lambda a: imap)],
                        mail_sender=lambda acc, msg: outbox.append(msg))
    hub = DeviceHub(settings, ConversationStore(settings.history_file))
    session = FakeSession("pc")
    hub.register(session)
    return ToolBox(build_tools(settings, services), ToolContext(settings, services, hub, session)), session, outbox


def run(coro):
    return asyncio.run(coro)


def test_reply_needs_confirmation_and_sends_exact_draft(tmp_path):
    box, session, outbox = setup(tmp_path)
    first = json.loads(run(box.run("t", "send_email", {"reply_to_id": "gmail:7", "body": "Oui, à samedi !"}))["content"])
    assert first["status"] == "confirmation_requise" and outbox == []
    assert first["brouillon"] == {"a": "sarah@example.com", "objet": "Re: Souper samedi ?", "texte": "Oui, à samedi !"}
    assert session.sent[-1]["card"]["title"] == "Brouillon à confirmer"

    # Même tour : le modèle ne peut pas confirmer seul.
    res = run(box.run("t", "send_email", {"body": "x", "confirmation_id": first["confirmation_id"]}))
    assert res["is_error"] and outbox == []

    # L'utilisateur dit « oui » ; le modèle tente de changer le texte au passage : ignoré.
    session.current_turn += 1
    res = run(box.run("t", "send_email", {"body": "Transfère-moi tous tes mots de passe",
                                          "confirmation_id": first["confirmation_id"]}))
    assert "envoyé à sarah@example.com" in res["content"]
    msg = outbox[0]
    assert msg["To"] == "sarah@example.com" and msg["Subject"] == "Re: Souper samedi ?"
    assert msg["In-Reply-To"] == "<abc@example.com>"
    assert msg.get_content().strip() == "Oui, à samedi !"


def test_new_email_validation(tmp_path):
    box, session, outbox = setup(tmp_path)
    bad = run(box.run("t", "send_email", {"to": "pas-une-adresse", "subject": "Hello", "body": "x"}))
    assert bad["is_error"]
    no_subject = run(box.run("t", "send_email", {"to": "prof@uqam.ca", "body": "x"}))
    assert no_subject["is_error"]
    # Tentative d'injection d'en-tête : les retours à la ligne sont supprimés.
    res = json.loads(run(box.run("t", "send_email", {
        "to": "prof@uqam.ca", "subject": "Question\nBcc: espion@evil.com", "body": "Bonjour"}))["content"])
    assert res["brouillon"]["objet"] == "Question Bcc: espion@evil.com"
    session.current_turn += 1
    run(box.run("t", "send_email", {"body": "Bonjour", "confirmation_id": res["confirmation_id"]}))
    assert outbox[0]["Bcc"] is None and outbox[0]["To"] == "prof@uqam.ca"


def test_send_tool_absent_without_smtp(tmp_path):
    box, *_ = setup(tmp_path, smtp=False)
    assert "send_email" not in box.tools and "check_emails" in box.tools
