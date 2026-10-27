import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from fakes import FakeIMAP, FakeSession, make_mail
from jarvis_brain.config import CalendarSource, MailAccount, Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub
from jarvis_brain.services import Services
from jarvis_brain.sources.calendar import CalendarClient, parse_events
from jarvis_brain.sources.mail import MailClient
from jarvis_brain.sources.weather import WeatherClient
from jarvis_brain.tools import ToolBox, ToolContext, ToolError, validate
from jarvis_brain.tools.builtin import build_tools

TZ = ZoneInfo("America/Toronto")

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//FR
BEGIN:VEVENT
UID:dentiste-1
DTSTART:20261027T140000Z
DTEND:20261027T150000Z
SUMMARY:Dentiste
LOCATION:Clinique du Plateau
END:VEVENT
BEGIN:VEVENT
UID:cours-1
DTSTART;TZID=America/Toronto:20261026T090000
DTEND;TZID=America/Toronto:20261026T120000
RRULE:FREQ=WEEKLY;COUNT=10
SUMMARY:Cours INF1070
END:VEVENT
END:VCALENDAR
"""


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mailbox():
    return FakeIMAP({
        "1": (make_mail("Hydro-Québec <factures@hydro.qc.ca>", "Votre facture est prête",
                        "Montant dû : 84,32 $. Date limite : 12 novembre 2026."), False),
        "2": (make_mail("Promo <news@shop.com>", "Soldes -50%", "Achetez !", bulk=True), False),
        "3": (make_mail("Sarah <sarah@example.com>", "Souper samedi ?", "On se voit à 19 h ?"), True),
    })


def make_ctx(tmp_path, mailbox=None, calendar=None, weather=None, session=None):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    account = MailAccount("perso", "imap.example.com", "moi", "secret")
    services = Services(
        mail=[MailClient(account, connect=lambda a: mailbox)] if mailbox else [],
        calendar=calendar, weather=weather,
    )
    hub = DeviceHub(settings, ConversationStore(settings.history_file))
    session = session or FakeSession()
    hub.register(session)
    tools = build_tools(settings, services)
    return ToolBox(tools, ToolContext(settings, services, hub, session)), session, hub


# ---------------------------------------------------------------- validation

def test_validate_rejects_bad_inputs():
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}}, "required": ["n"]}
    validate(schema, {"n": 3})
    for bad in [{}, {"n": "3"}, {"n": 0}, {"n": True}, {"n": 2, "x": 1}]:
        with pytest.raises(ToolError):
            validate(schema, bad)


def test_unknown_tool_and_bad_input_are_reported_as_errors(tmp_path):
    box, *_ = make_ctx(tmp_path)
    res = run(box.run("t1", "rm_rf", {}))
    assert res["is_error"] and "inconnu" in res["content"]
    res = run(box.run("t2", "get_weather", {"days": 99}))
    assert res["is_error"] and "maximum" in res["content"]


def test_only_configured_tools_are_offered(tmp_path, mailbox):
    box, *_ = make_ctx(tmp_path)
    assert "check_emails" not in box.tools and "get_events" not in box.tools
    box, *_ = make_ctx(tmp_path, mailbox=mailbox)
    assert {"check_emails", "read_email", "find_bills"} <= set(box.tools)
    assert all(d.get("eager_input_streaming") for d in box.definitions())


# ---------------------------------------------------------------- courriels

def test_check_emails_lists_unread_read_only(tmp_path, mailbox):
    box, *_ = make_ctx(tmp_path, mailbox=mailbox)
    res = run(box.run("t", "check_emails", {}))
    assert not res.get("is_error")
    assert "Hydro" in res["content"] and "Souper" not in res["content"]  # 3 est déjà lu
    assert mailbox.readonly is True  # boîte ouverte en lecture seule (EXAMINE)
    assert all("BODY.PEEK" in c[-1] for c in mailbox.commands if c[0] == "FETCH")


def test_find_bills_and_read_email(tmp_path, mailbox):
    box, *_ = make_ctx(tmp_path, mailbox=mailbox)
    res = run(box.run("t", "find_bills", {}))
    assert '"looks_like_bill": true' in res["content"] and "Soldes" not in res["content"]
    res = run(box.run("t", "read_email", {"id": "perso:1"}))
    assert "84,32" in res["content"]
    res = run(box.run("t", "read_email", {"id": "perso:../../etc"}))
    assert res["is_error"]


# ---------------------------------------------------------------- agenda

def test_parse_events_expands_recurrence():
    start = datetime(2026, 10, 27, tzinfo=TZ)
    events = parse_events(ICS, "perso", start, start + timedelta(days=14), TZ)
    titles = [e.title for e in events]
    assert titles.count("Cours INF1070") == 2 and "Dentiste" in titles
    dentiste = next(e for e in events if e.title == "Dentiste")
    assert dentiste.start.startswith("2026-10-27T10:00")  # 14 h UTC = 10 h à Montréal


def test_next_event_tool(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=ICS))
    cal = CalendarClient((CalendarSource("perso", "https://cal.example/secret.ics"),), TZ,
                         http=httpx.AsyncClient(transport=transport))

    async def go():
        box, *_ = make_ctx(tmp_path, calendar=cal)
        return await box.run("t", "next_event", {"query": "dentiste"})

    res = run(go())
    # Selon l'heure du test, le rendez-vous est passé ou à venir : on vérifie juste le format.
    assert not res.get("is_error") and '"events"' in res["content"]


# ---------------------------------------------------------------- météo

def test_weather_tool(tmp_path):
    def handler(req: httpx.Request):
        if "geocoding" in req.url.host:
            return httpx.Response(200, json={"results": [
                {"name": "Montréal", "admin1": "Québec", "latitude": 45.5, "longitude": -73.6}]})
        return httpx.Response(200, json={
            "current": {"temperature_2m": 4.2, "apparent_temperature": 1.0, "weather_code": 61,
                        "wind_speed_10m": 20, "precipitation": 0.4},
            "daily": {"time": ["2026-10-27"], "weather_code": [63], "temperature_2m_min": [2],
                      "temperature_2m_max": [7], "precipitation_probability_max": [80]},
        })

    async def go():
        weather = WeatherClient("America/Toronto", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        box, *_ = make_ctx(tmp_path, weather=weather)
        return await box.run("t", "get_weather", {})

    res = run(go())
    assert "pluie légère" in res["content"] and "Montréal" in res["content"]


# ---------------------------------------------------------------- écran et mémoire

def test_show_on_screen_targets_devices(tmp_path):
    box, session, hub = make_ctx(tmp_path)
    res = run(box.run("t", "show_on_screen", {"title": "Courriels", "items": [{"title": "Facture"}]}))
    assert not res.get("is_error") and session.sent[-1]["type"] == "display"

    phone = FakeSession("telephone")
    hub.register(phone)
    run(box.run("t", "show_on_screen", {"title": "Agenda", "device": "telephone"}))
    assert phone.sent[-1]["card"]["title"] == "Agenda"

    car = FakeSession("voiture", has_display=False)
    box.context.session = car
    res = run(box.run("t", "show_on_screen", {"title": "x"}))
    assert res["is_error"] and "pas d'écran" in res["content"] and "telephone" in res["content"]


def test_remember_writes_memory_safely(tmp_path):
    box, *_ = make_ctx(tmp_path)
    run(box.run("t", "remember", {"fact": "Préfère le café sans sucre", "topic": "preferences"}))
    assert "café sans sucre" in (tmp_path / "memory" / "preferences.md").read_text(encoding="utf-8")
    res = run(box.run("t", "remember", {"fact": "x", "topic": "../../evil"}))
    assert res["is_error"]
    assert not (tmp_path.parent / "evil.md").exists()
