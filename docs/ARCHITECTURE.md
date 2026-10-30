# Architecture

## Principe : un cerveau, des terminaux bêtes

Toute l'intelligence (STT, LLM, voix, mémoire, outils) vit dans **le cerveau** (`brain/`). Un appareil n'a besoin que de trois choses : un micro, un haut-parleur et un client qui parle le [protocole](PROTOCOL.md). Conséquences :

- **Même mémoire partout** : il n'y a qu'un historique, côté serveur. Ce que tu dis dans la voiture, Jarvis s'en souvient devant la télé.
- **Nouveaux appareils à moindre coût** : un navigateur suffit. Le client web (`brain/web/`) couvre déjà PC, Android, tablettes et les télés qui ont un navigateur avec micro.
- **Les clients restent légers** : un Raspberry Pi n'a pas à faire tourner Whisper ni le LLM.

## Un tour de parole

```
client                         cerveau
──────                         ───────
micro ─► VAD (début/fin) ─► audio_start + PCM 16 kHz ─► audio_end
                               │
                               ├─ faster-whisper ─► texte
                               ├─ (mot de réveil vérifié si wake_word=true)
                               ├─ historique partagé + mémoire .md ─► prompt
                               ├─ Claude (streaming) ─► morceaux de texte ─► reply_delta
                               ├─ découpage en phrases
                               └─ Piper (phrase N) pendant que Claude écrit la phrase N+1
haut-parleur ◄─ sentence + WAV ◄┘
```

La voix commence **dès la première phrase**, sans attendre la réponse complète. La synthèse de la phrase N se fait pendant que Claude écrit la suivante (`session.py`, `_speak_worker`).

**Interruption (barge-in)** : si tu reparles pendant que Jarvis répond, le client coupe le son et le serveur annule le tour en cours. La partie déjà dite est gardée dans l'historique avec la mention `[interrompu]`.

## Le mot de réveil, à deux endroits

| Où | Comment | Pour qui |
|---|---|---|
| **Sur l'appareil** (`clients/voice`, `JARVIS_WAKE=local`) | openWakeWord, modèle `hey_jarvis` | PC, Raspberry Pi. Rien ne part sur le réseau avant « Hey Jarvis » : mieux pour la vie privée et pour les données 4G. |
| **Sur le serveur** (`wake_word: true`) | Whisper transcrit la phrase, `wakeword.py` vérifie qu'elle commence par « Jarvis » | Navigateurs et télés, où l'on ne peut pas faire tourner openWakeWord facilement. |

Après une réponse, tu as **6 secondes pour enchaîner sans redire « Jarvis »**, pour une conversation naturelle.

## Moteurs interchangeables

Chaque brique est derrière une petite interface, et se choisit dans `.env` :

| Brique | Interface | Implémentations | Plus tard |
|---|---|---|---|
| LLM | `llm/base.py` : `stream(system, messages)` | `claude`, `echo` (test) | modèle local (Ollama) pour le mode hors ligne |
| STT | `stt/__init__.py` : `transcribe(pcm, rate)` | `whisper`, `none` | |
| TTS | `tts/__init__.py` : `synthesize(text) -> wav` | `piper`, `none` | Fish Audio (voix premium) |

Ajouter Fish Audio, par exemple, revient à écrire `tts/fish.py` avec une méthode `synthesize`. Rien d'autre ne change.

## Fichiers du cerveau

| Fichier | Rôle |
|---|---|
| `__main__.py` | ligne de commande : `serve`, `token add/list/revoke`, `say` |
| `config.py` | lecture du `.env` |
| `auth.py` | tokens par appareil (stockés hachés), anti force brute |
| `server.py` | HTTP + WebSocket, en-têtes de sécurité, sert le client web |
| `session.py` | une connexion = une session : tours, annulation, envoi phrase par phrase |
| `conversation.py` | historique partagé (`data/conversation.jsonl`) |
| `prompts.py` | personnalité, mémoire (`data/memory/*.md`), date, heure et appareil |
| `sentences.py` | découpage du flux en phrases, nettoyage du Markdown |
| `wakeword.py` | détection de « Jarvis » dans une transcription |
| `tools/` | outils proposés à Claude (`builtin.py`) et validation de leurs paramètres (`__init__.py`) |
| `sources/` | accès aux données : `mail.py` (IMAP), `calendar.py` (iCal), `weather.py` (Open-Meteo) |
| `services.py` | regroupe les sources configurées dans `.env` |
| `hub.py` | appareils connectés, dernier utilisé, notifications et file d'attente |
| `watcher.py` | surveillance en arrière-plan (courriels, rendez-vous, rappels, factures, briefing) |
| `planner.py` | rappels et factures suivies (`data/planner.json`) |
| `briefing.py` | briefing du jour, composé sans appeler Claude (gratuit et instantané) |

## Outils : comment Jarvis va chercher l'information

```
« J'ai des factures à payer ? »
      │
      ▼
Claude ──tool_use: find_bills──► ToolBox.run ─► validation ─► IMAP (lecture seule)
   ▲                                                              │
   └──────────── tool_result (contenu marqué « non fiable ») ◄────┘
   │
   ├─ tool_use: show_on_screen ─► fiche affichée sur l'appareil
   └─ texte : « Deux factures : Hydro-Québec, 84 $ pour le 12 novembre… » ─► voix
```

La boucle (`llm/claude.py`) continue tant que Claude demande des outils (8 tours au maximum). Les outils d'un même tour s'exécutent en parallèle. Seuls les outils dont la source est configurée sont proposés : sans compte courriel, Claude ne voit pas `check_emails`.

## Autonomie : le hub et le watcher

`watcher.py` tourne en tâche de fond et appelle `hub.notify()`. Le hub choisit **qui parle** (le dernier appareil utilisé) et **qui affiche** (tous les appareils avec écran), met en attente si personne n'est connecté, et écrit la notification dans l'historique partagé. C'est ce qui permet de reprendre la conversation n'importe où.

## Données (`brain/data/`, jamais commitées)

```
data/
  devices.json        empreintes SHA-256 des tokens
  conversation.jsonl  historique (une ligne par message)
  memory/*.md         mémoire longue durée, lue à chaque requête (et écrite par l'outil remember)
  watch_state.json    courriels déjà signalés, rappels déjà faits
  pending_notifications.jsonl   notifications en attente (aucun appareil connecté)
```
