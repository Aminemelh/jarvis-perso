"""Contrôle du PC : liste blanche, son, et confirmation vocale imposée par le code."""

import asyncio
import dataclasses
import json

from fakes import FakeSession
from jarvis_brain.config import Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub
from jarvis_brain.pc import parse_apps
from jarvis_brain.services import Services
from jarvis_brain.tools import ToolBox, ToolContext
from jarvis_brain.tools.builtin import build_tools


class FakePC:
    def __init__(self):
        self.calls = []

    def open(self, target):
        self.calls.append(("open", target))

    def key(self, name, times=1):
        self.calls.append(("key", name, times))

    def lock(self):
        self.calls.append(("lock",))

    def sleep(self):
        self.calls.append(("sleep",))

    def power(self, action):
        self.calls.append(("power", action))


def setup(tmp_path):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    pc = FakePC()
    services = Services(pc=pc, pc_apps=parse_apps("obs=obs64.exe"))
    hub = DeviceHub(settings, ConversationStore(settings.history_file))
    session = FakeSession("telephone")
    hub.register(session)
    return ToolBox(build_tools(settings, services), ToolContext(settings, services, hub, session)), pc, session


def run(coro):
    return asyncio.run(coro)


def test_open_only_allowed_apps_and_https(tmp_path):
    box, pc, _ = setup(tmp_path)
    assert not run(box.run("t", "pc_open", {"target": "Spotify"})).get("is_error")
    assert not run(box.run("t", "pc_open", {"target": "OBS"})).get("is_error")  # ajoutée par JARVIS_PC_APPS
    assert not run(box.run("t", "pc_open", {"target": "https://www.uqam.ca"})).get("is_error")
    for bad in ["cmd.exe /c del C:\\*", "powershell", "file:///C:/Windows/System32/cmd.exe", "C:\\evil.bat"]:
        res = run(box.run("t", "pc_open", {"target": bad}))
        assert res["is_error"] and "liste des applications autorisées" in res["content"]
    assert pc.calls == [("open", "spotify:"), ("open", "obs64.exe"), ("open", "https://www.uqam.ca")]


def test_media_and_volume(tmp_path):
    box, pc, _ = setup(tmp_path)
    run(box.run("t", "pc_media", {"action": "next"}))
    run(box.run("t", "pc_media", {"action": "volume_up", "steps": 3}))
    run(box.run("t", "pc_volume", {"level": 40}))
    assert pc.calls == [("key", "next", 1), ("key", "volume_up", 3),
                        ("key", "volume_down", 50), ("key", "volume_up", 20)]


def test_lock_is_immediate(tmp_path):
    box, pc, _ = setup(tmp_path)
    run(box.run("t", "pc_power", {"action": "lock"}))
    assert pc.calls == [("lock",)]


def test_shutdown_requires_a_new_user_turn(tmp_path):
    box, pc, session = setup(tmp_path)
    first = json.loads(run(box.run("t", "pc_power", {"action": "shutdown"}))["content"])
    assert first["status"] == "confirmation_requise" and pc.calls == []
    code = first["confirmation_id"]

    # Le modèle essaie de confirmer tout seul, dans le même tour : refusé.
    res = run(box.run("t", "pc_power", {"action": "shutdown", "confirmation_id": code}))
    assert res["is_error"] and "pas encore répondu" in res["content"] and pc.calls == []

    # Un code inventé : refusé.
    assert run(box.run("t", "pc_power", {"action": "shutdown", "confirmation_id": "abc123"}))["is_error"]

    # Confirmation utilisée pour une autre action : refusée.
    session.current_turn += 1
    res = run(box.run("t", "pc_power", {"action": "restart", "confirmation_id": code}))
    assert res["is_error"] and pc.calls == []

    # Nouvelle demande, l'utilisateur répond « oui » (nouveau tour) : exécuté une seule fois.
    code = json.loads(run(box.run("t", "pc_power", {"action": "shutdown"}))["content"])["confirmation_id"]
    session.current_turn += 1
    assert not run(box.run("t", "pc_power", {"action": "shutdown", "confirmation_id": code})).get("is_error")
    assert pc.calls == [("power", "shutdown")]
    assert run(box.run("t", "pc_power", {"action": "shutdown", "confirmation_id": code}))["is_error"]  # usage unique


def test_pc_tools_absent_when_disabled(tmp_path):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    assert "pc_open" not in build_tools(settings, Services())
