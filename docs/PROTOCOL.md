# Protocole client ↔ cerveau (v1)

Un seul WebSocket : `ws(s)://<serveur>:8765/ws`. Les messages texte sont en JSON, les messages binaires contiennent de l'audio. N'importe quel langage peut écrire un client (Python, JS, Kotlin pour Android…).

## 1. Authentification

Le **premier** message, dans les 5 secondes :

```json
{"type": "auth", "token": "jv_…", "client": "web/0.2", "audio_replies": true, "display": true}
```

Réponse :

```json
{"type": "auth_ok", "device": "voiture", "protocol": 1, "server": "jarvis-brain/0.1.0",
 "assistant": "Jarvis", "audio": {"input": "pcm_s16le", "output": "wav"}}
```

En cas d'échec, la connexion est fermée avec le code **4401**. Après 5 échecs en une minute depuis la même IP, la connexion est fermée avec le code **4429**.

`display: true` signale que l'appareil a un écran : il peut recevoir des fiches (`display`). Le serveur répond aussi la liste des outils actifs dans `auth_ok.tools`.

Le token passe dans un message et non dans l'URL : les URL finissent souvent dans des logs (proxy, historique).

## 2. Client → cerveau

| Message | Effet |
|---|---|
| `{"type":"text","text":"…"}` | Demande écrite. Interrompt la réponse en cours. |
| `{"type":"audio_start","sample_rate":16000,"encoding":"pcm_s16le","wake_word":false}` | Début d'un enregistrement. Si `wake_word` vaut `false`, la réponse en cours est interrompue. |
| *binaire* | Morceaux de PCM 16 bits mono little-endian, de 8 à 48 kHz (16 kHz conseillé). 30 s maximum. |
| `{"type":"audio_end"}` | Fin de l'enregistrement : transcription puis réponse. |
| `{"type":"cancel"}` | Arrête la réponse en cours. |
| `{"type":"config","audio_replies":false}` | Réponses en texte seulement (pas de WAV). |
| `{"type":"ping"}` | Le cerveau répond `{"type":"pong"}`. |

Avec `wake_word: true`, le cerveau transcrit la phrase et :
- l'**ignore** si elle ne commence pas par « Jarvis » : `{"type":"ignored","text":"…"}` ;
- répond « Oui, Monsieur ? » si c'est seulement « Jarvis » : `{"type":"wake"}` suivi d'une `sentence`. Le client enregistre alors la suite **sans** `wake_word` ;
- sinon traite la demande normalement, sans le mot « Jarvis ».

## 3. Cerveau → client (un tour)

```
{"type":"state","state":"transcribing","turn_id":7}     (si audio)
{"type":"transcript","turn_id":7,"text":"quelle heure est-il"}
{"type":"state","state":"thinking","turn_id":7}
{"type":"reply_delta","turn_id":7,"text":"Il est"}        (streaming du texte, pour l'affichage)
{"type":"reply_delta","turn_id":7,"text":" 14 h 05"}
{"type":"sentence","turn_id":7,"index":0,"text":"Il est 14 h 05, Monsieur.","audio":true,"format":"wav"}
<binaire : WAV de cette phrase>
… autres phrases …
{"type":"reply_end","turn_id":7,"text":"<réponse complète>","cancelled":false}
```

Règles :
- Quand `sentence.audio` vaut `true`, **le message binaire suivant** est le WAV de cette phrase. Le serveur garantit qu'aucun autre message ne s'intercale.
- Joue les WAV dans l'ordre de réception, l'un après l'autre.
- Après un `cancel`, ignore les `sentence` dont le `turn_id` est inférieur ou égal au tour annulé (certains peuvent arriver juste après).
- `{"type":"error","code":"…","message":"…"}` signale une erreur. Avec un `turn_id`, une `sentence` lisible à voix haute suit (par exemple « Je n'arrive pas à joindre mon cerveau… »). La connexion reste ouverte.

Codes d'erreur : `bad_json`, `unknown_type`, `bad_audio`, `too_long`, `turn_failed`.

## 4. Pendant un tour : statut et affichage

```
{"type":"status","text":"Je consulte vos courriels…"}     (un outil est en cours)
{"type":"display","card":{"title":"Courriels non lus","text":"","items":[
   {"title":"Votre facture est prête","subtitle":"Hydro-Québec","detail":"Montant dû : 84,32 $"}]}}
```

Une fiche (`card`) a un `title`, un `text` facultatif et des `items` (`title`, et en option `subtitle` et `detail`). **Affiche tout en texte brut**, jamais en HTML : le contenu vient de courriels, donc de l'extérieur.

## 5. Notifications spontanées

Envoyées à tout moment, hors de tout tour :

```
{"type":"notify","kind":"mail|reminder|digest|info","text":"Vous avez reçu une facture…","card":{…}}
{"type":"sentence","turn_id":null,"index":0,"text":"Vous avez reçu une facture…","audio":true,"format":"wav"}
<binaire : WAV>
```

La `sentence` (la notification parlée) n'est envoyée qu'à l'appareil utilisé le plus récemment, et jamais pendant les heures calmes. `card` vaut `null` pour un appareil sans écran.
