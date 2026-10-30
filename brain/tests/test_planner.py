"""Rappels, factures suivies et briefing du matin."""

import asyncio
import dataclasses
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fakes import FakeSession
from jarvis_brain.config import Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub
from jarvis_brain.planner import Planner
from jarvis_brain.services import Services
from jarvis_brain.tools import ToolBox, ToolContext
from jarvis_brain.tools.builtin import build_tools
from jarvis_brain.watcher import Watcher

TZ = ZoneInfo("America/Toronto")


class FakeWeather:
    async def forecast(self, place, days=2):
        return {"jours": [{"conditions": "pluie", "min_c": 3.2, "max_c": 8.9, "probabilite_pluie_pct": 80}]}


def setup(tmp_path, briefing=(7, 30)):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path, quiet_hours=None, briefing_time=briefing)
    planner = Planner(settings.planner_file)
    services = Services(weather=FakeWeather(), planner=planner)
    hub = DeviceHub(settings, ConversationStore(settings.history_file))
    session = FakeSession("pc")
    hub.register(session)
    box = ToolBox(build_tools(settings, services), ToolContext(settings, services, hub, session))
    return settings, planner, hub, session, box, Watcher(settings, services, hub)


def run(coro):
    return asyncio.run(coro)


def test_reminder_is_set_then_spoken_once(tmp_path):
    settings, planner, hub, pc, box, watcher = setup(tmp_path, briefing=None)
    when = (datetime.now(TZ) + timedelta(minutes=20)).replace(second=0, microsecond=0)
    res = run(box.run("t", "set_reminder", {"text": "sortir le linge", "when": when.strftime("%Y-%m-%dT%H:%M")}))
    assert not res.get("is_error"), res

    run(watcher.tick(datetime.now(TZ), full=False))
    assert pc.notifications == []  # pas encore l'heure
    run(watcher.tick(when + timedelta(seconds=5), full=False))
    run(watcher.tick(when + timedelta(seconds=20), full=False))
    assert len(pc.notifications) == 1 and "sortir le linge" in pc.notifications[0][0]

    # Survit à un redémarrage : le rappel est marqué comme fait.
    assert Planner(settings.planner_file).pending_reminders() == []


def test_reminder_rejects_past_and_can_be_cancelled(tmp_path):
    *_, box, _ = setup(tmp_path)
    past = (datetime.now(TZ) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    assert run(box.run("t", "set_reminder", {"text": "x", "when": past}))["is_error"]
    assert run(box.run("t", "set_reminder", {"text": "x", "when": "demain"}))["is_error"]
    later = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    res = run(box.run("t", "set_reminder", {"text": "appeler maman", "when": later}))
    rid = __import__("json").loads(res["content"])["id"]
    assert "appeler maman" in run(box.run("t", "list_reminders", {}))["content"]
    assert not run(box.run("t", "cancel_reminder", {"id": rid})).get("is_error")
    assert '"count": 0' in run(box.run("t", "list_reminders", {}))["content"]


def test_bill_tracking_reminds_at_each_stage_until_paid(tmp_path):
    settings, planner, hub, pc, box, watcher = setup(tmp_path, briefing=None)
    now = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
    res = run(box.run("t", "track_bill", {"payee": "Hydro-Québec", "due_date": "2026-11-12", "amount": 84.32}))
    assert '"new": true' in res["content"]
    # Même facture redemandée : pas de doublon.
    assert '"new": false' in run(box.run("t", "track_bill", {"payee": "hydro-québec", "due_date": "2026-11-12"}))["content"]

    run(watcher.tick(now))                          # J-3
    run(watcher.tick(now + timedelta(hours=2)))     # même jour : pas de répétition
    run(watcher.tick(now + timedelta(days=2)))      # veille
    texts = [n[0] for n in pc.notifications]
    assert len(texts) == 2
    assert "84,32 $" in texts[0] and "dans 3 jours" in texts[0] and "demain" in texts[1]

    bill_id = planner.unpaid_bills()[0].id
    run(box.run("t", "mark_bill_paid", {"id": bill_id}))
    run(watcher.tick(now + timedelta(days=3)))      # jour J, mais payée
    assert len(pc.notifications) == 2
    assert '"count": 0' in run(box.run("t", "list_bills", {}))["content"]


def test_morning_briefing_once_per_day_and_only_in_the_morning(tmp_path):
    settings, planner, hub, pc, box, watcher = setup(tmp_path)
    run(planner.add_bill("Bell", datetime(2026, 11, 12).date(), 65.0, "CAD"))
    afternoon = datetime(2026, 11, 10, 15, 0, tzinfo=TZ)
    run(watcher.tick(afternoon))
    assert not any(n[2] == "briefing" for n in pc.notifications)

    morning = datetime(2026, 11, 11, 7, 45, tzinfo=TZ)
    run(watcher.tick(morning))
    run(watcher.tick(morning + timedelta(minutes=30)))
    briefings = [n for n in pc.notifications if n[2] == "briefing"]
    assert len(briefings) == 1
    text = briefings[0][0]
    assert text.startswith("Bonjour, Monsieur.") and "parapluie" in text and "Bell de 65 $" in text and "demain" in text


def test_briefing_tool_shows_card(tmp_path):
    *_, pc, box, _ = setup(tmp_path)
    res = run(box.run("t", "daily_briefing", {}))
    assert "Bonjour" in res["content"] or "Bonsoir" in res["content"]
    assert pc.sent[-1]["type"] == "display"
