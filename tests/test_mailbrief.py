"""Tests du formatage Telegram et de l'extraction de texte."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from mailbrief.formatter import esc, render_report, split_chunks
from mailbrief.imap_client import Mail, html_to_text, parse_message
from mailbrief.state import State
from mailbrief.summarizer import Report, _extract_json, _normalize


def make_mail(uid: int, subject: str = "Sujet", sender: str = "a@b.fr",
              body: str = "Corps du mail.") -> Mail:
    return Mail(
        uid=uid,
        sender_addr=sender,
        sender_name="Alice",
        subject=subject,
        date=datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc),
        body=body,
    )


class TestEscaping(unittest.TestCase):
    def test_escapes_html(self):
        self.assertEqual(esc("<b> & co"), "&lt;b&gt; &amp; co")

    def test_escapes_quotes_kept_readable(self):
        self.assertIn('"', esc('il a dit "bonjour"'))


class TestSplitChunks(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(split_chunks("coucou"), ["coucou"])

    def test_empty(self):
        self.assertEqual(split_chunks(""), [])

    def test_long_text_respects_limit(self):
        text = "\n\n".join(f"Paragraphe {i} " + "x" * 300 for i in range(60))
        chunks = split_chunks(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 4096)
        # Rien ne se perd entre les morceaux
        joined = "".join(c.replace("\n", "") for c in chunks)
        self.assertEqual(len(joined), len(text.replace("\n", "")))

    def test_single_huge_paragraph_is_hard_split(self):
        text = "y" * 12000
        chunks = split_chunks(text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 4096)
        self.assertEqual(sum(len(c) for c in chunks), len(text))


class TestRenderReport(unittest.TestCase):
    def test_report_contains_priorities_and_drafts(self):
        from mailbrief.summarizer import MailBrief

        mails = [make_mail(1, "Facture impayée"), make_mail(2, "Newsletter")]
        report = Report(
            titre="Journée chargée",
            synthese="Deux mails importants.",
            model="qwen3:8b",
        )
        report.items = [
            MailBrief(uid=1, priorite="urgent", resume="Paiement en retard.",
                      brouillon="Bonjour, je règle ça."),
            MailBrief(uid=2, priorite="info", resume="Newsletter hebdo."),
        ]
        out = render_report(report, mails, datetime(2026, 10, 7, 8, 0))
        self.assertIn("🔴 URGENT", out)
        self.assertIn("🔵 INFO", out)
        self.assertIn("Facture impayée", out)
        self.assertIn("Brouillon de réponse", out)
        self.assertIn("je règle ça", out)
        self.assertLessEqual(len(out), 4096)

    def test_info_mail_has_no_draft(self):
        from mailbrief.summarizer import MailBrief

        mails = [make_mail(5, "Promo")]
        report = Report(titre="t", synthese="s", model="m")
        report.items = [MailBrief(uid=5, priorite="info", resume="Promo -30%",
                                  brouillon="ne devrait pas apparaître")]
        out = render_report(report, mails, datetime(2026, 10, 7, 8, 0))
        self.assertNotIn("ne devrait pas apparaître", out)


class TestFrenchDates(unittest.TestCase):
    def test_header_is_in_french(self):
        from mailbrief.summarizer import MailBrief
        from mailbrief.formatter import fr_date

        mails = [make_mail(1)]
        report = Report(titre="t", synthese="s", model="m")
        report.items = [MailBrief(uid=1, priorite="info", resume="r")]
        when = datetime(2026, 10, 7, 8, 0)  # mercredi
        out = render_report(report, mails, when)
        self.assertIn("Brief du Mercredi 7 octobre 2026", out)
        self.assertNotIn("Wednesday", out)
        self.assertNotIn("October", out)
        self.assertEqual(fr_date(when), "7 octobre 2026")


class TestBatching(unittest.TestCase):
    def test_single_batch_when_small(self):
        from mailbrief.summarizer import _make_batches
        mails = [make_mail(i) for i in range(5)]
        self.assertEqual(len(_make_batches(mails)), 1)

    def test_splits_when_prompt_too_large(self):
        from mailbrief.summarizer import _make_batches, _block_for
        mails = [make_mail(i, body="x" * 800) for i in range(30)]
        total = sum(len(_block_for(m)) for m in mails)
        batches = _make_batches(mails, max_chars=5000)
        self.assertGreater(len(batches), 1)
        # aucun mail perdu, aucun doublon, ordre conservé
        flat = [m.uid for b in batches for m in b]
        self.assertEqual(flat, [m.uid for m in mails])
        for batch in batches:
            self.assertLessEqual(sum(len(_block_for(m)) for m in batch), 5000)
        self.assertGreater(total, 5000)

    def test_oversized_single_mail_still_alone(self):
        from mailbrief.summarizer import _make_batches
        mails = [make_mail(1, body="y" * 20000), make_mail(2, body="z" * 100)]
        batches = _make_batches(mails, max_chars=5000)
        self.assertEqual(len(batches), 2)
        self.assertEqual([m.uid for m in batches[0]], [1])


class TestImapPresets(unittest.TestCase):
    """Toujours avec un .env inexistant : jamais contaminé par le .env réel."""

    NO_ENV = "/tmp/mailbrief-test-no-env-file"

    def _load(self, env):
        import os
        from mailbrief.config import load_config
        with mock.patch.dict(os.environ, env, clear=False):
            for key in ("IMAP_PRESET", "IMAP_HOST", "IMAP_PORT",
                        "IMAP_USER", "IMAP_PASSWORD"):
                if key not in env:
                    os.environ.pop(key, None)
            return load_config(env_file=self.NO_ENV)

    def test_gmail_preset(self):
        cfg = self._load({"IMAP_PRESET": "gmail"})
        self.assertEqual(cfg.imap_host, "imap.gmail.com")
        self.assertEqual(cfg.imap_port, 993)

    def test_outlook_preset(self):
        cfg = self._load({"IMAP_PRESET": "outlook"})
        self.assertEqual(cfg.imap_host, "outlook.office365.com")

    def test_proton_preset_uses_bridge(self):
        cfg = self._load({"IMAP_PRESET": "proton"})
        self.assertEqual(cfg.imap_host, "127.0.0.1")
        self.assertEqual(cfg.imap_port, 1143)

    def test_preset_wins_over_stale_host(self):
        """Un IMAP_HOST résiduel ne doit pas neutraliser le preset."""
        cfg = self._load({"IMAP_PRESET": "gmail",
                          "IMAP_HOST": "imap.mail.me.com"})
        self.assertEqual(cfg.imap_host, "imap.gmail.com")

    def test_custom_requires_host(self):
        import os
        from mailbrief.config import load_config
        with mock.patch.dict(os.environ, {"IMAP_PRESET": "custom"}, clear=False):
            os.environ.pop("IMAP_HOST", None)
            with self.assertRaises(SystemExit):
                load_config(env_file=self.NO_ENV)

    def test_custom_uses_explicit_host(self):
        cfg = self._load({"IMAP_PRESET": "custom",
                          "IMAP_HOST": "imap.mondomaine.fr",
                          "IMAP_PORT": "143"})
        self.assertEqual(cfg.imap_host, "imap.mondomaine.fr")
        self.assertEqual(cfg.imap_port, 143)

    def test_unknown_preset_exits(self):
        from mailbrief.config import load_config
        with mock.patch.dict(os.environ, {"IMAP_PRESET": "bidon"}):
            with self.assertRaises(SystemExit):
                load_config(env_file=self.NO_ENV)

    def test_default_preset_is_icloud(self):
        cfg = self._load({})
        self.assertEqual(cfg.imap_host, "imap.mail.me.com")

    def test_every_preset_resolves_to_a_real_host(self):
        """Couvre les 10 presets d'un coup : une faute de frappe dans le
        tableau serait ignorée par les tests de cas particuliers."""
        from mailbrief.config import IMAP_PRESETS

        attendus = {
            "icloud": "imap.mail.me.com",
            "gmail": "imap.gmail.com",
            "outlook": "outlook.office365.com",
            "yahoo": "imap.mail.yahoo.com",
            "free": "imap.free.fr",
            "orange": "ssl0.orange.net",
            "sfr": "imap.sfr.fr",
            "laposte": "imap.laposte.net",
            "proton": "127.0.0.1",
            "custom": "",
        }
        self.assertEqual(set(IMAP_PRESETS), set(attendus),
                         "un preset a été ajouté/retiré sans mettre le test à jour")
        for name, host in attendus.items():
            with self.subTest(preset=name):
                env = {"IMAP_PRESET": name}
                if name == "custom":
                    # custom n'a pas d'hôte prédéfini : on fournit le sien.
                    env["IMAP_HOST"] = "imap.example.org"
                    host = "imap.example.org"
                cfg = self._load(env)
                self.assertEqual(cfg.imap_host, host)
                self.assertGreater(cfg.imap_port, 0)

    def test_preset_hosts_match_the_readme_table(self):
        """Le README promet ces hôtes : on ne veut pas qu'ils divergent."""
        import re
        from pathlib import Path
        from mailbrief.config import IMAP_PRESETS

        root = Path(__file__).resolve().parent.parent
        for readme in (root / "README.md", root / "README.fr.md"):
            text = readme.read_text(encoding="utf-8")
            rows = re.findall(
                r"\|\s*`([a-z]+)`\s*\|[^\n]*\|\s*([^|\n]+?)\s*\|", text
            )
            declared = {name for name, _ in rows}
            self.assertTrue(
                declared.issubset(set(IMAP_PRESETS)),
                f"{readme.name} cite un preset inconnu : "
                f"{declared - set(IMAP_PRESETS)}",
            )


class TestNormalize(unittest.TestCase):
    def test_missing_uid_is_added_as_info(self):
        mails = [make_mail(10), make_mail(11)]
        payload = {
            "titre": "titre",
            "synthese": "synthese",
            "mails": [{"uid": 10, "priorite": "urgent", "resume": "ok"}],
        }
        report = _normalize(payload, mails)
        self.assertEqual({i.uid for i in report.items}, {10, 11})
        self.assertEqual(report.items[-1].uid, 11)

    def test_unknown_priority_falls_back_to_info_and_drops_draft(self):
        mails = [make_mail(1)]
        payload = {"mails": [{"uid": 1, "priorite": "pub",
                              "resume": "r", "brouillon": "à supprimer"}]}
        report = _normalize(payload, mails)
        self.assertEqual(report.items[0].priorite, "info")
        self.assertEqual(report.items[0].brouillon, "")

    def test_unknown_uid_is_dropped(self):
        mails = [make_mail(1)]
        payload = {"mails": [{"uid": 999, "priorite": "urgent", "resume": "r"}]}
        report = _normalize(payload, mails)
        self.assertEqual(report.items[0].uid, 1)  # re-complété depuis les mails

    def test_ordering_by_priority(self):
        mails = [make_mail(1), make_mail(2), make_mail(3)]
        payload = {"mails": [
            {"uid": 1, "priorite": "info", "resume": "a"},
            {"uid": 2, "priorite": "urgent", "resume": "b"},
            {"uid": 3, "priorite": "repondre", "resume": "c"},
        ]}
        report = _normalize(payload, mails)
        self.assertEqual([i.uid for i in report.items], [2, 3, 1])


class TestExtractJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        raw = "Voici :\n```json\n{\"a\": 2}\n```\nfin"
        self.assertEqual(_extract_json(raw), {"a": 2})

    def test_wrapped_in_prose(self):
        raw = 'Le résultat est {"a": 3} comme demandé.'
        self.assertEqual(_extract_json(raw), {"a": 3})

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            _extract_json("pas de json ici")


class TestHtmlToText(unittest.TestCase):
    def test_strips_tags_and_keeps_breaks(self):
        html = "<p>Bonjour</p><p>Voici<br>le corps</p><style>p{color:red}</style>"
        text = html_to_text(html)
        self.assertIn("Bonjour", text)
        self.assertNotIn("<p>", text)
        self.assertNotIn("color:red", text)

    def test_decodes_entities(self):
        self.assertIn("&", html_to_text("<p>A &amp; B</p>"))


class TestParseMessage(unittest.TestCase):
    def test_rfc2047_subject_and_quoted_printable(self):
        raw = (
            b"From: =?UTF-8?B?Sm9obiDDoXRoYW5l?= <john@example.com>\r\n"
            b"Subject: =?UTF-8?Q?Caf=C3=A9_et_r=C3=A9union?=\r\n"
            b"Date: Tue, 06 Oct 2026 10:00:00 +0000\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Transfer-Encoding: quoted-printable\r\n"
            b"\r\n"
            b"Bonjour, on se voit demain =E2=9C=94\r\n"
        )
        mail = parse_message(7, raw, max_body_chars=1000)
        self.assertEqual(mail.subject, "Café et réunion")
        self.assertEqual(mail.sender_addr, "john@example.com")
        self.assertIn("demain", mail.body)
        self.assertEqual(mail.uid, 7)

    def test_html_fallback_when_no_plain_text(self):
        raw = (
            b"From: a@b.fr\r\nSubject: HTML only\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            b"<html><body><p>Contenu <b>important</b></p></body></html>"
        )
        mail = parse_message(1, raw, max_body_chars=1000)
        self.assertEqual(mail.body, "Contenu important")

    def test_body_is_truncated(self):
        raw = (
            b"From: a@b.fr\r\nSubject: Long\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n" + b"z" * 5000
        )
        mail = parse_message(1, raw, max_body_chars=100)
        self.assertLess(len(mail.body), 200)
        self.assertIn("tronqué", mail.body)

    def test_attachment_is_extracted_not_in_body(self):
        raw = (
            b"From: a@b.fr\r\nSubject: Avec pj\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=XX\r\n\r\n"
            b"--XX\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Voir la pi\xc3\xa8ce jointe.\r\n"
            b"--XX\r\nContent-Type: application/pdf\r\n"
            b"Content-Disposition: attachment; filename=\"facture.pdf\"\r\n\r\n"
            b"AAAA\r\n--XX--\r\n"
        )
        mail = parse_message(1, raw, max_body_chars=1000)
        self.assertEqual(mail.attachments, ["facture.pdf"])
        self.assertIn("Voir la pièce jointe", mail.body)
        self.assertNotIn("AAAA", mail.body)


class TestState(unittest.TestCase):
    def test_roundtrip_and_dedupe(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            st = State.load(path)
            self.assertEqual(st.processed, [])
            st.mark([3, 5, 5, 7])
            st.uidvalidity = 42
            st.runs = 2
            st.save()

            again = State.load(path)
            self.assertEqual(again.processed, [3, 5, 7])
            self.assertEqual(again.uidvalidity, 42)
            self.assertTrue(again.is_done(5))
            self.assertFalse(again.is_done(6))

    def test_corrupt_state_does_not_crash(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{pas json", encoding="utf-8")
            st = State.load(path)
            self.assertEqual(st.processed, [])


if __name__ == "__main__":
    unittest.main()
