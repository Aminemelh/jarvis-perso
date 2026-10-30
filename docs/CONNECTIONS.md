# Brancher tes comptes

Jarvis va chercher les informations **directement à la source**, pas sur l'écran de ton PC ou de ton téléphone : ta boîte Gmail, ton Google Agenda… Il fonctionne donc même quand ton téléphone est éteint, et la réponse est la même où que tu sois.

Tout se règle dans `brain/.env`, puis on redémarre le serveur. Au démarrage, le journal affiche la liste des outils actifs (`Outils : check_emails, find_bills, …`).

## Courriels : Gmail

Jarvis lit en **IMAP**, en **lecture seule** : il ne peut ni envoyer, ni supprimer, ni marquer comme lu.

1. Active la **validation en deux étapes** sur ton compte Google (obligatoire pour l'étape 2).
2. Crée un **mot de passe d'application** sur https://myaccount.google.com/apppasswords (nom : « Jarvis »). Google affiche 16 lettres.
3. Dans `.env` :
   ```
   JARVIS_MAIL_GMAIL_HOST=imap.gmail.com
   JARVIS_MAIL_GMAIL_USER=toi@gmail.com
   JARVIS_MAIL_GMAIL_PASSWORD=abcdefghijklmnop
   ```

Ce mot de passe ne donne accès qu'aux courriels, pas à ton compte Google entier. Tu peux le révoquer à tout moment sur la même page. N'utilise **jamais** ton vrai mot de passe Google.

Autres fournisseurs :

| Fournisseur | `HOST` | Mot de passe |
|---|---|---|
| Gmail | `imap.gmail.com` | mot de passe d'application |
| iCloud | `imap.mail.me.com` | mot de passe pour app (appleid.apple.com) |
| Yahoo | `imap.mail.yahoo.com` | mot de passe d'application |
| Outlook / Hotmail | pas encore pris en charge | Microsoft exige OAuth. En attendant, active le transfert automatique vers Gmail. |

Plusieurs comptes : un bloc par compte, avec un nom différent (`JARVIS_MAIL_UQAM_…`, `JARVIS_MAIL_ICLOUD_…`).

## Agenda : Google Agenda

Jarvis lit l'**adresse iCal secrète** de ton agenda, en lecture seule. Aucun mot de passe n'est nécessaire, mais cette adresse donne accès à tout l'agenda : garde-la secrète comme un mot de passe.

1. Sur un ordinateur, ouvre https://calendar.google.com, puis ⚙️ **Paramètres**.
2. À gauche, clique sur ton agenda, puis **Intégrer l'agenda**.
3. Copie l'**Adresse secrète au format iCal** (elle se termine par `basic.ics`).
4. Dans `.env` :
   ```
   JARVIS_CALENDAR_PERSO_URL=https://calendar.google.com/calendar/ical/.../private-.../basic.ics
   ```

Tu as plusieurs agendas (cours, travail) ? Ajoute une ligne par agenda : `JARVIS_CALENDAR_COURS_URL=…`.

Si l'adresse fuit, clique sur **Réinitialiser** sur la même page de Google Agenda : l'ancienne cesse de fonctionner.

Outlook : Paramètres → Calendrier → Calendriers partagés → **Publier un calendrier** → lien ICS. iCloud : partage public de l'agenda, puis remplace `webcal://` par `https://` (Jarvis le fait aussi tout seul).

## UQAM : remises Moodle et horaire de cours

**Moodle (remises, quiz, examens)** :
1. Sur Moodle, ouvre le **Calendrier**, puis **Importer ou exporter des calendriers** → **Exporter le calendrier**.
2. Choisis **Tous les cours** et **Événements récents et des 60 prochains jours**, puis **Obtenir l'adresse URL du calendrier**.
3. Copie l'adresse dans `.env` :
   ```
   JARVIS_CALENDAR_MOODLE_URL=https://moodle.uqam.ca/calendar/export_execute.php?...
   ```

Cette adresse contient un jeton personnel : garde-la secrète, comme un mot de passe.

**Horaire de cours** : si tes cours sont dans ton Google Agenda (dans un agenda nommé par exemple « Cours »), ajoute son adresse iCal avec `JARVIS_CALENDAR_COURS_URL=…`. Sinon, crée une fois tes cours comme événements récurrents dans un agenda Google « Cours ».

Ce que ça donne :
- dans le **briefing** : « Vous avez un rendez-vous aujourd'hui, le premier : INF1070 à 13 h 30. 3 remises cette semaine, la prochaine : TP2, demain à 23 h 59. »
- des **rappels** 3 jours avant chaque remise, la veille, puis le matin du jour J ;
- « **Quelles sont mes prochaines remises ?** » à tout moment.

Les événements « … s'ouvre » de Moodle sont ignorés : ce ne sont pas des échéances.

## Météo

Rien à configurer : Open-Meteo, gratuit et sans clé. La ville par défaut est `JARVIS_CITY`.

## Actualités

Flux RSS gratuits : Radio-Canada, Le Devoir et La Presse par défaut. Pour en changer, utilise `JARVIS_NEWS_FEEDS` dans `.env` (format `Nom|adresse`, séparés par des virgules). Un flux en panne est simplement ignoré. Si un article n'a pas de photo dans le flux, Jarvis prend l'image principale de la page de l'article.

## Recherche web

C'est l'outil `web_search` de Claude, facturé environ 1 ¢ par recherche (3 recherches maximum par question). Pour le désactiver : `JARVIS_WEB_SEARCH=false`.

## Ce que tu peux demander

- « Jarvis, j'ai des nouveaux courriels ? » → résumé à l'oral, liste à l'écran
- « Est-ce que j'ai des factures à payer ? » → fournisseur, montant et date limite
- « Lis-moi le courriel de Sarah »
- « J'ai quoi aujourd'hui ? » / « C'est quand mon prochain rendez-vous chez le dentiste ? »
- « Affiche ma semaine sur mon téléphone » → la fiche apparaît sur l'appareil nommé (il doit être connecté)
- « Retiens que je préfère les rendez-vous le matin » → écrit dans `data/memory/`
- « Quel temps fera-t-il demain ? »
- « Rappelle-moi dans 20 minutes de sortir le linge », « Demain à 9 h, rappelle-moi d'appeler le dentiste »
- « Suis cette facture » (après qu'il en a trouvé une) → rappel 3 jours avant l'échéance, la veille, le jour J
- « J'ai payé Hydro-Québec » → plus de rappel
- « Mon briefing » → météo, rendez-vous, courriels, factures et rappels du jour
- « Montre-moi deux photos de l'actualité » → deux photos s'affichent, numérotées 1 et 2
- « Zoom sur la première » / « La suivante » / « Ferme » → plein écran, pilotable à la voix
- « Montre-moi une photo de la tour Eiffel » → recherche web, puis image de la page trouvée
- « Affiche ça sur mon téléphone » → la même chose sur un autre appareil connecté
- « Ouvre Spotify », « mets pause », « chanson suivante », « monte le son », « volume à 30 » → sur le PC
- « Ajoute du lait et des œufs à l'épicerie », « j'ai pris le lait », « montre ma liste » → listes partagées entre tous tes appareils
- « Ajoute réviser INF1070 à mes tâches », « qu'est-ce qu'il me reste à faire ? »
- « Quand est mon prochain examen ? », « mes remises de la semaine »
- 📷 puis « C'est quoi ça ? » → il analyse la photo (plante, objet, plat, panneau, document…)
- « Jarvis, regarde ça » → la caméra s'ouvre, 3-2-1, photo, réponse ; « regarde avec le téléphone » pour utiliser un autre appareil
- « Lis-moi ce panneau », « traduis ce menu », « résume cette page » (photo d'un document)
- « Verrouille l'ordi » → immédiat ; « éteins l'ordi » → il demande confirmation, puis éteint dans une minute (« annule » pour l'en empêcher)

Sur la page web, les boutons **Mon briefing**, **Courriels**, **Factures**, **Agenda** et **Rappels** posent ces questions pour toi.

## Ce qu'il fait tout seul

Toutes les 2 minutes (`JARVIS_WATCH_INTERVAL`) :
- **Nouveau courriel important** (les infolettres sont ignorées, sauf si c'est une facture) : « Vous avez reçu une facture d'Hydro-Québec… »
- **Rendez-vous dans 30 minutes** (`JARVIS_REMINDER_MINUTES`) : « Rappel : dentiste dans 20 minutes, Clinique du Plateau. »
- **Tes rappels**, à l'heure demandée (vérifiés toutes les 15 secondes).
- **Factures suivies** : 3 jours avant l'échéance, la veille, le jour J et en cas de retard, jusqu'à ce que tu dises qu'elle est payée.
- **Briefing du matin** à 7 h 30 (`JARVIS_BRIEFING_TIME`) : « Bonjour Monsieur. Aujourd'hui à Montréal : pluie… »

La notification est **dite sur le dernier appareil que tu as utilisé**, et **affichée sur tous** ceux qui ont un écran. Si aucun appareil n'est connecté, elle est gardée et te sera dite à la prochaine connexion (« Pendant votre absence… »). Entre 22 h et 7 h (`JARVIS_QUIET_HOURS`), Jarvis affiche sans parler.

Chaque notification est aussi ajoutée à l'historique partagé : tu peux dire « c'était quoi, ce courriel ? » depuis n'importe quel appareil.

## Notifications sur le téléphone, même page fermée

1. Ouvre Jarvis sur ton téléphone, en HTTPS (via Tailscale, voir docs/SECURITY.md).
2. Dans le menu de Chrome, choisis « Ajouter à l'écran d'accueil ».
3. Dans Jarvis : **Réglages → Activer les notifications sur cet appareil**, puis accepte.

Ensuite, quand la page est fermée ou cachée, les rappels, factures, courriels importants et le briefing arrivent comme des notifications Android normales. Touche une notification pour ouvrir Jarvis. Quand la page est affichée, Jarvis parle au lieu de notifier.

## Et le contenu du téléphone (SMS, notifications d'applis) ?

Un navigateur ne peut pas lire tes SMS ni les notifications des autres applis, et c'est voulu. Il faudra la future **application Android** de Jarvis, avec une autorisation « accès aux notifications » que tu accordes explicitement (phase 4). D'ici là, tout ce qui passe par tes courriels et ton agenda est couvert.
