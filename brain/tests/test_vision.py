"""Vision : photo jointe avec le bouton 📷, et « Jarvis, regarde ça » (capture à distance)."""

import base64
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from fakes import FakeSTT, FakeTTS
from jarvis_brain.auth import DeviceRegistry
from jarvis_brain.config import Settings
from jarvis_brain.server import create_app
from jarvis_brain.session import Engines
from jarvis_brain.vision import PhotoError, decode_photo

JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 500
PHOTO = base64.b64encode(JPEG).decode()


def test_decode_photo_validation():
    raw, mime = decode_photo(PHOTO)
    assert raw == JPEG and mime == "image/jpeg"
    assert decode_photo("data:image/jpeg;base64," + PHOTO)[1] == "image/jpeg"
    for bad in ["pas du base64 !!", base64.b64encode(b"<svg/>").decode(), base64.b64encode(b"GIF89a....").decode()]:
        with pytest.raises(PhotoError):
            decode_photo(bad)
    with pytest.raises(PhotoError, match="volumineuse"):
        decode_photo("A" * (5 * 1024 * 1024))


class SpyLLM:
    """Retient ce que reçoit le modèle ; demande une photo si on lui dit « regarde »."""

    def __init__(self):
        self.seen = []

    async def stream(self, system, messages, toolbox=None):
        self.seen.append(messages[-1]["content"])
        if isinstance(messages[-1]["content"], str) and "regarde" in messages[-1]["content"]:
            result = await toolbox.run("t1", "look", {})
            self.seen.append(result)
        yield "C'est une tasse."


def connect(client, token, camera=True):
    ws = client.websocket_connect("/ws").__enter__()
    ws.send_json({"type": "auth", "token": token, "display": True, "camera": camera})
    assert ws.receive_json()["type"] == "auth_ok"
    return ws


def until(ws, kind):
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
        if msg.get("audio"):
            ws.receive_bytes()


@pytest.fixture
def env(tmp_path):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path)
    token = DeviceRegistry(settings.devices_file).add("telephone")
    llm = SpyLLM()
    app = create_app(settings, Engines(llm, FakeSTT(), FakeTTS()), watch=False, push=False)
    return TestClient(app), token, llm, settings


def test_photo_button_attaches_image_to_question(env):
    client, token, llm, settings = env
    ws = connect(client, token)
    ws.send_json({"type": "photo", "data": PHOTO, "question": "C'est quoi ça ?"})
    assert until(ws, "photo_ok")
    until(ws, "reply_end")
    content = llm.seen[-1]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert content[1] == {"type": "text", "text": "C'est quoi ça ?"}
    # L'historique garde une trace textuelle, pas la photo.
    history = settings.history_file.read_text(encoding="utf-8")
    assert "C'est quoi ça ? [avec une photo]" in history and PHOTO not in history
    ws.send_json({"type": "photo", "data": base64.b64encode(b"<svg/>").decode()})
    assert until(ws, "error")["code"] == "bad_photo"
    ws.close()


def test_look_tool_asks_the_device_camera(env):
    client, token, llm, _ = env
    ws = connect(client, token)
    ws.send_json({"type": "text", "text": "Jarvis, regarde ça"})
    request = until(ws, "camera")
    assert request["action"] == "capture"
    ws.send_json({"type": "camera_result", "request_id": request["request_id"], "data": PHOTO})
    until(ws, "reply_end")
    result = llm.seen[-1]
    assert not result.get("is_error")
    assert result["content"][0]["type"] == "image"
    ws.close()


def test_look_without_camera_is_explained(env):
    client, token, llm, _ = env
    ws = connect(client, token, camera=False)
    ws.send_json({"type": "text", "text": "regarde"})
    until(ws, "reply_end")
    assert llm.seen[-1]["is_error"] and "pas de caméra" in llm.seen[-1]["content"]
    ws.close()


def test_camera_refusal_is_reported(env):
    client, token, llm, _ = env
    ws = connect(client, token)
    ws.send_json({"type": "text", "text": "regarde"})
    request = until(ws, "camera")
    ws.send_json({"type": "camera_result", "request_id": request["request_id"], "error": "Photo annulée."})
    until(ws, "reply_end")
    assert llm.seen[-1]["is_error"] and "Photo annulée" in llm.seen[-1]["content"]
    ws.close()
