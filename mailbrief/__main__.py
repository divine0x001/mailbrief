"""Point d'entrée : `python -m mailbrief [options]`."""

from __future__ import annotations

import argparse
import html
import sys
import time
import traceback

from . import __version__
from .config import load_config, require_imap
from .formatter import render_empty, render_report, split_chunks
from .imap_client import ImapClient, fetch_new_mails, now_local
from .state import State
from .summarizer import SummarizerError, check_ollama, summarize
from .telegram_client import TelegramError, check_token, send_message


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mailbrief",
        description="Résumé quotidien de tes mails, envoyé sur Telegram.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Ne rien envoyer sur Telegram, affiche le résultat.")
    p.add_argument("--limit", type=int, default=None,
                   help="Nombre max de mails traités (défaut : MAX_MAILS).")
    p.add_argument("--hours", type=int, default=None,
                   help="Fenêtre de recherche en heures (défaut : LOOKBACK_HOURS).")
    p.add_argument("--force", action="store_true",
                   help="Ignore l'état et re-traite les mails déjà vus.")
    p.add_argument("--check", action="store_true",
                   help="Vérifie la config, Ollama et Telegram, puis sort.")
    p.add_argument("--version", action="version", version=f"mailbrief {__version__}")
    return p


def notify_failure(cfg, code: int, detail: str) -> None:
    """Alerte best-effort sur Telegram : un échec planifié ne doit pas
    passer inaperçu dans un log que personne ne lit."""
    if not cfg.telegram_configured:
        return
    text = (
        "⚠️ <b>MailBrief — le brief n'a pas pu être généré</b>\n"
        f"Code d'erreur : <b>{code}</b>\n\n"
        f"<blockquote>{html.escape(detail.strip()[:600])}</blockquote>\n\n"
        "<i>Aucun mail n'a été marqué : le prochain essai les reprendra.</i>"
    )
    try:
        send_message(cfg.telegram_token, cfg.telegram_chat_id, text)
    except Exception:  # noqa: BLE001 - best-effort, on n'enrichit pas l'échec
        pass


def run_check(cfg) -> int:
    ok = True
    print("== MailBrief — diagnostic ==")
    print(f"IMAP       : {cfg.imap_host}:{cfg.imap_port} "
          f"{'✅ ' + cfg.imap_user if cfg.imap_configured else '❌ IMAP_USER/PASSWORD manquants'}")
    if cfg.imap_configured:
        try:
            with ImapClient(cfg):
                print("           ✅ connexion + dossier ouverts")
        except SystemExit as exc:
            print(f"           ❌ {exc}")
            ok = False
        except Exception as exc:  # noqa: BLE001
            print(f"           ❌ {exc}")
            ok = False

    if check_ollama(cfg):
        print(f"Ollama     : ✅ {cfg.ollama_url} · modèle {cfg.ollama_model}")
    else:
        print(f"Ollama     : ❌ {cfg.ollama_url} inaccessible ou modèle "
              f"{cfg.ollama_model} absent (`ollama list`)")
        ok = False

    if cfg.telegram_configured:
        try:
            bot = check_token(cfg.telegram_token)
            print(f"Telegram   : ✅ bot @{bot} · chat {cfg.telegram_chat_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"Telegram   : ❌ {exc}")
            ok = False
    else:
        print("Telegram   : ❌ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants")
        ok = False

    print("État       :", cfg.state_path)
    print("Diagnostic :", "✅ prêt" if ok else "❌ à corriger")
    return 0 if ok else 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()

    if args.check:
        return run_check(cfg)

    require_imap(cfg)
    now = now_local(cfg.tz)
    state = State.load(cfg.state_path)

    if args.force:
        state.processed = []
        state.last_uid = 0

    limit = args.limit or cfg.max_mails
    hours = args.hours or cfg.lookback_hours

    # 1. Lecture de la boîte mail ------------------------------------------
    try:
        mails, uidvalidity = fetch_new_mails(
            cfg,
            exclude_uids=set(state.processed),
            uidvalidity=state.uidvalidity,
            since_hours=hours,
            limit=limit,
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[imap] échec : {exc}", file=sys.stderr)
        traceback.print_exc()
        notify_failure(cfg, 2, f"Lecture IMAP impossible : {exc}")
        return 2

    print(f"[imap] {len(mails)} nouveau(x) mail(s) à analyser")
    state.uidvalidity = uidvalidity or state.uidvalidity

    # 2. Rien de nouveau → petit message, on s'arrête ----------------------
    if not mails:
        message = render_empty(now)
        if args.dry_run:
            print(f"[dry-run] {message}")
            print("[dry-run] état inchangé.")
            return 0
        if cfg.telegram_configured:
            try:
                send_message(cfg.telegram_token, cfg.telegram_chat_id, message)
            except TelegramError as exc:
                print(f"[telegram] {exc}", file=sys.stderr)
                return 3
        state.runs += 1
        state.save()
        return 0

    # 3. Résumé IA local ---------------------------------------------------
    if not check_ollama(cfg):
        detail = (
            f"{cfg.ollama_url} inaccessible ou modèle {cfg.ollama_model} "
            "introuvable. Lance `ollama serve` puis `ollama pull qwen3:8b`."
        )
        print(f"[ollama] {detail}", file=sys.stderr)
        notify_failure(cfg, 4, detail)
        return 4

    try:
        report = summarize(cfg, mails)
    except SummarizerError as exc:
        print(f"[ollama] {exc}", file=sys.stderr)
        notify_failure(cfg, 4, str(exc))
        return 4

    body = render_report(report, mails, now)
    chunks = split_chunks(body)

    # 4. Envoi Telegram ----------------------------------------------------
    if args.dry_run:
        print(f"[dry-run] {len(chunks)} message(s) prêt(s) :\n")
        print(body)
    elif cfg.telegram_configured:
        try:
            send_message(cfg.telegram_token, cfg.telegram_chat_id, body)
        except TelegramError as exc:
            print(f"[telegram] {exc}", file=sys.stderr)
            print("L'état n'est pas validé : le prochain run re-mettra ces mails.",
                  file=sys.stderr)
            return 3
        print(f"[telegram] brief envoyé ({len(chunks)} message(s))")
    else:
        print("[config] TELEGRAM non configuré — brief affiché en console.\n")
        print(body)

    # 5. On valide seulement après un vrai envoi réussi -----------------
    if args.dry_run:
        print("[dry-run] état inchangé : ces mails seront traités au prochain run réel.")
        return 0

    state.mark([m.uid for m in mails])
    state.runs += 1
    state.last_run_ts = time.time()
    state.save()
    print(f"[état] sauvegardé → {cfg.state_path} (run n°{state.runs})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
