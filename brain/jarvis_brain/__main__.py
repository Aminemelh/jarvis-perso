"""Ligne de commande du cerveau.

  python -m jarvis_brain serve                 lance le serveur
  python -m jarvis_brain token add <appareil>  crée un token (affiché une seule fois)
  python -m jarvis_brain token list            liste les appareils
  python -m jarvis_brain token revoke <app.>   révoque un appareil
  python -m jarvis_brain say "Bonjour"         teste la voix (écrit test.wav)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .auth import DeviceRegistry
from .config import load_settings

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis_brain", description="Le cerveau de Jarvis")
    parser.add_argument("--env", default=".env", help="fichier de configuration (défaut : .env)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="lance le serveur")

    tok = sub.add_parser("token", help="gère les tokens des appareils")
    tok_sub = tok.add_subparsers(dest="action", required=True)
    tok_add = tok_sub.add_parser("add")
    tok_add.add_argument("name")
    tok_sub.add_parser("list")
    tok_rev = tok_sub.add_parser("revoke")
    tok_rev.add_argument("name")

    say = sub.add_parser("say", help="synthétise une phrase dans un fichier WAV")
    say.add_argument("text")
    say.add_argument("-o", "--output", default="test.wav")

    args = parser.parse_args(argv)
    settings = load_settings(args.env)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "token":
        registry = DeviceRegistry(settings.devices_file)
        if args.action == "add":
            try:
                token = registry.add(args.name)
            except ValueError as e:
                print(e, file=sys.stderr)
                return 1
            print(f"Appareil '{args.name}' créé. Token (copie-le maintenant, il ne sera plus affiché) :\n")
            print(f"  {token}\n")
            return 0
        if args.action == "list":
            for d in registry.list():
                print(f"{d.name:<20} créé le {time.strftime('%Y-%m-%d %H:%M', time.localtime(d.created_at))}")
            return 0
        if args.action == "revoke":
            ok = registry.revoke(args.name)
            if ok and settings.data_dir.joinpath("push_subscriptions.json").exists():
                from .push import PushService

                PushService(settings.data_dir).remove_device(args.name)  # plus de notifications non plus
            print("Révoqué." if ok else "Appareil introuvable.")
            return 0 if ok else 1

    if args.cmd == "say":
        from .tts import create_tts

        audio = create_tts(settings).synthesize(args.text)
        if audio is None:
            print("Aucun moteur TTS configuré (JARVIS_TTS=none).", file=sys.stderr)
            return 1
        with open(args.output, "wb") as f:
            f.write(audio)
        print(f"Écrit : {args.output}")
        return 0

    if args.cmd == "serve":
        import uvicorn

        from .server import create_app

        if settings.host not in LOCAL_HOSTS and not settings.tls_cert:
            logging.warning(
                "Le serveur écoute sur %s sans TLS. Utilise-le seulement sur un réseau de confiance "
                "ou derrière Tailscale (voir docs/SECURITY.md).", settings.host,
            )
        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            ssl_certfile=str(settings.tls_cert) if settings.tls_cert else None,
            ssl_keyfile=str(settings.tls_key) if settings.tls_key else None,
            ws_max_size=1 << 20,  # 1 Mo par message WebSocket
            server_header=False,
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
