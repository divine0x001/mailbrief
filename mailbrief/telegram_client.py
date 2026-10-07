"""Envoi de messages via l'API Bot Telegram."""

from __future__ import annotations

import time
from typing import List

import requests

from .formatter import split_chunks

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


def _send_once(token: str, chat_id: str, text: str) -> None:
    resp = requests.post(
        f"{API_BASE}/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code == 429:
        retry_after = (resp.json().get("parameters") or {}).get("retry_after", 3)
        time.sleep(min(int(retry_after) + 1, 30))
        resp = requests.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
    if resp.status_code != 200:
        try:
            desc = resp.json().get("description", resp.text[:200])
        except ValueError:
            desc = resp.text[:200]
        raise TelegramError(f"Telegram {resp.status_code} : {desc}")


def send_message(token: str, chat_id: str, text: str, log=print) -> List[str]:
    """Découpe et envoie. Lève TelegramError en cas d'échec."""
    chunks = split_chunks(text)
    for i, chunk in enumerate(chunks, 1):
        last_error: Exception | None = None
        for attempt in (1, 2, 3):
            try:
                _send_once(token, chat_id, chunk)
                last_error = None
                break
            except TelegramError as exc:
                last_error = exc
                if "chat not found" in str(exc).lower():
                    raise
                time.sleep(2 * attempt)
        if last_error is not None:
            raise TelegramError(str(last_error))
        if len(chunks) > 1:
            log(f"[telegram] partie {i}/{len(chunks)} envoyée")
        time.sleep(0.5)
    return chunks


def check_token(token: str) -> str:
    """Valide le token et renvoie le nom du bot."""
    resp = requests.get(f"{API_BASE}/bot{token}/getMe", timeout=15)
    if resp.status_code != 200:
        raise TelegramError(f"Token invalide ({resp.status_code}).")
    return resp.json().get("result", {}).get("username", "?")
