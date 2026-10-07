"""Chargement et validation de la configuration (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Presets IMAP : (hôte, port). `IMAP_HOST` explicite reste prioritaire.
IMAP_PRESETS = {
    "icloud": ("imap.mail.me.com", 993),
    "gmail": ("imap.gmail.com", 993),
    "outlook": ("outlook.office365.com", 993),
    "yahoo": ("imap.mail.yahoo.com", 993),
    "free": ("imap.free.fr", 993),
    "orange": ("ssl0.orange.net", 993),
    "sfr": ("imap.sfr.fr", 993),
    "laposte": ("imap.laposte.net", 993),
    # Proton / Tutanota passent par leur client local (Bridge).
    "proton": ("127.0.0.1", 1143),
    "custom": ("", 993),
}


@dataclass(frozen=True)
class Config:
    # IMAP
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_folder: str
    # Ollama
    ollama_url: str
    ollama_model: str
    # Telegram
    telegram_token: str
    telegram_chat_id: str
    # Comportement
    lookback_hours: int
    max_mails: int
    max_body_chars: int
    state_path: Path
    tz: str

    @property
    def imap_configured(self) -> bool:
        return bool(self.imap_user and self.imap_password)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"[config] {name} doit être un entier, reçu : {raw!r}")


def load_config(env_file: str | None = None) -> Config:
    """Charge .env (racine du projet) puis les variables d'environnement."""
    load_dotenv(env_file or ROOT / ".env")

    preset_name = os.getenv("IMAP_PRESET", "icloud").strip().lower() or "icloud"
    if preset_name not in IMAP_PRESETS:
        raise SystemExit(
            f"[config] IMAP_PRESET inconnu : {preset_name!r}\n"
            f"  Choix possibles : {', '.join(sorted(IMAP_PRESETS))}"
        )

    # `custom` est le seul cas où l'hôte se renseigne à la main.
    if preset_name == "custom":
        host = os.getenv("IMAP_HOST", "").strip()
        if not host:
            raise SystemExit(
                "[config] IMAP_PRESET=custom exige IMAP_HOST "
                "(et IMAP_PORT si différent de 993)."
            )
        port = _int("IMAP_PORT", 993)
    else:
        # Le preset fait autorité : sinon un IMAP_HOST résiduel dans un ancien
        # .env neutraliserait silencieusement le fournisseur choisi.
        host, port = IMAP_PRESETS[preset_name]

    state_raw = os.getenv("STATE_PATH", "data/state.json").strip()
    state_path = Path(state_raw)
    if not state_path.is_absolute():
        state_path = ROOT / state_path

    return Config(
        imap_host=host,
        imap_port=port,
        imap_user=os.getenv("IMAP_USER", "").strip(),
        imap_password=os.getenv("IMAP_PASSWORD", "").strip(),
        imap_folder=os.getenv("IMAP_FOLDER", "INBOX").strip() or "INBOX",
        ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:8b").strip(),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        lookback_hours=_int("LOOKBACK_HOURS", 26),
        max_mails=max(1, _int("MAX_MAILS", 40)),
        max_body_chars=max(500, _int("MAX_BODY_CHARS", 1500)),
        state_path=state_path,
        tz=os.getenv("TZ", "Europe/Paris").strip() or "Europe/Paris",
    )


def require_imap(cfg: Config) -> None:
    if not cfg.imap_configured:
        raise SystemExit(
            "[config] IMAP_USER / IMAP_PASSWORD manquants.\n"
            "Copie .env.example → .env et renseigne tes identifiants iCloud.\n"
            "Mot de passe d'application : https://account.apple.com → Sécurité."
        )
