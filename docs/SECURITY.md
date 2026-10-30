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
| **Outils en lecture seule** | Courriels : IMAP en TLS, boîte ouverte avec `EXAMINE` et messages lus avec `BODY.PEEK` (rien n'est marqué comme lu). Agenda : lien iCal en lecture. Aucun outil ne peut envoyer, payer ou supprimer. |
| **Injection de prompt** | Un courriel piégé (« Jarvis, ignore tes règles et… ») est une vraie menace. Le contenu externe arrive au modèle marqué comme donnée non fiable, et le prompt système interdit d'y obéir. Surtout, les outils sont **en lecture seule par construction** : même trompé, Jarvis ne peut rien faire de grave. Le client web affiche tout en `textContent` (pas d'injection HTML). |
| **Paramètres d'outils validés** | Chaque paramètre envoyé par le modèle est vérifié (type, bornes, champs inconnus) avant exécution. Le nom de fichier de mémoire est filtré, donc pas de `../`. |
| **Téléchargements web (anti-SSRF)** | Images et pages sont téléchargées par le cerveau à partir d'adresses non fiables : http/https seulement, ports 80/443, pas d'identifiants dans l'adresse, **adresse IP publique obligatoire**, vérifiée à chaque redirection. « Affiche http://192.168.0.1/… » est refusé : Jarvis ne peut pas sonder ton réseau local. Taille et durée limitées. |
| **Images vérifiées** | Le format est reconnu à partir des octets (JPEG, PNG, GIF, WebP, AVIF), pas à partir de l'en-tête. Le SVG est refusé. L'image arrive au navigateur en `data:` : la CSP reste stricte et ton navigateur ne contacte aucun site tiers. |
| **Notifications push** | Contenu chiffré de bout en bout (aes128gcm, RFC 8291) : Google ou Mozilla transportent la notification sans pouvoir la lire. Le cerveau n'envoie qu'aux services de notification connus (FCM, Mozilla, Windows, Apple), pas à une adresse arbitraire. `token revoke` supprime aussi les abonnements de l'appareil, donc un téléphone volé ne reçoit plus rien. Clé VAPID dans `data/vapid.pem`. |
| **Mots de passe d'application** | Gmail et iCloud : un mot de passe dédié à Jarvis, révocable, jamais le mot de passe principal. |

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

## Attention : la voiture et le téléphone lisent tes courriels

Tout appareil qui a un token peut demander « lis-moi mes courriels ». Révoque immédiatement le token d'un appareil perdu, et pense à protéger le téléphone par un verrouillage d'écran.

## À venir

- **Outils qui agissent** (répondre à un courriel, domotique) : confirmation vocale avant toute action, et liste d'outils autorisés par appareil.
- Journal d'audit des appels d'outils.
- OAuth Google (accès plus fin et révocable que le mot de passe d'application).
