"""Tests de bout en bout du WebSocket, avec des moteurs factices."""

import dataclasses

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis_brain.audio import silent_wav
from jarvis_brain.auth import DeviceRegistry
from jarvis_brain.config import Settings
from jarvis_brain.llm.echo import EchoLLM
from jarvis_brain.server import create_app
from jarvis_brain.session import Engines


class FakeSTT:
    def __init__(self, text="quelle heure est-il"):
        self.text = text

    def transcribe(self, pcm16, sample_rate):
        return self.text


class FakeTTS:
    def synthesize(self, text):
        return silent_wav(0.05)


@pytest.fixture
def env(tmp_path):
    settings = dataclasses.replace(Settings(), data_dir=tmp_path, llm_backend="echo")
    token = DeviceRegistry(settings.devices_file).add("test-pc")
    stt = FakeSTT()
    app = create_app(settings, Engines(llm=EchoLLM(), stt=stt, tts=FakeTTS()), watch=False)
    return TestClient(app), token, stt, settings


def connect(client, token):
    ws = client.websocket_connect("/ws").__enter__()
    ws.send_json({"type": "auth", "token": token})
    hello = ws.receive_json()
    assert hello["type"] == "auth_ok"
    assert hello["device"] == "test-pc"
    return ws


def collect_turn(ws):
    """Lit les messages jusqu'à reply_end ; renvoie (messages, nombre de WAV reçus)."""
    msgs, wavs, expect_audio = [], 0, False
    while True:
        if expect_audio:
            data = ws.receive_bytes()
            assert data[:4] == b"RIFF"
            wavs += 1
            expect_audio = False
            continue
        msg = ws.receive_json()
        msgs.append(msg)
        if msg["type"] == "sentence" and msg["audio"]:
            expect_audio = True
        if msg["type"] == "reply_end":
            return msgs, wavs


def test_health_and_security_headers(env):
    client, *_ = env
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_web_client_is_served(env):
    client, *_ = env
    r = client.get("/")
    assert r.status_code == 200 and "Jarvis" in r.text


def test_rejects_bad_token(env):
    client, *_ = env
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": "jv_faux"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_blocks_brute_force(env):
    client, *_ = env
    for _ in range(5):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": "jv_faux"})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    with client.websocket_connect("/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4429


def test_text_turn_streams_sentences_with_audio(env):
    client, token, _, settings = env
    ws = connect(client, token)
    ws.send_json({"type": "text", "text": "Bonjour Jarvis"})
    msgs, wavs = collect_turn(ws)
    types = [m["type"] for m in msgs]
    assert types[0] == "transcript"
    assert "reply_delta" in types
    sentences = [m for m in msgs if m["type"] == "sentence"]
    assert len(sentences) >= 2 and wavs == len(sentences)
    end = msgs[-1]
    assert end["cancelled"] is False and "Bonjour Jarvis" in end["text"]
    # L'échange est mémorisé pour tous les appareils.
    assert "Bonjour Jarvis" in settings.history_file.read_text(encoding="utf-8")
    ws.close()


def test_history_is_shared_between_connections(env):
    client, token, _, settings = env
    ws = connect(client, token)
    ws.send_json({"type": "text", "text": "Retiens le chiffre 42"})
    collect_turn(ws)
    ws.close()
    ws2 = connect(client, token)
    ws2.send_json({"type": "text", "text": "Quel chiffre ?"})
    collect_turn(ws2)
    ws2.close()
    lines = settings.history_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4


def test_audio_turn_uses_stt(env):
    client, token, *_ = env
    ws = connect(client, token)
    ws.send_json({"type": "audio_start", "sample_rate": 16000, "encoding": "pcm_s16le"})
    ws.send_bytes(b"\x00\x00" * 16000)  # 1 s de silence
    ws.send_json({"type": "audio_end"})
    msgs, _ = collect_turn(ws)
    transcript = next(m for m in msgs if m["type"] == "transcript")
    assert transcript["text"] == "quelle heure est-il"
    ws.close()


def test_wake_word_filters_background_speech(env):
    client, token, stt, _ = env
    ws = connect(client, token)
    stt.text = "on regarde un film ce soir"
    ws.send_json({"type": "audio_start", "sample_rate": 16000, "wake_word": True})
    ws.send_bytes(b"\x00\x00" * 16000)
    ws.send_json({"type": "audio_end"})
    assert ws.receive_json()["type"] == "ignored"

    stt.text = "Jarvis, quelle heure est-il ?"
    ws.send_json({"type": "audio_start", "sample_rate": 16000, "wake_word": True})
    ws.send_bytes(b"\x00\x00" * 16000)
    ws.send_json({"type": "audio_end"})
    msgs, _ = collect_turn(ws)
    assert next(m for m in msgs if m["type"] == "transcript")["text"] == "quelle heure est-il ?"
    ws.close()


def test_rejects_oversized_audio(env):
    client, token, *_ = env
    ws = connect(client, token)
    ws.send_json({"type": "audio_start", "sample_rate": 16000})
    chunk = b"\x00\x00" * 16000 * 10
    for _ in range(4):  # 40 s > limite de 30 s
        ws.send_bytes(chunk)
    err = ws.receive_json()
    assert err["type"] == "error" and err["code"] == "too_long"
    ws.close()


def test_protocol_errors_do_not_kill_connection(env):
    client, token, *_ = env
    ws = connect(client, token)
    ws.send_text("pas du json")
    assert ws.receive_json()["code"] == "bad_json"
    ws.send_json({"type": "inconnu"})
    assert ws.receive_json()["code"] == "unknown_type"
    ws.send_json({"type": "ping"})
    assert ws.receive_json()["type"] == "pong"
    ws.close()
