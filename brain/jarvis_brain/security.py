"""Tableau de bord sécurité : appareils, connexions, activité, fuites de données."""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from .audit import AuditLog
    from .auth import DeviceRegistry
    from .config import Settings
    from .hub import DeviceHub

log = logging.getLogger(__name__)

HIBP_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/"


async def check_breaches(addresses: list[str], api_key: str, http: httpx.AsyncClient | None = None) -> dict:
    """Have I Been Pwned : dans quelles fuites connues apparaissent ces adresses ?"""
    if not api_key:
        return {"disponible": False,
                "raison": "Ajoute JARVIS_HIBP_API_KEY (clé payante, haveibeenpwned.com/API/Key) pour l'activer."}
    http = http or httpx.AsyncClient(timeout=15)
    results = {}
    for address in addresses:
        r = await http.get(HIBP_URL + quote(address), params={"truncateResponse": "false"},
                           headers={"hibp-api-key": api_key, "user-agent": "JarvisPerso"})
        if r.status_code == 404:
            results[address] = []
        elif r.status_code == 200:
            results[address] = [{"nom": b.get("Title") or b.get("Name"), "date": b.get("BreachDate"),
                                 "donnees": b.get("DataClasses", [])[:6]} for b in r.json()]
        else:
            results[address] = {"erreur": f"HTTP {r.status_code}"}
    return {"disponible": True, "adresses": results}


async def build_report(settings: Settings, registry: DeviceRegistry, hub: DeviceHub, audit: AuditLog,
                       breach_check=check_breaches) -> dict:
    now = time.time()
    connected = {}
    for s in hub.connected():
        connected.setdefault(s.device, {"ip": s.ip, "visible": getattr(s, "visible", True)})
    push_devices = hub.push.devices() if hub.push else set()
    last_seen = {}
    for e in audit.query(events={"auth_ok"}, limit=500):
        last_seen[e.get("device")] = e
    devices = [{
        "nom": d.name,
        "cree_le": d.created_at,
        "connecte": d.name in connected,
        "ip": connected.get(d.name, {}).get("ip") or last_seen.get(d.name, {}).get("ip", ""),
        "derniere_connexion": last_seen.get(d.name, {}).get("ts"),
        "notifications_push": d.name in push_devices,
        "admin": d.name in settings.admin_devices,
    } for d in registry.list()]

    failures = audit.query(since=now - 86400, events={"auth_fail"}, limit=500)
    by_ip = Counter(e.get("ip", "?") for e in failures)
    activity = [{k: v for k, v in e.items() if k != "event"} | {"type": e["event"]}
                for e in audit.query(since=now - 7 * 86400, events={"tool", "notify", "revoke"}, limit=60)]
    addresses = [a.user for a in settings.mail_accounts]
    try:
        breaches = await breach_check(addresses, settings.hibp_api_key) if addresses else {
            "disponible": False, "raison": "Aucune adresse courriel configurée."}
    except httpx.HTTPError as e:
        breaches = {"disponible": False, "raison": f"Service injoignable ({type(e).__name__})."}
    return {
        "appareils": devices,
        "echecs_24h": {"total": len(failures), "par_ip": by_ip.most_common(10)},
        "activite": list(reversed(activity)),
        "fuites": breaches,
        "conseils": _advice(devices, len(failures), settings),
    }


def _advice(devices: list[dict], failures: int, settings: Settings) -> list[str]:
    tips = []
    if failures >= 5:
        tips.append(f"{failures} tentatives de connexion échouées en 24 h : vérifie que Jarvis n'est pas exposé "
                    "sur Internet (garde JARVIS_HOST=127.0.0.1 et Tailscale).")
    stale = [d["nom"] for d in devices if not d["derniere_connexion"] and not d["connecte"]]
    if stale:
        tips.append("Appareils jamais connectés : " + ", ".join(stale) + ". Révoque ceux que tu n'utilises pas.")
    if settings.host not in {"127.0.0.1", "localhost", "::1"} and not settings.tls_cert:
        tips.append("Le serveur écoute sur le réseau sans TLS : préfère Tailscale (docs/SECURITY.md).")
    if not tips:
        tips.append("Rien d'anormal.")
    return tips
