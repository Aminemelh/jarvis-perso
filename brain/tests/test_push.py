"""Notifications push : abonnement, routage, et chiffrement vérifié de bout en bout."""

import asyncio
import base64
import dataclasses
import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from fakes import FakeSession, FakeSTT, FakeTTS
from jarvis_brain.auth import DeviceRegistry
from jarvis_brain.config import Settings
from jarvis_brain.conversation import ConversationStore
from jarvis_brain.hub import DeviceHub
from jarvis_brain.llm.echo import EchoLLM
from jarvis_brain.push import PushService, allowed_endpoint
from jarvis_brain.server import create_app
from jarvis_brain.session import Engines


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def browser_subscription(endpoint="https://fcm.googleapis.com/fcm/send/abc123"):
    """Ce que produirait pushManager.subscribe(), avec la clé privée du « téléphone »."""
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return {"endpoint": endpoint, "keys": {"p256dh": b64(public), "auth": b64(auth)}}, private, auth


@pytest.mark.parametrize("endpoint,ok", [
    ("https://fcm.googleapis.com/fcm/send/x", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://wns2-bl2p.notify.windows.com/w/?token=x", True),
    ("https://web.push.apple.com/x", True),
    ("http://fcm.googleapis.com/x", False),
    ("https://evil.example/fcm.googleapis.com", False),
    ("https://fcm.googleapis.com.evil.example/x", False),
    ("https://192.168.0.1/x", False),
    ("https://fcm.googleapis.com:8443/x", False),
])
def test_only_known_push_services(endpoint, ok):
    assert allowed_endpoint(endpoint) is ok


def test_subscription_validation_and_revoke(tmp_path):
    push = PushService(tmp_path)
    assert len(push.public_key) > 80  # point P-256 non compressé, base64url
    sub, *_ = browser_subscription()
    push.subscribe("telephone", sub)
    with pytest.raises(ValueError):
        push.subscribe("telephone", {"endpoint": "https://evil.example/x", "keys": sub["keys"]})
    assert PushService(tmp_path).devices() == {"telephone"}  # clé et abonnements persistants
    push.remove_device("telephone")
    assert push.devices() == set()


def test_push_payload_is_encrypted_for_the_phone_only(tmp_path, monkeypatch):
    import http_ece
    import pywebpush

    sent = {}

    class Resp:
        status_code = 201
        text = ""
        headers = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        sent.update(url=url, data=data, headers=headers)
        return Resp()

    monkeypatch.setattr(pywebpush.requests, "post", fake_post)
    push = PushService(tmp_path)
    sub, phone_key, auth = browser_subscription()
    push.subscribe("telephone", sub)
    assert asyncio.run(push.send({"telephone"}, "Rappel", "Sortir le linge", "reminder")) == 1

    assert sent["url"] == sub["endpoint"]
    assert sent["headers"]["content-encoding"] == "aes128gcm"
    assert "vapid" in sent["headers"]["authorization"].lower()
    assert b"linge" not in sent["data"]  # chiffré : le service de notification ne peut pas lire
    clear = http_ece.decrypt(sent["data"], private_key=phone_key, auth_secret=auth, version="aes128gcm")
    assert json.loads(clear) == {"title": "Rappel", "body": "Sortir le linge", "tag": "reminder"}


class FakePush:
    def __init__(self, devices):
        self._devices = set(devices)
        self.sent = []

    def devices(self):
        return set(self._devices)

    async def send(self, devices, title, body, tag="jarvis"):
        self.sent.append((sorted(devices), title, body))
        return len(devices)


def test_hub_pushes_only_to_devices_not_looking(tmp_path):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path, quiet_hours=None)
    push = FakePush({"telephone", "pc"})
    hub = DeviceHub(settings, ConversationStore(settings.history_file), push)
    pc, phone = FakeSession("pc"), FakeSession("telephone")
    pc.visible, phone.visible = True, False
    pc.last_active, phone.last_active = 1.0, 2.0
    hub.register(pc)
    hub.register(phone)
    asyncio.run(hub.notify("Facture Bell", None, "bill"))
    assert push.sent == [(["telephone"], "Facture à payer", "Facture Bell")]
    # Le téléphone était le dernier utilisé, mais sa page est cachée : c'est le PC qui parle.
    assert pc.notifications[0][3] is True and phone.notifications[0][3] is False

    hub.unregister(pc)
    hub.unregister(phone)
    asyncio.run(hub.notify("Rappel", None, "reminder"))
    assert push.sent[-1][0] == ["pc", "telephone"]


def test_websocket_subscribe(tmp_path):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    token = DeviceRegistry(settings.devices_file).add("telephone")
    app = create_app(settings, Engines(EchoLLM(), FakeSTT(), FakeTTS()), watch=False)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token, "display": True})
        hello = ws.receive_json()
        assert hello["push_key"]
        sub, *_ = browser_subscription()
        ws.send_json({"type": "push_subscribe", "subscription": sub})
        assert ws.receive_json()["type"] == "push_ok"
        ws.send_json({"type": "push_subscribe", "subscription": {"endpoint": "https://evil.example/x"}})
        assert ws.receive_json()["code"] == "bad_subscription"
    assert PushService(tmp_path).devices() == {"telephone"}
