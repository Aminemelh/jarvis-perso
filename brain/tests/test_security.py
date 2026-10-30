"""Tableau de bord sécurité : audit, droits admin, révocation immédiate, fuites de données."""

import asyncio
import dataclasses

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fakes import FakeSTT, FakeTTS
from jarvis_brain.audit import AuditLog, summarize
from jarvis_brain.auth import DeviceRegistry
from jarvis_brain.config import Settings
from jarvis_brain.llm.echo import EchoLLM
from jarvis_brain.security import check_breaches
from jarvis_brain.server import create_app
from jarvis_brain.session import Engines


def test_audit_never_stores_sensitive_content(tmp_path):
    assert summarize({"to": "a@b.c", "body": "secret", "data": "base64…", "days": 3, "items": ["x"]}) == \
        {"to": "a@b.c", "body": "…", "data": "…", "days": 3, "items": "…"}
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("tool", device="pc", tool="check_emails", args={}, outcome="ok")
    assert AuditLog(tmp_path / "audit.jsonl").query(events={"tool"})[0]["tool"] == "check_emails"  # relu au démarrage


@pytest.fixture
def env(tmp_path):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    reg = DeviceRegistry(settings.devices_file)
    tokens = {name: reg.add(name) for name in ("pc", "telephone", "vieux-portable")}
    app = create_app(settings, Engines(EchoLLM(), FakeSTT(), FakeTTS()), watch=False, push=False)
    return TestClient(app), tokens, settings


def connect(client, token):
    ws = client.websocket_connect("/ws").__enter__()
    ws.send_json({"type": "auth", "token": token, "display": True})
    hello = ws.receive_json()
    assert hello["type"] == "auth_ok"
    return ws, hello


def test_only_admin_devices_see_the_dashboard(env):
    client, tokens, _ = env
    phone, hello = connect(client, tokens["telephone"])
    assert hello["admin"] is False
    phone.send_json({"type": "security_report"})
    assert phone.receive_json()["code"] == "forbidden"
    phone.send_json({"type": "revoke_device", "device": "pc"})
    assert phone.receive_json()["code"] == "forbidden"
    phone.close()


def test_report_lists_devices_failures_and_activity(env):
    client, tokens, _ = env
    with client.websocket_connect("/ws") as bad:  # une tentative ratée
        bad.send_json({"type": "auth", "token": "jv_faux"})
        with pytest.raises(WebSocketDisconnect):
            bad.receive_json()
    pc, hello = connect(client, tokens["pc"])
    assert hello["admin"] is True
    pc.send_json({"type": "security_report"})
    report = pc.receive_json()["report"]
    names = {d["nom"]: d for d in report["appareils"]}
    assert set(names) == {"pc", "telephone", "vieux-portable"}
    assert names["pc"]["connecte"] and names["pc"]["admin"] and not names["telephone"]["connecte"]
    assert report["echecs_24h"]["total"] == 1
    assert report["fuites"]["disponible"] is False  # pas de clé HIBP ni d'adresse configurée
    assert any("jamais connectés" in t for t in report["conseils"])
    pc.close()


def test_revoke_disconnects_the_device_immediately(env):
    client, tokens, settings = env
    pc, _ = connect(client, tokens["pc"])
    phone, _ = connect(client, tokens["telephone"])
    pc.send_json({"type": "revoke_device", "device": "pc"})
    assert pc.receive_json()["code"] == "forbidden"  # pas soi-même
    pc.send_json({"type": "revoke_device", "device": "telephone"})
    assert pc.receive_json() == {"type": "revoked", "device": "telephone"}
    with pytest.raises(WebSocketDisconnect) as exc:
        phone.receive_json()
    assert exc.value.code == 4401
    assert DeviceRegistry(settings.devices_file).verify(tokens["telephone"]) is None
    with client.websocket_connect("/ws") as again:
        again.send_json({"type": "auth", "token": tokens["telephone"]})
        with pytest.raises(WebSocketDisconnect):
            again.receive_json()
    pc.close()


def test_hibp_check():
    def handler(req: httpx.Request):
        assert req.headers["hibp-api-key"] == "cle"
        if "propre" in str(req.url):
            return httpx.Response(404)
        return httpx.Response(200, json=[{"Name": "Adobe", "Title": "Adobe", "BreachDate": "2013-10-04",
                                          "DataClasses": ["Email addresses", "Passwords"]}])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res = asyncio.run(check_breaches(["fuite@example.com", "propre@example.com"], "cle", http))
    assert res["adresses"]["propre@example.com"] == []
    assert res["adresses"]["fuite@example.com"][0]["nom"] == "Adobe"
    assert asyncio.run(check_breaches(["x@y.z"], ""))["disponible"] is False
