"""Listes (épicerie, tâches) et remises UQAM / Moodle."""

import asyncio
import dataclasses
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fakes import FakeSession
from jarvis_brain.briefing import build_briefing
from jarvis_brain.config import Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub
from jarvis_brain.planner import Planner
from jarvis_brain.school import is_deadline
from jarvis_brain.services import Services
from jarvis_brain.sources.calendar import Event
from jarvis_brain.tools import ToolBox, ToolContext
from jarvis_brain.tools.builtin import build_tools
from jarvis_brain.watcher import Watcher

TZ = ZoneInfo("America/Toronto")
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)  # lundi


def ev(cal, title, start, uid=None):
    return Event(uid or f"{cal}:{title}", cal, title, start.isoformat(), (start + timedelta(hours=1)).isoformat(),
                 False, "", "")


EVENTS = [
    ev("moodle", "TP2 INF1070 est à remettre", NOW.replace(hour=23, minute=59) + timedelta(days=1)),
    ev("moodle", "TP3 INF1070 s'ouvre", NOW + timedelta(days=1)),
    ev("moodle", "Quiz 4 MAT1060", NOW.replace(hour=13) + timedelta(days=3)),
    ev("cours", "INF1070 - Cours magistral", NOW.replace(hour=13, minute=30)),
    ev("cours", "Examen intra MAT1060", NOW.replace(hour=9, minute=30) + timedelta(days=6)),
    ev("perso", "Remise du vélo au garage", NOW + timedelta(days=1)),
]


class FakeCalendar:
    async def events(self, start, end, query=None):
        return [e for e in EVENTS if start <= datetime.fromisoformat(e.start) <= end]


def setup(tmp_path):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path, quiet_hours=None, briefing_time=None)
    services = Services(planner=Planner(settings.planner_file), calendar=FakeCalendar())
    hub = DeviceHub(settings, ConversationStore(settings.history_file))
    session = FakeSession("telephone")
    hub.register(session)
    box = ToolBox(build_tools(settings, services), ToolContext(settings, services, hub, session))
    return settings, services, session, box, Watcher(settings, services, hub)


def run(coro):
    return asyncio.run(coro)


def test_grocery_list_flow(tmp_path):
    settings, services, phone, box, _ = setup(tmp_path)
    res = run(box.run("t", "list_add", {"list": "Épicerie", "items": ["lait", "œufs", "pain"]}))
    assert "lait, œufs, pain" in res["content"]
    res = run(box.run("t", "list_add", {"list": "épicerie", "items": ["Lait", "café"]}))
    assert "Ajouté à « épicerie » : café" in res["content"] and "Déjà dans la liste : Lait" in res["content"]

    run(box.run("t", "list_update", {"list": "épicerie", "items": ["lait", "pain"], "action": "check"}))
    shown = json.loads(run(box.run("t", "list_show", {"list": "épicerie"}))["content"])
    assert shown["a_faire"] == ["œufs", "café"] and shown["fait"] == ["lait", "pain"]
    assert phone.sent[-1]["type"] == "display"

    run(box.run("t", "list_clear", {"list": "épicerie"}))  # retire seulement ce qui est coché
    assert [i.text for i in Planner(settings.planner_file).lists["épicerie"]] == ["œufs", "café"]
    assert run(box.run("t", "list_update", {"list": "épicerie", "items": ["chocolat"], "action": "check"}))["is_error"]
    overview = json.loads(run(box.run("t", "list_show", {}))["content"])
    assert overview == {"lists": {"épicerie": 2}}


def test_deadline_detection():
    titles = {e.title: is_deadline(e) for e in EVENTS}
    assert titles == {
        "TP2 INF1070 est à remettre": True,
        "TP3 INF1070 s'ouvre": False,           # ouverture, pas une échéance
        "Quiz 4 MAT1060": True,
        "INF1070 - Cours magistral": False,     # un cours n'est pas une remise
        "Examen intra MAT1060": True,
        "Remise du vélo au garage": False,      # agenda perso : pas l'école
    }


def test_school_deadlines_tool(tmp_path, monkeypatch):
    import jarvis_brain.tools.builtin as builtin

    monkeypatch.setattr(builtin, "_now", lambda ctx: NOW)  # date figée : le test ne vieillit pas
    *_, box, _ = setup(tmp_path)
    res = json.loads(run(box.run("t", "school_deadlines", {"days": 7}))["content"])
    assert [r["titre"] for r in res["remises"]][:1] == ["TP2 INF1070 est à remettre"]
    assert res["count"] == 3


def test_deadline_reminders_stages(tmp_path):
    settings, services, phone, box, watcher = setup(tmp_path)
    run(watcher.tick(NOW))
    texts = [n[0] for n in phone.notifications if n[2] == "deadline"]
    assert any("TP2 INF1070" in t and "demain à 23 h 59" in t for t in texts)
    assert any("Quiz 4" in t and "dans 3 jours" in t for t in texts)
    assert not any("s'ouvre" in t for t in texts)
    before = len(phone.notifications)
    run(watcher.tick(NOW + timedelta(hours=3)))  # rien de nouveau le même jour
    assert len(phone.notifications) == before


def test_briefing_mentions_classes_and_deadlines(tmp_path):
    settings, services, *_ = setup(tmp_path)
    text, card = run(build_briefing(settings, services, NOW))
    assert "un rendez-vous aujourd'hui, le premier : INF1070 - Cours magistral à 13 h 30" in text
    assert "3 remises cette semaine, la prochaine : TP2 INF1070 est à remettre, demain à 23 h 59" in text
