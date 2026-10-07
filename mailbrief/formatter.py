"""Composition du message Telegram (HTML) + découpage sous 4096 caractères."""

from __future__ import annotations

import html
from datetime import datetime
from typing import List

from .imap_client import Mail
from .summarizer import Report

TELEGRAM_MAX = 4096

PRIORITY_LABEL = {
    "urgent": "🔴 URGENT",
    "repondre": "🟡 À RÉPONDRE",
    "info": "🔵 INFO",
}
PRIORITY_ORDER = ("urgent", "repondre", "info")

WEEKDAYS_FR = (
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
)
MONTHS_FR = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


def fr_date(dt: datetime) -> str:
    """07 octobre 2026, indépendant de la locale du système."""
    return f"{dt.day} {MONTHS_FR[dt.month - 1]} {dt.year}"


def fr_datetime_short(dt: datetime) -> str:
    return f"{dt.day:02d}/{dt.month:02d} {dt.hour:02d}:{dt.minute:02d}"


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def split_chunks(text: str, limit: int = TELEGRAM_MAX) -> List[str]:
    """Découpe en messages < limit en coupant sur des frontières de paragraphes."""
    if len(text) <= limit:
        return [text] if text else []

    paragraphs = text.split("\n\n")
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        # Paragraphe seul trop long → coupe dur.
        if len(para) > limit:
            if current:
                chunks.append(current)
                current = ""
            while len(para) > limit:
                cut = para.rfind("\n", 0, limit)
                if cut <= 0:
                    cut = limit
                chunks.append(para[:cut])
                para = para[cut:].lstrip("\n")
            current = para
            continue
        if len(candidate) > limit:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _mail_block(mail: Mail, item, show_draft: bool) -> str:
    lines = [
        f"<b>{esc(mail.subject)}</b>",
        f"<i>{esc(mail.from_display)}</i>"
        + (f" · {fr_datetime_short(mail.date)}" if mail.date else ""),
        esc(item.resume),
    ]
    if mail.attachments:
        lines.append(f"📎 {esc(', '.join(mail.attachments))}")
    if show_draft and item.brouillon and item.priorite in ("urgent", "repondre"):
        lines.append("")
        lines.append("<b>✏️ Brouillon de réponse :</b>")
        lines.append(f"<blockquote>{esc(item.brouillon)}</blockquote>")
    return "\n".join(lines)


def render_report(
    report: Report,
    mails: List[Mail],
    when: datetime,
    with_drafts: bool = True,
) -> str:
    by_uid = {m.uid: m for m in mails}
    urgent = report.count("urgent")
    reply = report.count("repondre")
    info = report.count("info")

    header = [
        f"📬 <b>Brief du {WEEKDAYS_FR[when.weekday()].capitalize()} {fr_date(when)}</b>",
        f"<i>{esc(report.titre)}</i>",
        "",
        (
            f"🔴 {urgent} urgent · 🟡 {reply} à répondre · 🔵 {info} info"
            f" · {len(report.items)} au total"
        ),
    ]
    if report.synthese:
        header += ["", esc(report.synthese)]

    body: List[str] = []
    for priorite in PRIORITY_ORDER:
        items = [i for i in report.items if i.priorite == priorite]
        if not items:
            continue
        body += ["", f"<b>{PRIORITY_LABEL[priorite]} ({len(items)})</b>", ""]
        for item in items:
            mail = by_uid.get(item.uid)
            if mail is None:
                continue
            body.append(_mail_block(mail, item, with_drafts))
            body.append("")

    footer = ["—", f"<i>Source : mail · modèle {esc(report.model)} · {when.strftime('%H:%M')}</i>"]

    return "\n".join(header + body + footer).strip()


def render_empty(when: datetime) -> str:
    return (
        f"📬 <b>Brief du {WEEKDAYS_FR[when.weekday()].capitalize()} {fr_date(when)}</b>\n"
        "Aucun nouveau mail depuis la dernière analyse. 🎉"
    )
