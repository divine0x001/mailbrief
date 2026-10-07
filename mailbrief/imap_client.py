"""Lecture de la boîte mail via IMAP (iCloud ou n'importe quel fournisseur)."""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup

from .config import Config

_UID_RE = re.compile(rb"UID (\d+)")


@dataclass
class Mail:
    uid: int
    sender_addr: str
    sender_name: str
    subject: str
    date: Optional[datetime]
    body: str
    attachments: List[str] = field(default_factory=list)
    message_id: str = ""

    @property
    def from_display(self) -> str:
        if self.sender_name and self.sender_name != self.sender_addr:
            return f"{self.sender_name} <{self.sender_addr}>"
        return self.sender_addr or "(expéditeur inconnu)"


def decode_mime_header(value: str) -> str:
    """Décode un en-tête encodé (RFC 2047) : =?UTF-8?B?...?= → texte lisible."""
    if not value:
        return ""
    parts = []
    try:
        chunks = email.header.decode_header(value)
    except email.header.HeaderParseError:
        return value
    for chunk, charset in chunks:
        if isinstance(chunk, bytes):
            for candidate in (charset, "utf-8", "latin-1"):
                if not candidate:
                    continue
                try:
                    parts.append(chunk.decode(candidate, errors="replace"))
                    break
                except (LookupError, UnicodeDecodeError):
                    continue
            else:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _angle_addr(value: str) -> str:
    match = re.search(r"<([^>]+)>", value)
    return match.group(1).strip() if match else value.strip()


def html_to_text(html: str) -> str:
    """HTML → texte propre, en conservant les retours à la ligne utiles."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head", "meta", "title"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4"]):
        block.append("\n")
    text = soup.get_text("")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _iter_parts(msg: email.message.Message) -> Tuple[str, str, List[str]]:
    """Retourne (texte, html, noms de pièces jointes)."""
    plain_parts: List[str] = []
    html_parts: List[str] = []
    attachments: List[str] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if filename or disposition == "attachment":
            if filename:
                attachments.append(decode_mime_header(filename))
            continue

        content_type = part.get_content_type().lower()
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001 - un mail corrompu ne doit pas tout casser
            payload = None
        if payload is None:
            continue

        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")

        if content_type == "text/plain":
            plain_parts.append(text)
        elif content_type == "text/html":
            html_parts.append(text)

    if plain_parts:
        return "\n".join(plain_parts).strip(), "", attachments
    if html_parts:
        return "", "\n".join(html_parts).strip(), attachments
    return "", "", attachments


def parse_message(uid: int, raw: bytes, max_body_chars: int) -> Mail:
    msg = email.message_from_bytes(raw)
    plain, html, attachments = _iter_parts(msg)
    body = plain or (html_to_text(html) if html else "")

    date_raw = msg.get("Date")
    date: Optional[datetime] = None
    if date_raw:
        try:
            parsed = email.utils.parsedate_to_datetime(date_raw)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                date = parsed
        except (TypeError, ValueError):
            date = None

    if len(body) > max_body_chars:
        body = body[:max_body_chars] + "\n[… Mail tronqué …]"

    raw_from = decode_mime_header(msg.get("From", ""))
    parsed_name, parsed_addr = email.utils.parseaddr(msg.get("From", ""))
    sender_addr = parsed_addr or _angle_addr(raw_from)
    sender_name = (parsed_name or raw_from).strip()

    return Mail(
        uid=uid,
        sender_addr=sender_addr,
        sender_name=sender_name,
        subject=decode_mime_header(msg.get("Subject", "")) or "(sans objet)",
        date=date,
        body=body,
        attachments=attachments,
        message_id=msg.get("Message-ID", "").strip(),
    )


def _imap_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%d-%b-%Y")


class ImapClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.conn: Optional[imaplib.IMAP4_SSL] = None
        self.uidvalidity: int = 0

    def __enter__(self) -> "ImapClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        cfg = self.cfg
        conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port, timeout=30)
        conn.login(cfg.imap_user, cfg.imap_password)
        status, _ = conn.select(cfg.imap_folder, readonly=True)
        if status != "OK":
            conn.logout()
            raise SystemExit(
                f"[imap] Impossible d'ouvrir le dossier {cfg.imap_folder!r} "
                f"sur {cfg.imap_host}"
            )
        raw = conn.response("UIDVALIDITY")
        try:
            self.uidvalidity = int(raw[1][0]) if raw and raw[1] else 0
        except (TypeError, ValueError, IndexError):
            self.uidvalidity = 0
        self.conn = conn

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.conn.logout()
        except Exception:  # noqa: BLE001
            pass
        self.conn = None

    def search_recent(self, since_hours: int) -> List[int]:
        """UIDs des mails reçus sur la fenêtre glissante, triés croissants."""
        assert self.conn is not None
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        typ, data = self.conn.uid("search", None, "SINCE", _imap_date(since))
        if typ != "OK" or not data or not data[0]:
            return []
        uids = [int(u) for u in data[0].split() if u.strip()]
        return sorted(uids)

    def fetch(self, uid: int) -> Optional[Mail]:
        assert self.conn is not None
        try:
            typ, data = self.conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        except imaplib.IMAP4.error:
            return None
        if typ != "OK" or not data:
            return None
        raw = next(
            (chunk for chunk in data if isinstance(chunk, tuple) and len(chunk) > 1),
            None,
        )
        if raw is None:
            # Certains serveurs renvoient le corps seul, sans métadonnées.
            raw = next((chunk for chunk in data if isinstance(chunk, bytes)), None)
            if raw is None:
                return None
            payload = raw
        else:
            payload = raw[1]
            m = _UID_RE.search(raw[0] if isinstance(raw[0], bytes) else b"")
            if m:
                uid = int(m.group(1))
        if not isinstance(payload, (bytes, bytearray)):
            return None
        return parse_message(uid, bytes(payload), self.cfg.max_body_chars)


def fetch_new_mails(
    cfg: Config,
    exclude_uids: set,
    uidvalidity: int,
    since_hours: int,
    limit: int,
    log=print,
) -> Tuple[List[Mail], int]:
    """Récupère les nouveaux mails, hors ceux déjà traités."""
    with ImapClient(cfg) as client:
        if client.uidvalidity and uidvalidity and client.uidvalidity != uidvalidity:
            log(
                f"[imap] UIDVALIDITY a changé ({uidvalidity} → "
                f"{client.uidvalidity}) : le dossier a été reconstruit."
            )
        uids = client.search_recent(since_hours)
        fresh = [u for u in uids if u not in exclude_uids]
        fresh.sort(reverse=True)  # les plus récents d'abord
        fresh = fresh[:limit]
        fresh.sort()  # lecture dans l'ordre chronologique

        mails: List[Mail] = []
        for uid in fresh:
            mail = client.fetch(uid)
            if mail is None:
                continue
            if not mail.body and not mail.subject:
                continue
            mails.append(mail)
        return mails, client.uidvalidity


def now_local(tz_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001 - fuseau indisponible → heure locale
        return datetime.now()
