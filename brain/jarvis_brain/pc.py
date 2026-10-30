"""Contrôle du PC Windows sur lequel tourne le cerveau.

Jarvis n'exécute JAMAIS de commande libre : seulement des actions précises,
dans une liste fermée (ouvrir une appli de la liste, son, musique, verrouiller,
veille, arrêt). Les actions lourdes (veille, arrêt, redémarrage) passent par
une confirmation vocale imposée par le code (voir tools.ConfirmationGate).
"""

from __future__ import annotations

import re
import subprocess
import sys
import webbrowser
from typing import Protocol

# Applications par défaut : nom prononcé -> commande Windows (exécutable du PATH ou protocole).
DEFAULT_APPS = {
    "navigateur": "https://www.google.com",
    "spotify": "spotify:",
    "explorateur": "explorer.exe",
    "calculatrice": "calc.exe",
    "bloc-notes": "notepad.exe",
    "paramètres": "ms-settings:",
    "gestionnaire des tâches": "taskmgr.exe",
    "discord": "discord:",
    "steam": "steam:",
    "vs code": "vscode:",
    "outlook": "outlook:",
    "courriel": "mailto:",
    "musique": "spotify:",
}

# Touches virtuelles Windows (multimédia)
VK = {"mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF, "next": 0xB0, "previous": 0xB1, "play_pause": 0xB3}


class PCBackend(Protocol):
    def open(self, target: str) -> None: ...
    def key(self, name: str, times: int = 1) -> None: ...
    def lock(self) -> None: ...
    def sleep(self) -> None: ...
    def power(self, action: str) -> None: ...


class WindowsBackend:
    def __init__(self):
        import ctypes

        self.user32 = ctypes.windll.user32
        self.powrprof = ctypes.windll.powrprof

    def open(self, target: str) -> None:
        import os

        os.startfile(target)  # noqa: S606 - cible issue de la liste blanche ou URL http(s) validée

    def key(self, name: str, times: int = 1) -> None:
        vk = VK[name]
        for _ in range(times):
            self.user32.keybd_event(vk, 0, 0, 0)
            self.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP

    def lock(self) -> None:
        self.user32.LockWorkStation()

    def sleep(self) -> None:
        self.powrprof.SetSuspendState(0, 1, 0)

    def power(self, action: str) -> None:
        args = {"shutdown": ["/s", "/t", "60"], "restart": ["/r", "/t", "60"], "cancel": ["/a"]}[action]
        subprocess.run(["shutdown", *args], check=False, capture_output=True)


class UnsupportedBackend:
    """Hors Windows : seules les adresses web peuvent s'ouvrir."""

    def open(self, target: str) -> None:
        if not target.startswith(("http://", "https://")):
            raise RuntimeError("Ouvrir une application n'est possible que sous Windows.")
        webbrowser.open(target)

    def key(self, name: str, times: int = 1) -> None:
        raise RuntimeError("Le contrôle du son n'est possible que sous Windows.")

    def lock(self) -> None:
        raise RuntimeError("Verrouiller n'est possible que sous Windows.")

    def sleep(self) -> None:
        raise RuntimeError("La mise en veille n'est possible que sous Windows.")

    def power(self, action: str) -> None:
        raise RuntimeError("L'arrêt n'est possible que sous Windows.")


def default_backend() -> PCBackend:
    return WindowsBackend() if sys.platform == "win32" else UnsupportedBackend()


def normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def parse_apps(value: str | None) -> dict[str, str]:
    """JARVIS_PC_APPS="obs=obs64.exe;notion=notion:" s'ajoute à la liste par défaut."""
    apps = dict(DEFAULT_APPS)
    for part in (value or "").split(";"):
        name, _, command = part.partition("=")
        if name.strip() and command.strip():
            apps[normalize(name)] = command.strip()
    return apps
