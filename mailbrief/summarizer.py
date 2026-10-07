"""Résumé des mails par un modèle local Ollama (sortie JSON structurée)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

import requests

from .config import Config
from .imap_client import Mail

SYSTEM_PROMPT = """Tu es l'assistant personnel de l'utilisateur. Tu analyses des \
emails reçus et tu produis un brief clair en FRANÇAIS.

Règles :
- Sois concis : 1 à 3 phrases par mail, jamais plus.
- Classe chaque mail dans UNE priorité :
  "urgent"  = une action humaine est attendue VRAIMENT et bientôt : facture à
              payer, échéance contractuelle, demande pressante d'un humain,
              incident ou sécurité avérée.
              INTERDIT pour une promotion, une newsletter, une notification
              automatique ou un email marketing — même "dernier jour".
  "repondre"= un humain attend une réponse de l'utilisateur : une question,
              un rendez-vous, une validation, un échange en cours.
              INTERDIT pour un no-reply, une newsletter ou un message automatique.
  "info"    = tout le reste : promos, newsletters, notifications, communiqués,
              alertes de compte. Une offre urgente pour l'expéditeur reste "info"
              : l'urgence n'est pas celle de l'utilisateur.
- Si aucun humain n'est identifiable derrière l'expéditeur (no-reply, noreply,
  notifications automatiques), c'est TOUJOURS "info".
- Écris un brouillon de réponse UNIQUEMENT si la priorité est "urgent" ou \
"repondre" ET que l'expéditeur attend une réponse humaine. Il est INTERDIT de \
répondre à une newsletter, une promotion, un email automatique ou un no-reply : \
mets alors "brouillon": "".
- Un brouillon ne doit contenir QUE des informations présentes dans le mail. \
N'invente JAMAIS de date, d'heure, de montant, de nom ou d'engagement : si une \
info manque, écris [à confirmer] à la place.
- Ignore les signatures, les pieds de page et les pubs parasites.
- Ne fabrique jamais de contenu absent du mail.

Réponds UNIQUEMENT par un objet JSON valide, sans texte autour, avec cette forme :
{
  "titre": "résumé global en 8 mots max",
  "synthese": "2 à 4 phrases donnant le fil du jour",
  "mails": [
    {
      "uid": 123,
      "priorite": "urgent" | "repondre" | "info",
      "resume": "1 à 3 phrases",
      "brouillon": "texte de réponse prêt à envoyer ou chaîne vide"
    }
  ]
}
"""

BRIEF_INSTRUCTION = (
    "Voici {count} mail(s) reçu(s). Analyse-les et réponds en JSON.\n"
    "Le champ \"uid\" doit être repris EXACTEMENT tel qu'il est fourni.\n\n"
    "{mails}"
)

MAIL_BLOCK = """--- MAIL uid={uid} ---
De : {sender}
Objet : {subject}
Date : {date}
Pièces jointes : {attachments}
Contenu :
{body}
"""

PRIORITY_ORDER = {"urgent": 0, "repondre": 1, "info": 2}


@dataclass
class MailBrief:
    uid: int
    priorite: str  # urgent | repondre | info
    resume: str
    brouillon: str = ""


@dataclass
class Report:
    titre: str
    synthese: str
    items: List[MailBrief] = field(default_factory=list)
    model: str = ""
    fallback: bool = False

    def count(self, priorite: str) -> int:
        return sum(1 for i in self.items if i.priorite == priorite)


class SummarizerError(RuntimeError):
    pass


MAX_PROMPT_CHARS = 24000  # ~7k tokens : laisse la place à la sortie dans un contexte de 16k


def _block_for(m: Mail) -> str:
    return MAIL_BLOCK.format(
        uid=m.uid,
        sender=m.from_display,
        subject=m.subject,
        date=m.date.strftime("%Y-%m-%d %H:%M") if m.date else "inconnue",
        attachments=", ".join(m.attachments) if m.attachments else "aucune",
        body=m.body or "(corps vide)",
    )


def _build_user_prompt(mails: List[Mail]) -> str:
    return BRIEF_INSTRUCTION.format(
        count=len(mails), mails="\n".join(_block_for(m) for m in mails)
    )


def _make_batches(mails: List[Mail], max_chars: int = MAX_PROMPT_CHARS) -> List[List[Mail]]:
    """Découpe en lots qui tiennent dans le contexte du modèle.

    Sans ça, un résumé quotidien de 20-40 mails déborde le contexte : Ollama
    renvoie `token repeat limit reached` et le JSON s'effondre.
    """
    batches: List[List[Mail]] = []
    current: List[Mail] = []
    size = 0
    for mail in mails:
        cost = len(_block_for(mail))
        if current and size + cost > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(mail)
        size += cost
    if current:
        batches.append(current)
    return batches


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Retire un éventuel bloc ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("aucun objet JSON détecté")
    return json.loads(text[start : end + 1])


def _normalize(payload: dict, mails: List[Mail]) -> Report:
    by_uid = {m.uid: m for m in mails}
    items: List[MailBrief] = []
    for entry in payload.get("mails", []) or []:
        if not isinstance(entry, dict):
            continue
        try:
            uid = int(entry.get("uid"))
        except (TypeError, ValueError):
            continue
        priorite = str(entry.get("priorite", "info")).strip().lower()
        if priorite not in PRIORITY_ORDER:
            if priorite in ("haute", "high", "important", "répondre", "repondre"):
                priorite = "urgent" if priorite in ("haute", "high", "important") else "repondre"
            else:
                priorite = "info"
        resume = str(entry.get("resume", "")).strip()
        brouillon = str(entry.get("brouillon", "") or "").strip()
        if not resume:
            continue
        if priorite == "info":
            brouillon = ""
        items.append(
            MailBrief(uid=uid, priorite=priorite, resume=resume, brouillon=brouillon)
        )

    # Conserve uniquement les UID réellement fournis, et garde l'ordre d'arrivée.
    valid_uids = set(by_uid)
    items = [i for i in items if i.uid in valid_uids]
    items.sort(key=lambda i: (PRIORITY_ORDER[i.priorite], -i.uid))

    titre = str(payload.get("titre", "")).strip() or "Brief du jour"
    synthese = str(payload.get("synthese", "")).strip()

    # Modèle a omis un mail : on l'ajoute en "info" pour ne rien perdre.
    seen = {i.uid for i in items}
    for m in mails:
        if m.uid not in seen:
            items.append(
                MailBrief(
                    uid=m.uid,
                    priorite="info",
                    resume=f"{m.subject} — {m.from_display}",
                )
            )
        items.sort(key=lambda i: (PRIORITY_ORDER[i.priorite], -i.uid))

    return Report(titre=titre, synthese=synthese, items=items)


def _call_ollama(cfg: Config, system: str, user: str, as_json: bool) -> str:
    body = {
        "model": cfg.ollama_model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {
            "temperature": 0.2,
            "num_ctx": 16384,
            "num_predict": 4096,
            "repeat_penalty": 1.08,
        },
    }
    if as_json:
        body["format"] = "json"
    try:
        resp = requests.post(
            f"{cfg.ollama_url}/api/chat", json=body, timeout=600
        )
    except requests.RequestException as exc:
        raise SummarizerError(
            f"Ollama injoignable sur {cfg.ollama_url} : {exc}\n"
            "Lance `ollama serve` puis réessaie."
        ) from exc
    if resp.status_code != 200:
        raise SummarizerError(f"Ollama a répondu {resp.status_code} : {resp.text[:400]}")
    data = resp.json()
    content = (data.get("message") or {}).get("content", "")
    if not content:
        raise SummarizerError("Ollama a renvoyé une réponse vide.")
    return content


def check_ollama(cfg: Config) -> bool:
    try:
        resp = requests.get(f"{cfg.ollama_url}/api/tags", timeout=5)
        resp.raise_for_status()
        return any(
            m.get("name", "").split(":")[0] == cfg.ollama_model.split(":")[0]
            for m in resp.json().get("models", [])
        )
    except (requests.RequestException, ValueError, KeyError):
        return False


def _json_report(cfg: Config, mails: List[Mail], log) -> Optional[Report]:
    """2 tentatives en JSON stricte, None si le modèle n'a pas coopéré."""
    user_prompt = _build_user_prompt(mails)
    for attempt in (1, 2):
        try:
            raw = _call_ollama(cfg, SYSTEM_PROMPT, user_prompt, as_json=True)
            payload = _extract_json(raw)
            report = _normalize(payload, mails)
            report.model = cfg.ollama_model
            if report.items:
                return report
            log(f"[ollama] JSON valide mais vide (tentative {attempt}).")
        except (ValueError, json.JSONDecodeError, SummarizerError) as exc:
            log(f"[ollama] tentative {attempt} échouée : {exc}")
    return None


def _fallback_report(cfg: Config, mails: List[Mail], log) -> Report:
    """Sortie texte libre : une entrée par mail, priorité lue dans les crochets."""
    log("[ollama] bascule sur le mode résumé libre (sans JSON).")
    fallback_system = (
        "Tu résumes des emails en français, 2 phrases maximum par mail, "
        "en commençant par la priorité entre crochets : [URGENT], [À RÉPONDRE] "
        "ou [INFO]. Chaque bloc commence par 'uid=NUMERO'. "
        "Pas de commentaire, pas de markdown."
    )
    try:
        raw = _call_ollama(cfg, fallback_system, _build_user_prompt(mails), as_json=False)
    except SummarizerError as exc:
        raise SummarizerError(str(exc)) from exc

    items = []
    for m in mails:
        chunk = _find_uid_chunk(raw, m.uid)
        text = (chunk or f"{m.subject} — {m.from_display}").strip()
        low = text.lower()
        if "[urgent]" in low:
            prio = "urgent"
        elif "[à répondre]" in low or "[a repondre]" in low:
            prio = "repondre"
        else:
            prio = "info"
        items.append(MailBrief(uid=m.uid, priorite=prio, resume=text))
    items.sort(key=lambda i: (PRIORITY_ORDER[i.priorite], -i.uid))
    return Report(
        titre="Brief du jour",
        synthese=f"{len(mails)} mail(s) analysé(s) en mode dégradé (JSON indisponible).",
        items=items,
        model=cfg.ollama_model,
        fallback=True,
    )


def summarize(cfg: Config, mails: List[Mail], log=print) -> Report:
    """Produit le brief, en découpant par lots si ça dépasse le contexte."""
    if not mails:
        return Report(titre="Rien à signaler", synthese="Aucun nouveau mail.")

    batches = _make_batches(mails)
    if len(batches) > 1:
        log(
            f"[ollama] {len(mails)} mails trop volumineux pour un seul prompt "
            f"→ {len(batches)} lots"
        )

    titres: List[str] = []
    syntheses: List[str] = []
    items: List[MailBrief] = []
    any_fallback = False

    for index, batch in enumerate(batches, 1):
        if len(batches) > 1:
            log(f"[ollama] lot {index}/{len(batches)} ({len(batch)} mails)")
        report = _json_report(cfg, batch, log)
        if report is None:
            report = _fallback_report(cfg, batch, log)
        any_fallback = any_fallback or report.fallback
        if report.titre and report.titre != "Brief du jour":
            titres.append(report.titre)
        if report.synthese:
            syntheses.append(report.synthese)
        items.extend(report.items)

    # Un UID ne peut pas revenir de deux lots, mais on se protège quand même.
    seen = set()
    unique: List[MailBrief] = []
    for item in items:
        if item.uid in seen:
            continue
        seen.add(item.uid)
        unique.append(item)
    unique.sort(key=lambda i: (PRIORITY_ORDER[i.priorite], -i.uid))

    titre = " — ".join(titres)[:90] if titres else "Brief du jour"
    synthese = " ".join(syntheses)[:700]

    return Report(titre=titre, synthese=synthese, items=unique,
                  model=cfg.ollama_model, fallback=any_fallback)


def _find_uid_chunk(text: str, uid: int) -> Optional[str]:
    match = re.search(
        rf"(?:uid\s*=?\s*{uid}\b|mail\s*{uid}\b)(.*?)(?=(?:uid\s*=?\s*\d+\b)|(?:mail\s+\d+\b)|\Z)",
        text,
        flags=re.S | re.I,
    )
    if match:
        return match.group(1).strip(" \n-:•")
    return None
