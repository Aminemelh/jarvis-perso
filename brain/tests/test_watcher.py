import asyncio
import dataclasses
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fakes import FakeIMAP, FakeSession, make_mail
from jarvis_brain.config import MailAccount, Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub, in_quiet_hours
from jarvis_brain.services import Services
from jarvis_brain.sources.calendar import Event
from jarvis_brain.sources.mail import MailClient
from jarvis_brain.watcher import Watcher

TZ = ZoneInfo("America/Toronto")
NOON = datetime(2026, 10, 27, 12, 0, tzinfo=TZ)


class FakeCalendar:
    def __init__(self, events):
        self._events = events

    async def events(self, start, end, query=None):
        return [e for e in self._events if start <= datetime.fromisoformat(e.start) <= end]


def setup(tmp_path, mails=None, events=None, quiet=None):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path, quiet_hours=quiet)
    imap = FakeIMAP(mails or {})
    services = Services(
        mail=[MailClient(MailAccount("perso", "h", "u", "p"), connect=lambda a: imap)] if mails is not None else [],
        calendar=FakeCalendar(events) if events is not None else None,
    )
    store = ConversationStore(settings.history_file)
    hub = DeviceHub(settings, store)
    return settings, imap, hub, Watcher(settings, services, hub), store


def test_new_mail_is_announced_once_and_newsletters_skipped(tmp_path):
    settings, imap, hub, watcher, store = setup(tmp_path, mails={
        "1": (make_mail("Vieux <a@b.c>", "Ancien", "déjà là"), False),
    })
    phone = FakeSession("telephone")
    hub.register(phone)

    asyncio.run(watcher.tick(NOON))  # premier passage : on ne réannonce pas l'existant
    assert phone.notifications == []

    imap.mails["2"] = (make_mail("Hydro-Québec <f@hq.ca>", "Votre facture", "84 $"), False)
    imap.mails["3"] = (make_mail("Promo <n@shop.com>", "Soldes", "!!!", bulk=True), False)
    asyncio.run(watcher.tick(NOON))
    assert len(phone.notifications) == 1
    text_, card, kind, speak = phone.notifications[0]
    assert "facture" in text_ and "Hydro" in text_ and kind == "mail" and speak
    assert "[Notification]" in store.snapshot()[-1].text  # Jarvis pourra en reparler partout

    asyncio.run(watcher.tick(NOON))  # rien de nouveau
    assert len(phone.notifications) == 1

    # Après un redémarrage, l'état est relu : pas de doublon.
    _, _, hub2, watcher2, _ = setup(tmp_path, mails=imap.mails)
    watcher2.services.mail[0]._connect = lambda a: imap
    s2 = FakeSession()
    hub2.register(s2)
    asyncio.run(watcher2.tick(NOON))
    assert s2.notifications == []


def test_event_reminder_once(tmp_path):
    soon = Event("perso:1:x", "perso", "Dentiste", (NOON + timedelta(minutes=20)).isoformat(),
                 (NOON + timedelta(minutes=80)).isoformat(), False, "Clinique du Plateau", "")
    later = Event("perso:2:x", "perso", "Cours", (NOON + timedelta(hours=3)).isoformat(),
                  (NOON + timedelta(hours=4)).isoformat(), False, "", "")
    _, _, hub, watcher, _ = setup(tmp_path, events=[soon, later])
    pc = FakeSession("pc")
    hub.register(pc)
    asyncio.run(watcher.tick(NOON))
    asyncio.run(watcher.tick(NOON + timedelta(minutes=2)))
    assert len(pc.notifications) == 1
    assert "Dentiste dans 20 minutes" in pc.notifications[0][0]


def test_notification_goes_to_last_used_device_and_queues_when_offline(tmp_path):
    settings, _, hub, _, _ = setup(tmp_path)
    pc, phone = FakeSession("pc"), FakeSession("telephone")
    pc.last_active, phone.last_active = 10.0, 20.0
    hub.register(pc)
    hub.register(phone)
    asyncio.run(hub.notify("Test", None))
    speakers = [s.device for s in (pc, phone) if s.notifications and s.notifications[0][3]]
    assert speakers == ["telephone"]  # seul le dernier appareil utilisé parle
    assert pc.notifications  # mais tous reçoivent l'affichage

    hub.unregister(pc)
    hub.unregister(phone)
    asyncio.run(hub.notify("Courriel de Sarah", None))
    assert settings.inbox_file.exists()
    later = FakeSession("voiture")
    asyncio.run(hub.deliver_pending(later))
    assert "Courriel de Sarah" in later.notifications[0][0]
    assert not settings.inbox_file.exists()


def test_quiet_hours():
    assert in_quiet_hours((22, 7), NOON.replace(hour=23))
    assert in_quiet_hours((22, 7), NOON.replace(hour=3))
    assert not in_quiet_hours((22, 7), NOON)
    assert not in_quiet_hours(None, NOON.replace(hour=3))
