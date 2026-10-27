# Sécurité

Jarvis entend ce qui se dit chez toi et dans ta voiture, et il aura bientôt accès à ton calendrier et à ta domotique. Il faut le traiter comme un service sensible.

## Ce qui est en place

| Mesure | Détail |
|---|---|
| **Un token par appareil** | `token add voiture`, `token revoke voiture`. Un appareil volé se révoque sans toucher aux autres. |
| **Tokens hachés** | `data/devices.json` ne contient que des empreintes SHA-256, comparées en temps constant (`hmac.compare_digest`). Le token en clair n'est affiché qu'une fois. |
| **Token hors de l'URL** | Il est envoyé dans le premier message WebSocket, pour ne pas apparaître dans des logs. |
| **Anti force brute** | 5 échecs par minute et par IP : la connexion est fermée avec le code 4429. Un client non authentifié est déconnecté après 5 s. |
| **Écoute locale par défaut** | `JARVIS_HOST=127.0.0.1` : rien n'est exposé sur le réseau tant que tu ne le décides pas. |
| **Limites** | 1 Mo par message, 30 s d'audio, 2 000 caractères de texte : le serveur ne peut pas être saturé par un seul client. |
| **En-têtes HTTP** | CSP stricte (aucun script externe ni inline), `nosniff`, `frame-ancestors 'none'`, `no-referrer`. Pas de page `/docs` exposée. |
| **Secrets** | Clé API et tokens dans `.env`, ignoré par Git (`.gitignore`). |
| **Mot de réveil local** | Avec `JARVIS_WAKE=local`, aucun son ne quitte l'appareil avant « Hey Jarvis ». |

## Accéder à Jarvis depuis tes autres appareils

**N'ouvre pas de port sur ta box Internet.** Utilise [Tailscale](https://tailscale.com) (gratuit pour un usage perso). C'est un réseau privé chiffré (WireGuard) entre tes appareils, qui fonctionne aussi en 4G depuis la voiture.

1. Installe Tailscale sur le PC serveur, ton téléphone et le Raspberry Pi, avec le même compte.
2. Sur le PC serveur, garde `JARVIS_HOST=127.0.0.1` et publie Jarvis **uniquement sur ton réseau Tailscale** :
   ```powershell
   tailscale serve --bg 8765
   ```
   Tailscale fournit un certificat HTTPS valide : `https://<nom-du-pc>.<ton-tailnet>.ts.net`.
3. Sur le téléphone, ouvre cette adresse : le micro fonctionne, car la page est en HTTPS. Dans le menu de Chrome, choisis « Ajouter à l'écran d'accueil » : Jarvis s'ouvre ensuite comme une application.
4. Pour le Pi : `JARVIS_SERVER=wss://<nom-du-pc>.<ton-tailnet>.ts.net/ws`.

N'utilise **pas** `tailscale funnel`, qui expose le service sur Internet.

### Sans Tailscale (réseau local uniquement)

Mets `JARVIS_HOST=0.0.0.0` et fournis un certificat (par exemple généré avec [mkcert](https://github.com/FiloSottile/mkcert)) via `JARVIS_TLS_CERT` et `JARVIS_TLS_KEY`. Sans HTTPS, les navigateurs refusent le micro, sauf sur `localhost`.

## Bonnes pratiques

- Fixe une **limite de dépense** sur ta clé API dans la console Anthropic.
- Révoque tout de suite le token d'un appareil perdu : `python -m jarvis_brain token revoke <nom>`.
- `data/conversation.jsonl` contient tout ce que tu as dit. Sauvegarde-le, mais pas sur un dépôt public. Supprime-le si tu veux que Jarvis oublie.
- Garde les dépendances à jour : `pip install -U -r requirements.txt`.

## À venir (phase 2)

- **Outils sensibles** (domotique, achats, messages) : confirmation vocale avant les actions irréversibles, et liste blanche d'outils par appareil (la télé n'a pas besoin d'ouvrir la porte du garage).
- **Injection de prompt** : le contenu web et les courriels lus par Jarvis seront traités comme des données, jamais comme des ordres.
- Journal d'audit des actions exécutées par les outils.
