# Feuille de route

Tes choix : voiture en **Bluetooth, aux et Android Auto**, télé « autre marque », téléphone **Android**, budget **max local et gratuit** (sauf le LLM), voix **Piper** d'abord.

## Phase 1 : cerveau et client Windows ✅ (cette version)

- [x] Serveur WebSocket avec un token par appareil
- [x] Whisper local, Claude en streaming, Piper local, voix phrase par phrase
- [x] Historique partagé entre appareils, mémoire Markdown en lecture
- [x] Client web universel (bouton, mains libres « Jarvis… », texte)
- [x] Client vocal Python avec « Hey Jarvis » local
- [ ] **À toi de tester** : régler la sensibilité du micro et choisir la taille du modèle Whisper selon ton PC

## Phase 2 : mémoire, outils et autonomie (en cours)

Priorité actuelle : **ordinateur et téléphone**. La télé et la voiture viendront après.

- [x] Outils : courriels (IMAP, lecture seule), factures, agenda (iCal), météo (Open-Meteo), recherche web
- [x] `remember` : Jarvis écrit lui-même dans `data/memory/*.md`
- [x] `show_on_screen` : fiches affichées sur l'appareil courant ou sur un autre (« affiche-le sur mon téléphone »)
- [x] Surveillance en arrière-plan : nouveaux courriels, factures, rappels de rendez-vous
- [x] Notifications sur le dernier appareil utilisé, file d'attente si tout est hors ligne, heures calmes
- [ ] **À toi de tester** avec tes vrais comptes (docs/CONNECTIONS.md)
- [x] Suivi des factures : « à payer / payée », rappels à J-3, la veille, le jour J et en cas de retard
- [x] Briefing du matin (« Bonjour Monsieur, aujourd'hui… ») automatique et à la demande
- [x] Rappels vocaux (« rappelle-moi dans 20 minutes… »)
- [x] Boutons raccourcis sur la page web (briefing, courriels, factures, agenda, rappels)
- [x] Photos à l'écran : actualités (RSS), images de n'importe quelle page, plein écran piloté à la voix (zoom, suivante, ferme)
- [x] Notifications push sur le téléphone même quand la page est fermée (Web Push)
- [x] Contrôle du PC Windows : applis (liste blanche), son, musique, verrouillage, veille/arrêt avec confirmation vocale
- [x] Listes (épicerie, tâches…) cochables, partagées entre appareils
- [x] UQAM : remises Moodle et horaire de cours dans le briefing, rappels avant chaque remise
- [ ] Résumé automatique des vieilles conversations vers la mémoire longue
- [ ] Connexions MCP, domotique (Home Assistant)
- [ ] Option Fish Audio (`tts/fish.py`) à côté de Piper

## Phase 3 : boîtier voiture (Raspberry Pi 5)

**Matériel**
- Raspberry Pi 5 (4 Go), carte microSD A2 ou SSD, boîtier ventilé (habitacle chaud l'été)
- Alimentation 12 V vers USB-C **5 V / 5 A** de qualité (le Pi 5 est exigeant)
- **Coupure propre au contact** : module d'alimentation avec détection d'allumage et petite batterie tampon (type « car PSU » pour Pi), sinon la carte SD finit corrompue. On ajoutera aussi un système de fichiers en lecture seule (overlayfs).
- Micro USB omnidirectionnel, idéalement un micro « conférence » avec réduction de bruit
- Internet : partage de connexion du téléphone (gratuit) ou clé 4G

**Les 3 sorties son, avec priorité automatique**
1. **Bluetooth** : le Pi se connecte à l'autoradio comme un téléphone (PipeWire + A2DP). Utilisé s'il est connecté.
2. **Aux** : le Pi 5 **n'a pas de prise jack**. Il faut une petite carte son USB (≈ 10 $), vers l'entrée aux. Utilisé si le Bluetooth n'est pas connecté.
3. **Android Auto** : un Raspberry Pi ne peut pas se présenter comme un appareil Android Auto. Pour cette voie, c'est le **téléphone Android** qui fait office de client (phase 4), et sa voix passe par Android Auto comme n'importe quel média. Le Pi reste utile quand le téléphone n'est pas branché.

Un script `jarvis-audio-route` choisira la sortie au démarrage et à chaque connexion ou déconnexion Bluetooth.

**Logiciel** : Raspberry Pi OS Lite, `clients/voice` en service systemd (démarrage automatique, redémarrage en cas d'erreur), Tailscale, `JARVIS_WAKE=local`. Plus tard, un mode dégradé hors ligne (Whisper `tiny`, Piper et un petit LLM local) pour les zones sans réseau.

## Phase 4 : télé et téléphone

- **Téléphone Android** : d'abord le client web en PWA (« Ajouter à l'écran d'accueil »). Ensuite une petite app native (Kotlin) avec service en arrière-plan, mot de réveil, sortie Android Auto, et lecture des notifications et SMS (avec ton autorisation explicite).
- **Télé** : dis-moi la marque et le modèle. Selon le cas :
  - navigateur intégré avec micro (rare) : client web ;
  - sinon **Raspberry Pi en HDMI** avec micro USB : même client que la voiture, et un écran « tableau de bord » en plein écran (Chromium en mode kiosque sur le client web).

## Plus tard

- Cerveau sur un mini PC ou un serveur (Docker), sauvegardes chiffrées de `data/`
- Reconnaissance du locuteur (seule ta voix déclenche les actions sensibles)
- Tableau de bord web : historique, mémoire éditable, état des appareils
