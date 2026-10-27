# Jarvis perso

Assistant vocal personnel façon majordome. **Un seul cerveau** (serveur) et **une seule mémoire**, partagés par tous tes appareils : PC Windows, téléphone, télé, voiture. Il suffit d'un micro, d'un haut-parleur et d'une connexion.

Projet personnel, inspiré de l'idée de [ethanplusai/jarvis](https://github.com/ethanplusai/jarvis), mais écrit de zéro et sans dépendre de macOS.

```
  PC (navigateur ou client vocal) ─┐
  Téléphone (navigateur)          ─┤   WebSocket + token     ┌──────────────────────────────┐
  Télé (navigateur ou Pi HDMI)    ─┼────────────────────────► │ Cerveau (brain/)              │
  Voiture (Raspberry Pi + 4G)     ─┘   (Tailscale hors LAN)   │ Whisper -> Claude -> Piper   │
                                                              │ mémoire + historique partagés │
                                                              └──────────────────────────────┘
```

## Ce qui marche aujourd'hui

| Composant | Rôle |
|---|---|
| `brain/` | Serveur FastAPI : auth par token pour chaque appareil, STT Whisper local, Claude en streaming, voix Piper locale phrase par phrase, historique partagé |
| `brain/web/` | **Client universel** : n'importe quel navigateur (PC, Android, télé, tablette). Bouton pour parler, mode mains libres « Jarvis… », saisie texte |
| `clients/voice/` | Client vocal Python sans écran (Windows, Linux, **Raspberry Pi**) : mot de réveil local « Hey Jarvis » avec openWakeWord |
| **Outils** | Courriels (Gmail, iCloud…), factures à payer, agenda (Google, Outlook…), météo, recherche web, mémoire (« retiens que… »), affichage de fiches sur n'importe quel appareil connecté |
| **Autonomie** | Surveillance en arrière-plan : nouveau courriel important, facture reçue, rappel 30 min avant un rendez-vous. Jarvis le dit sur le dernier appareil utilisé et l'affiche sur les autres. |

Pour brancher tes comptes : [docs/CONNECTIONS.md](docs/CONNECTIONS.md).

Tout est gratuit et local, **sauf le LLM** (Claude, payé à l'usage).

## Démarrage rapide (Windows)

Prérequis : [Python 3.12](https://www.python.org/downloads/) (coche « Add to PATH ») et Git.

```powershell
git clone https://github.com/aminemelh/jarvis-perso.git
cd jarvis-perso\brain
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 1. Configuration
copy .env.example .env
notepad .env            # colle ta clé ANTHROPIC_API_KEY

# 2. Voix française (environ 60 Mo, une seule fois)
python -m piper.download_voices fr_FR-tom-medium --download-dir models/piper
python -m jarvis_brain say "Bonsoir Monsieur. Tous les systèmes sont opérationnels."
# -> écoute test.wav

# 3. Un token par appareil (copie-le, il ne s'affiche qu'une fois)
python -m jarvis_brain token add pc

# 4. Lancement
python -m jarvis_brain serve
```

Ouvre **http://localhost:8765**, colle le token, touche le cercle et parle. Au premier lancement, Whisper télécharge son modèle (environ 500 Mo pour `small`).

Pour que Jarvis te connaisse, copie `data-example\memory\` dans `data\memory\` et complète `profil.md`. Chaque fichier `.md` de ce dossier est lu à chaque requête.

> Pas encore de clé API ? Mets `JARVIS_LLM=echo` dans `.env` : Jarvis répète ce qu'il entend. Ça permet de tester le micro, Whisper et la voix gratuitement.

### Client vocal sans navigateur (mot de réveil « Hey Jarvis »)

```powershell
cd clients\voice
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env     # JARVIS_TOKEN = un token créé avec « token add »
python -m jarvis_client
```

### Depuis ton téléphone ou ta télé

Les navigateurs n'autorisent le micro qu'en **HTTPS**. La méthode la plus simple et la plus sûre est [Tailscale](https://tailscale.com) (gratuit) : voir [docs/SECURITY.md](docs/SECURITY.md#accéder-à-jarvis-depuis-tes-autres-appareils).

## Documentation

- [docs/CONNECTIONS.md](docs/CONNECTIONS.md) : brancher Gmail, Google Agenda, etc., et ce que tu peux demander
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) : comment les morceaux s'emboîtent, et pourquoi
- [docs/PROTOCOL.md](docs/PROTOCOL.md) : le protocole WebSocket (pour écrire un nouveau client)
- [docs/SECURITY.md](docs/SECURITY.md) : tokens, réseau, HTTPS, Tailscale
- [docs/ROADMAP.md](docs/ROADMAP.md) : les phases (mémoire et outils, voiture, télé, téléphone)

## Tests

```bash
cd brain
pip install -r requirements-dev.txt
python -m pytest
```

Les tests utilisent des moteurs et des services factices : ils ne demandent ni clé API, ni micro, ni compte courriel, ni modèle à télécharger.

## Coûts

- Whisper, Piper, openWakeWord : gratuits, en local.
- Claude : payé au token. Une réponse vocale typique (environ 1 500 tokens en entrée, 100 en sortie, plus un peu de réflexion) coûte environ 1 ¢ avec `claude-opus-5-5`. Pour baisser la facture, mets `JARVIS_CLAUDE_MODEL=claude-haiku-5-5` (environ 40 fois moins cher, mais moins fin). Fixe aussi une limite de dépense mensuelle dans la console Anthropic.
