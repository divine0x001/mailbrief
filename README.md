# MailBrief 📬

**A self-hosted n8n for your inbox.** Every day, a local LLM reads your email,
summarizes it by priority, drafts the replies, and sends the whole thing to
Telegram.

- **Fully local** — Ollama runs on your machine; your mail never leaves it.
- **Free** — no subscription, no account, no third-party cloud.
- **Your real mailbox** — direct IMAP access, no duplicate account.
- **Lightweight** — a launchd timer fires at the scheduled time, plus a menu bar
  icon that costs 0 % CPU at rest.

```
Your IMAP provider ──► local Ollama summary ──► Telegram
        ▲                    (local)               │
        └──────── UID deduplication ◄──────────────┘
```

> **[Lire en français 🇫🇷](README.fr.md)**

---

## Quick start

```bash
git clone <this-repo> && cd MailBrief
./install.sh            # venv + deps + .env + tests + macOS app
```

Then fill in `.env` (below) and verify:

```bash
./install.sh --finish    # runs the full diagnostic
```

Manual install, if you prefer:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## Configuring `.env`

### 1. Your mailbox

Pick your provider with `IMAP_PRESET` — host and port are preconfigured:

| `IMAP_PRESET` | Provider | Notes |
|---|---|---|
| `icloud` | iCloud Mail | **app-specific password** required |
| `gmail` | Gmail | **app password** (2FA enabled) |
| `outlook` | Outlook / Office 365 | app password |
| `yahoo` | Yahoo Mail | app password |
| `free` | Free (FR) | account password |
| `orange` | Orange (FR) | account password |
| `sfr` | SFR (FR) | account password |
| `laposte` | La Poste / Mailoo (FR) | account password |
| `proton` | Proton Mail | via **Proton Bridge** (port 1143) |
| `custom` | anything else | set `IMAP_HOST` / `IMAP_PORT` |

```env
IMAP_PRESET=icloud
IMAP_USER=you@example.com
IMAP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

> **App-specific password** — iCloud and Gmail reject your normal password over
> IMAP. Generate a dedicated one:
> iCloud → <https://account.apple.com> · Security · App Password.
> Gmail → Google Account · Security · App passwords.

> **Proton** — install Proton Bridge, start it, and use the display password it
> shows (host `127.0.0.1:1143` is already the preset).

### 2. The local LLM

Install [Ollama](https://ollama.com), then:

```bash
ollama pull qwen3:8b
```

Everything runs on your machine. Smaller (`qwen3:4b`) and larger (`qwen3:27b`)
models work too — change `OLLAMA_MODEL`.

### 3. Telegram

1. Talk to **@BotFather** → `/newbot` → copy `TELEGRAM_BOT_TOKEN`.
2. Send your bot a message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates`:
   `"chat":{"id": 123456}` is your `TELEGRAM_CHAT_ID` (positive in DMs,
   negative `−100…` for a channel — the bot must be an admin there).

## Verify

```bash
.venv/bin/python -m mailbrief --check
```

Three ✅ expected. Then a dry run (sends nothing, consumes nothing):

```bash
.venv/bin/python -m mailbrief --dry-run
```

## Schedule the daily brief

```bash
./install_schedule.sh 8 0      # every day at 08:00
```

Installs a **launchd** service (`~/Library/LaunchAgents/com.mailbrief.daily.plist`).
Unlike cron, it catches up if the machine was asleep at the scheduled time.

```bash
./install_schedule.sh 19 30                          # change the time
launchctl bootout gui/$(id -u)/com.mailbrief.daily   # uninstall
tail -f data/launchd.log                             # watch the logs
```

With cron instead (`crontab -e`):

```cron
0 8 * * * /bin/bash /path/to/MailBrief/scripts/run.sh
```

## The Mac app (menu bar)

```bash
./scripts/build_app.sh     # compiles, installs, restarts
open ~/Applications/MailBrief.app
```

- **0 % CPU at rest** — no loop, no timer; it only wakes when you open the menu.
- ~55 MB RAM, no Dock icon (`LSUIElement`).
- Shows the next run, the last run, and whether Ollama is up.
- **Run now**, **Preview without sending**, **Diagnostic**.
- Starts at login (`~/Library/LaunchAgents/com.mailbrief.app.plist`).

The app **does not replace launchd**: it drives the same `scripts/run.sh`.
Scheduling stays with launchd, which is far more reliable on wake.

Headless modes, handy for testing:

```bash
~/Applications/MailBrief.app/Contents/MacOS/MailBrief --status   # state
~/Applications/MailBrief.app/Contents/MacOS/MailBrief --probe    # icon geometry
~/Applications/MailBrief.app/Contents/MacOS/MailBrief --run --dry-run
```

### Failure alerts

If the scheduled run fails (Ollama down, IMAP broken), a message is sent to
Telegram — otherwise the failure would sit silently in `data/launchd.err.log`.
State is never committed on failure, so no mail is lost.

## Commands

| Command | Effect |
|---|---|
| `python -m mailbrief` | classic brief (read, summarize, send) |
| `python -m mailbrief --dry-run` | send nothing, print the message |
| `python -m mailbrief --check` | diagnostic: config + Ollama + Telegram |
| `python -m mailbrief --force` | reprocess already-seen mails |
| `python -m mailbrief --limit 10` | cap the number of mails |
| `python -m mailbrief --hours 48` | widen the search window |

## How it works

1. **Read** — `imap_client.py` opens IMAP, searches the last N hours, drops
   already-processed UIDs (tracked in `data/state.json`).
2. **Clean** — MIME decoded, HTML converted to text, attachments listed but not
   embedded, body truncated to `MAX_BODY_CHARS`.
3. **Batch** — if the prompt would overflow the model's context, mails are split
   into batches (`_make_batches`); without this Ollama returns
   `token repeat limit reached`.
4. **Summarize** — `summarizer.py` calls Ollama with strict JSON output
   (2 attempts, then a plain-text fallback). Each mail becomes
   `urgent` / `repondre` / `info` plus a draft when relevant. Any `no-reply`
   sender is always `info`.
5. **Send** — `formatter.py` builds Telegram HTML and splits under 4096
   characters; `telegram_client.py` handles retries and rate limits.
6. **Commit** — state is written **only after** a successful send.

```
mailbrief/
├── config.py          IMAP presets, .env loading
├── state.py           processed UIDs (deduplication)
├── imap_client.py     reading + MIME decoding
├── summarizer.py      batching, prompt, JSON, fallback
├── formatter.py       Telegram rendering, FR dates, chunking
├── telegram_client.py sending, retries, 429 handling
└── __main__.py        CLI, orchestration, alerts
swift/MailBriefApp/    menu bar app (Swift/AppKit)
tests/                 unit + CLI integration tests
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

49 tests: Telegram rendering, 4096-char splitting, priorities, JSON, context
batching, MIME/encoding, state, IMAP presets, and the CLI (a `--dry-run` must
never consume mails; a failure must alert without committing state).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `IMAP_USER / IMAP_PASSWORD missing` | `.env` missing or incomplete |
| `unknown IMAP_PRESET` | typo — see the table above |
| `Login error: authentication failed` | normal password instead of an **app password** |
| `Ollama unreachable` | `ollama serve` — the MailBrief app shows "Ollama: OFF" |
| `token repeat limit reached` | too many mails per batch — lower `MAX_BODY_CHARS` or `MAX_MAILS` |
| `Telegram 409: chat not found` | bot never contacted, or wrong `TELEGRAM_CHAT_ID` |
| `UIDVALIDITY changed` | mailbox rebuilt by the provider — harmless |
| Nothing arrives | `tail -f data/launchd.err.log`, or check the Telegram alert |
| Missing icon (macOS) | `launchctl kickstart -k gui/$(id -u)/com.mailbrief.app` |

## Privacy

- `.env` and `data/` are **git-ignored** — credentials and read history stay on
  your machine.
- No telemetry. The only network calls are your IMAP server, your local Ollama
  instance, and the Telegram API to deliver your brief.
- Mail bodies go to **local** Ollama; nothing is sent to a third party.

## Contributing

Issues and PRs welcome. Good first contributions: more IMAP presets, a
translation of the brief, Windows/Linux packaging.

## License

[MIT](LICENSE).
