"""Serveur HTTP + WebSocket du cerveau.

- GET  /          : client web universel (PC, téléphone, télé, tablette…)
- GET  /health    : état du serveur (sans données sensibles)
- WS   /ws        : protocole voix/texte (voir docs/PROTOCOL.md)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audit import AuditLog
from .auth import AuthRateLimiter, DeviceRegistry
from .config import Settings
from .conversation import ConversationStore
from .hub import DeviceHub
from .push import PushService
from .security import build_report
from .services import Services, create_services
from .session import DeviceSession, Engines, ProtocolError
from .tools import ToolBox, ToolContext
from .tools.builtin import build_tools, server_tools
from .watcher import Watcher

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PROTOCOL_VERSION = 1

# Codes de fermeture WebSocket (plage 4000-4999 réservée aux applications)
CLOSE_AUTH_FAILED = 4401
CLOSE_RATE_LIMITED = 4429
CLOSE_PROTOCOL = 4400

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "media-src 'self' blob:; connect-src 'self'; worker-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(self), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def create_app(settings: Settings, engines: Engines | None = None, services: Services | None = None,
               watch: bool = True, push: PushService | None | bool = True) -> FastAPI:
    registry = DeviceRegistry(settings.devices_file)
    limiter = AuthRateLimiter()
    store = ConversationStore(settings.history_file, settings.history_turns)
    if push is True:
        push = PushService(settings.data_dir)
    push = push or None
    audit = AuditLog(settings.data_dir / "audit.jsonl")
    hub = DeviceHub(settings, store, push, audit)
    services = services or create_services(settings)
    tools = build_tools(settings, services) if settings.tools_enabled else {}
    remote_tools = server_tools(settings) if settings.tools_enabled else []
    state: dict = {"engines": engines}

    def toolbox_for(session: DeviceSession) -> ToolBox | None:
        if not tools:
            return None
        return ToolBox(tools, ToolContext(settings, services, hub, session, audit), remote_tools)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["engines"] is None:
            state["engines"] = await asyncio.to_thread(load_engines, settings)
        if not registry.list():
            log.warning("Aucun appareil enregistré. Crée un token : python -m jarvis_brain token add pc")
        log.info("Outils : %s", ", ".join(sorted(tools)) or "aucun")
        watcher_task = None
        if watch:  # rappels et briefing même sans courriel ni agenda
            watcher_task = asyncio.create_task(Watcher(settings, services, hub).run())
        yield
        if watcher_task:
            watcher_task.cancel()

    app = FastAPI(title="Jarvis brain", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub = hub

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "version": __version__, "protocol": PROTOCOL_VERSION})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        ip = ws.client.host if ws.client else "?"
        await ws.accept()
        if limiter.blocked(ip):
            await ws.close(CLOSE_RATE_LIMITED, "Trop de tentatives")
            return

        # 1) Authentification : le premier message doit être {"type":"auth","token":...}.
        #    Le token ne passe pas dans l'URL, pour ne pas finir dans des logs.
        try:
            first = await asyncio.wait_for(ws.receive_text(), settings.auth_timeout_seconds)
            hello = json.loads(first)
            if not isinstance(hello, dict):
                raise KeyError("auth")
        except (asyncio.TimeoutError, json.JSONDecodeError, KeyError, WebSocketDisconnect, RuntimeError):
            limiter.fail(ip)
            audit.record("auth_fail", ip=ip, reason="pas d'authentification")
            await _safe_close(ws, CLOSE_AUTH_FAILED, "Authentification requise")
            return
        device = registry.verify(hello.get("token", "")) if hello.get("type") == "auth" else None
        if device is None:
            limiter.fail(ip)
            audit.record("auth_fail", ip=ip, reason="token invalide")
            log.warning("Échec d'authentification depuis %s", ip)
            await _safe_close(ws, CLOSE_AUTH_FAILED, "Token invalide")
            return
        limiter.success(ip)
        audit.record("auth_ok", device=device, ip=ip, client=str(hello.get("client", ""))[:40])
        log.info("Appareil connecté : %s (%s)", device, ip)

        async def close_revoked():
            await _safe_close(ws, CLOSE_AUTH_FAILED, "Appareil révoqué")

        session = DeviceSession(
            device=device, settings=settings, engines=state["engines"], store=store,
            send_json=ws.send_json, send_bytes=ws.send_bytes, toolbox_factory=toolbox_for, push=push,
            ip=ip, close=close_revoked,
        )
        is_admin = device in settings.admin_devices
        session.want_audio = bool(hello.get("audio_replies", True))
        session.has_display = bool(hello.get("display", False))
        session.has_camera = bool(hello.get("camera", False))
        await session.send({
            "type": "auth_ok", "device": device, "protocol": PROTOCOL_VERSION,
            "server": f"jarvis-brain/{__version__}",
            "assistant": settings.assistant_name,
            "audio": {"input": "pcm_s16le", "output": "wav"},
            "tools": sorted(tools),
            "push_key": push.public_key if push else None,
            "admin": is_admin,
        })

        # 2) Boucle de messages
        hub.register(session)
        try:
            await hub.deliver_pending(session)
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                try:
                    if msg.get("bytes") is not None:
                        await session.on_bytes(msg["bytes"])
                    elif msg.get("text") is not None:
                        try:
                            data = json.loads(msg["text"])
                        except json.JSONDecodeError:
                            raise ProtocolError("bad_json", "JSON invalide.")
                        if not isinstance(data, dict):
                            raise ProtocolError("bad_json", "Objet JSON attendu.")
                        if data.get("type") in {"security_report", "revoke_device"}:
                            await admin_command(session, data, is_admin)
                        else:
                            await session.on_json(data)
                except ProtocolError as e:
                    await session.send({"type": "error", "code": e.code, "message": e.message})
        except WebSocketDisconnect:
            pass
        finally:
            hub.unregister(session)
            await session.close()
            log.info("Appareil déconnecté : %s", device)

    async def admin_command(session: DeviceSession, data: dict, is_admin: bool) -> None:
        if not is_admin:
            raise ProtocolError("forbidden", "Réservé aux appareils administrateurs (JARVIS_ADMIN_DEVICES).")
        if data["type"] == "security_report":
            await session.send({"type": "security_report", "report": await build_report(settings, registry, hub, audit)})
            return
        target = str(data.get("device", "")).strip().lower()
        if target == session.device:
            raise ProtocolError("forbidden", "Impossible de révoquer l'appareil que tu utilises.")
        if not await asyncio.to_thread(registry.revoke, target):
            raise ProtocolError("not_found", f"Appareil « {target} » introuvable.")
        if push:
            push.remove_device(target)
        kicked = await hub.disconnect(target)
        audit.record("revoke", device=session.device, target=target, disconnected=kicked)
        await session.send({"type": "revoked", "device": target})

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


async def _safe_close(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await ws.close(code, reason)
    except RuntimeError:
        pass


def load_engines(settings: Settings) -> Engines:
    from .llm import create_llm
    from .stt import create_stt
    from .tts import create_tts

    return Engines(llm=create_llm(settings), stt=create_stt(settings), tts=create_tts(settings))
