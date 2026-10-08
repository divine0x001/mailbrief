# MailBrief 📬

> **[Read in English 🇬🇧](README.md)**

**Un n8n gratuit, qui tourne chez toi.** Chaque jour, une IA locale lit ta boîte
mail, la résume en priorités, rédige les brouillons de réponse et t'envoie le
tout sur Telegram.

- **100 % local** — Ollama tourne sur ta machine, tes mails ne sortent pas.
- **Gratuit** — zéro abonnement, zéro compte, zéro cloud tiers.
- **Ta vraie boîte** — lecture IMAP directe, aucun double compte.
- **Léger** — un timer launchd à l'heure prévue, plus une icône de barre menu
  qui coûte 0 % CPU au repos.

```
IMAP (ton fournisseur) ──► résumé Ollama ──► Telegram
          ▲                    (local)           │
          └──────── dédoublonnage par UID ◄──────┘
```

---

## Installation rapide

```bash
git clone <ce-dépôt> && cd MailBrief
./install.sh            # venv + dépendances + .env + tests + app macOS
```

Puis remplis `.env` (voir ci-dessous) et vérifie :

```bash
./install.sh --finish    # lance le diagnostic complet
```

Installation manuelle, si tu préfères :

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## Configurer `.env`

### 1. La boîte mail

Choisis ton fournisseur avec `IMAP_PRESET` — l'hôte et le port sont déjà
pré-réglés :

| `IMAP_PRESET` | Fournisseur | Particularité |
|---|---|---|
| `icloud` | iCloud Mail | **mot de passe d'application** obligatoire |
| `gmail` | Gmail | **mot de passe d'application** (2FA actif) |
| `outlook` | Outlook / Office 365 | mot de passe d'application |
| `yahoo` | Yahoo Mail | mot de passe d'application |
| `free` | Free | mot de passe du compte |
| `orange` | Orange | mot de passe du compte |
| `sfr` | SFR | mot de passe du compte |
| `laposte` | La Poste (Mailoo) | mot de passe du compte |
| `proton` | Proton Mail | passe par **Proton Bridge** (port 1143) |
| `custom` | autre | renseigne `IMAP_HOST` / `IMAP_PORT` |

```env
IMAP_PRESET=icloud
IMAP_USER=toi@exemple.fr
IMAP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

> **Mot de passe d'application** — iCloud et Gmail refusent le mot de passe
> normal en IMAP. Génère-en un dédié :
> iCloud → <https://account.apple.com> · Sécurité · Mot de passe d'application.
> Gmail → Gestionnaire de comptes · Sécurité · Mots de passe d'application.

> **Proton** — installe Proton Bridge, lance-le, et utilise le mot de passe
> d'affichage qu'il affiche (l'hôte `127.0.0.1:1143` est déjà le preset).

### 2. L'IA locale

Installe [Ollama](https://ollama.com) puis :

```bash
ollama pull qwen3:8b
```

Tout tourne sur ta machine. Un modèle plus petit (`qwen3:4b`) ou plus grand
(`qwen3:27b`) fonctionnent aussi : change `OLLAMA_MODEL`.

### 3. Telegram

1. Parle à **@BotFather** → `/newbot` → copie le `TELEGRAM_BOT_TOKEN`.
2. Envoie un message à ton bot, puis ouvre
   `https://api.telegram.org/bot<TOKEN>/getUpdates` :
   `"chat":{"id": 123456}` → c'est ton `TELEGRAM_CHAT_ID` (positif en privé,
   négatif `−100…` pour un canal — le bot doit y être administrateur).

## Vérifier

```bash
.venv/bin/python -m mailbrief --check
```

Doit afficher trois ✅. Puis un essai à blanc (n'envoie rien, ne consomme
aucun mail) :

```bash
.venv/bin/python -m mailbrief --dry-run
```

## Planifier le brief quotidien

```bash
./install_schedule.sh 8 0      # tous les jours à 08:00
```

Installe un service **launchd** (`~/Library/LaunchAgents/com.mailbrief.daily.plist`).
Contrairement au cron, il rattrape le run si la machine dormait à l'heure prévue.

```bash
./install_schedule.sh 19 30                          # changer d'heure
launchctl bootout gui/$(id -u)/com.mailbrief.daily   # désinstaller
tail -f data/launchd.log                             # suivre les logs
```

Avec cron à la place (`crontab -e`) :

```cron
0 8 * * * /bin/bash /chemin/vers/MailBrief/scripts/run.sh
```

## L'app Mac (barre de menu)

```bash
./scripts/build_app.sh     # compile, installe et redémarre
open ~/Applications/MailBrief.app
```

- **0 % CPU à l'inactivité** — aucune boucle, aucun timer : elle ne se réveille
  qu'à l'ouverture du menu.
- ~55 Mo de RAM, pas d'icône Dock (`LSUIElement`).
- Affiche le prochain run, le dernier run, l'état d'Ollama.
- **Lancer maintenant**, **Aperçu sans envoyer**, **Diagnostic**.
- Démarre au login (`~/Library/LaunchAgents/com.mailbrief.app.plist`).

L'app **ne remplace pas launchd** : elle pilote le même `scripts/run.sh`. La
planification reste gérée par launchd, bien plus fiable au réveil.

Modes sans interface, utiles en test :

```bash
~/Applications/MailBrief.app/Contents/MacOS/MailBrief --status   # état
~/Applications/MailBrief.app/Contents/MacOS/MailBrief --probe    # géométrie de l'icône
~/Applications/MailBrief.app/Contents/MacOS/MailBrief --run --dry-run
```

### Alertes d'échec

Si le run planifié échoue (Ollama coupé, IMAP en panne), un message part sur
Telegram — sans quoi l'échec resterait silencieux dans `data/launchd.err.log`.
L'état n'est jamais validé en cas d'échec : aucun mail n'est perdu.

## Commandes

| Commande | Effet |
|---|---|
| `python -m mailbrief` | brief classique (lit, résume, envoie) |
| `python -m mailbrief --dry-run` | n'envoie rien, affiche le message |
| `python -m mailbrief --check` | diagnostic config + Ollama + Telegram |
| `python -m mailbrief --force` | re-traite les mails déjà vus |
| `python -m mailbrief --limit 10` | borne le nombre de mails |
| `python -m mailbrief --hours 48` | élargit la fenêtre de recherche |

## Comment ça marche

1. **Lecture** — `imap_client.py` ouvre IMAP, cherche les mails des N dernières
   heures, retire ceux déjà traités (UID + `data/state.json`).
2. **Nettoyage** — MIME décodé, HTML converti en texte, pièces jointes listées
   sans être embarquées, corps tronqué à `MAX_BODY_CHARS`.
3. **Lots** — si le prompt dépasse le contexte du modèle, les mails sont
   découpés en lots (`_make_batches`) : sans ça Ollama rend
   `token repeat limit reached`.
4. **Résumé** — `summarizer.py` interroge Ollama en sortie JSON stricte
   (2 tentatives, puis repli texte libre). Chaque mail devient
   `urgent` / `repondre` / `info` + un brouillon quand c'est pertinent.
   Un expéditeur `no-reply` est toujours `info`.
5. **Envoi** — `formatter.py` compose du HTML Telegram et découpe sous 4096
   caractères ; `telegram_client.py` gère les ratés et le throttling.
6. **Validation** — l'état n'est écrit **qu'après** envoi réussi.

```
mailbrief/
├── config.py          presets IMAP, chargement .env
├── state.py           UID déjà traités (dédoublonnage)
├── imap_client.py     lecture + décodage MIME
├── summarizer.py      lots, prompt, JSON, repli
├── formatter.py       rendu Telegram, dates FR, chunking
├── telegram_client.py envoi, retries, 429
└── __main__.py        CLI, orchestration, alertes
swift/MailBriefApp/    app barre de menu (Swift/AppKit)
tests/                 tests unitaires + intégration CLI
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

49 tests : rendu Telegram, découpage sous 4096, priorités, JSON, lots de
contexte, MIME/encodage, état, presets IMAP, et le CLI (un `--dry-run` ne
doit jamais consommer les mails, un échec doit alerter sans valider l'état).

## Dépannage

| Symptôme | Cause / fix |
|---|---|
| `IMAP_USER / IMAP_PASSWORD manquants` | `.env` absent ou incomplet |
| `IMAP_PRESET inconnu` | faute de frappe — voir le tableau plus haut |
| `Login error: authentication failed` | mot de passe normal au lieu d'un **mot de passe d'application** |
| `Ollama injoignable` | `ollama serve` — l'app MailBrief affiche « Ollama : ARRÊTÉ » |
| `token repeat limit reached` | trop de mails par lot — baisse `MAX_BODY_CHARS` ou `MAX_MAILS` |
| `Telegram 409: chat not found` | bot jamais contacté, ou `TELEGRAM_CHAT_ID` faux |
| `UIDVALIDITY a changé` | boîte reconstruite par le fournisseur — bénin |
| `Operation not permitted` dans `data/launchd.err.log` (job en exit `126`) | TCC de macOS bloque `~/Documents` aux jobs shell — relance `./install_schedule.sh 8 0`, qui planifie l'**app** plutôt que `/bin/bash` |
| Rien ne part | `tail -f data/launchd.err.log`, ou regarde l'alerte Telegram |
| Icône absente (macOS) | `launchctl kickstart -k gui/$(id -u)/com.mailbrief.app` |

## Confidentialité

- `.env` et `data/` sont **ignorés par git** — identifiants et historique de
  lectures restent sur ta machine.
- Aucune télémétrie, aucun appel réseau sauf : ton serveur IMAP, ton instance
  Ollama locale, et l'API Telegram pour t'envoyer le brief.
- Les corps de mails partent vers Ollama **en local** ; rien n'est envoyé à un
  tiers.

## Contribuer

Issues et PR les bienvenues. Premières contributions faciles : d'autres presets
IMAP, une traduction du brief, un packag Windows/Linux.

## Licence

[MIT](LICENSE).
