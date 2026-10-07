"""Tests d'intégration du CLI (IMAP et Ollama simulés)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from mailbrief.__main__ import main
from mailbrief.imap_client import Mail
from mailbrief.state import State
from mailbrief.summarizer import MailBrief, Report

ENV = {
    "IMAP_USER": "test@icloud.com",
    "IMAP_PASSWORD": "app-password",
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "TELEGRAM_CHAT_ID": "42",
}


def sample_mails():
    return [
        Mail(uid=10, sender_addr="a@b.fr", sender_name="Alice",
             subject="Contrat à signer", date=datetime(2026, 10, 7, tzinfo=timezone.utc),
             body="Merci de signer avant vendredi."),
        Mail(uid=11, sender_addr="news@x.fr", sender_name="X",
             subject="Newsletter", date=datetime(2026, 10, 7, tzinfo=timezone.utc),
             body="Les actus de la semaine."),
    ]


def sample_report():
    rep = Report(titre="Jour chargé", synthese="Un contrat, une newsletter.",
                 model="qwen3:8b")
    rep.items = [
        MailBrief(uid=10, priorite="repondre", resume="Contrat à signer avant vendredi.",
                  brouillon="Bonjour, je signe."),
        MailBrief(uid=11, priorite="info", resume="Newsletter hebdo."),
    ]
    return rep


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "state.json"
        self.env = dict(ENV)
        self.env["STATE_PATH"] = str(self.state_path)
        patcher = mock.patch.dict(os.environ, self.env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, argv, mails=None, report=None):
        out = io.StringIO()
        with mock.patch(
            "mailbrief.__main__.fetch_new_mails",
            side_effect=lambda *a, **k: (mails if mails is not None else [], 7),
        ), mock.patch(
            "mailbrief.__main__.summarize", return_value=report or sample_report()
        ), mock.patch(
            "mailbrief.__main__.check_ollama", return_value=True
        ), mock.patch(
            "mailbrief.__main__.send_message", return_value=["x"]
        ) as send, redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue(), send

    def test_dry_run_prints_but_never_marks_state(self):
        code, out, send = self.run_main(["--dry-run"], mails=sample_mails())
        self.assertEqual(code, 0)
        self.assertIn("Contrat à signer", out)
        self.assertIn("Brouillon de réponse", out)
        send.assert_not_called()

        st = State.load(self.state_path)
        self.assertEqual(st.processed, [],
                         "un dry-run ne doit pas consommer les mails")
        self.assertEqual(st.runs, 0)

    def test_real_run_marks_state(self):
        code, out, send = self.run_main([], mails=sample_mails())
        self.assertEqual(code, 0)
        send.assert_called_once()
        self.assertEqual(State.load(self.state_path).processed, [10, 11])

    def test_state_not_marked_when_telegram_fails(self):
        from mailbrief.telegram_client import TelegramError

        out = io.StringIO()
        with mock.patch(
            "mailbrief.__main__.fetch_new_mails",
            return_value=(sample_mails(), 7),
        ), mock.patch(
            "mailbrief.__main__.summarize", return_value=sample_report()
        ), mock.patch(
            "mailbrief.__main__.check_ollama", return_value=True
        ), mock.patch(
            "mailbrief.__main__.send_message",
            side_effect=TelegramError("boom"),
        ), redirect_stdout(out):
            code = main([])

        self.assertEqual(code, 3)
        self.assertFalse(State.load(self.state_path).processed,
                         "un envoi échoué ne doit pas valider les mails")

    def test_already_processed_mails_are_excluded(self):
        State(
            path=self.state_path, uidvalidity=7, processed=[10], last_uid=10
        ).save()
        captured = {}

        def fake_fetch(cfg, exclude_uids, uidvalidity, since_hours, limit):
            captured["exclude"] = set(exclude_uids)
            captured["uidvalidity"] = uidvalidity
            return [], uidvalidity

        with mock.patch("mailbrief.__main__.fetch_new_mails",
                        side_effect=fake_fetch), mock.patch(
            "mailbrief.__main__.send_message", return_value=["x"]
        ), redirect_stdout(io.StringIO()):
            code = main([])
        self.assertEqual(code, 0)
        self.assertEqual(captured["exclude"], {10})
        self.assertEqual(captured["uidvalidity"], 7)

    def test_no_new_mail_sends_empty_brief(self):
        code, out, send = self.run_main([])
        self.assertEqual(code, 0)
        send.assert_called_once()
        sent_text = send.call_args[0][2]
        self.assertIn("Aucun nouveau mail", sent_text)
        self.assertEqual(State.load(self.state_path).runs, 1)

    def test_force_resets_state(self):
        State(path=self.state_path, uidvalidity=7, processed=[10]).save()
        captured = {}

        def fake_fetch(cfg, exclude_uids, uidvalidity, since_hours, limit):
            captured["exclude"] = set(exclude_uids)
            return [], 7

        with mock.patch("mailbrief.__main__.fetch_new_mails",
                        side_effect=fake_fetch), mock.patch(
            "mailbrief.__main__.send_message", return_value=["x"]
        ), redirect_stdout(io.StringIO()):
            main(["--force"])
        self.assertEqual(captured["exclude"], set())

    def test_missing_imap_config_exits(self):
        with mock.patch.dict(os.environ, {"IMAP_USER": "", "IMAP_PASSWORD": ""}):
            with self.assertRaises(SystemExit):
                main([])

    def test_check_runs(self):
        with mock.patch("mailbrief.__main__.check_ollama", return_value=True), \
                mock.patch("mailbrief.__main__.ImapClient") as imap, \
                mock.patch("mailbrief.__main__.check_token", return_value="bot"), \
                redirect_stdout(io.StringIO()):
            imap.return_value.__enter__ = mock.Mock(return_value=None)
            imap.return_value.__exit__ = mock.Mock(return_value=None)
            code = main(["--check"])
        self.assertIn(code, (0, 1))

    def test_ollama_failure_alerts_telegram_and_keeps_state(self):
        with mock.patch("mailbrief.__main__.fetch_new_mails",
                        return_value=(sample_mails(), 7)), \
                mock.patch("mailbrief.__main__.check_ollama", return_value=False), \
                mock.patch("mailbrief.__main__.send_message",
                           return_value=["x"]) as send, \
                redirect_stdout(io.StringIO()):
            code = main([])

        self.assertEqual(code, 4)
        send.assert_called_once()
        alert = send.call_args[0][2]
        self.assertIn("MailBrief", alert)
        self.assertIn("Code d'erreur", alert)
        self.assertIn("<b>4</b>", alert)
        self.assertEqual(State.load(self.state_path).processed, [],
                         "un échec ne doit pas valider les mails")

    def test_failure_alert_is_best_effort(self):
        from mailbrief.telegram_client import TelegramError

        with mock.patch("mailbrief.__main__.fetch_new_mails",
                        return_value=(sample_mails(), 7)), \
                mock.patch("mailbrief.__main__.check_ollama", return_value=False), \
                mock.patch("mailbrief.__main__.send_message",
                           side_effect=TelegramError("réseau coupé")), \
                redirect_stdout(io.StringIO()):
            code = main([])
        self.assertEqual(code, 4, "l'échec d'alerte ne doit pas masquer le vrai code")

    def test_state_file_is_json(self):
        self.run_main([], mails=sample_mails())
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["processed"], [10, 11])
        self.assertIn("last_run_iso", payload)


if __name__ == "__main__":
    unittest.main()
