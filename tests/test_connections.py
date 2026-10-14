"""Connections: keys are tried before they are kept, .env keeps its other lines, and a
key is never handed back whole."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant import connections
from assistant.config import Settings


class EnvFile(unittest.TestCase):
    def test_setting_one_key_keeps_every_other_line(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("# keys\nGROQ_API_KEY=old\nOTHER=1\n", encoding="utf-8")
            connections.write_env(env, "GROQ_API_KEY", "new-key-123")
            lines = env.read_text(encoding="utf-8").splitlines()
            self.assertIn("# keys", lines)
            self.assertIn("OTHER=1", lines)
            self.assertIn("GROQ_API_KEY=new-key-123", lines)
            self.assertNotIn("GROQ_API_KEY=old", lines)
            connections.write_env(env, "GROQ_API_KEY", None)
            self.assertNotIn("GROQ_API_KEY", env.read_text(encoding="utf-8"))

    def test_a_key_is_only_ever_shown_by_its_last_four(self):
        self.assertEqual(connections.masked("gsk_abcdefghWXYZ"), "…WXYZ")


class SaveKey(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = Path(self.folder.name) / ".env"
        self.service = connections.ConnectionsService(Settings(), self.env)
        self.name = Settings().agents.groq.api_key_env
        self.saved = os.environ.pop(self.name, None)

    def tearDown(self):
        os.environ.pop(self.name, None)
        if self.saved is not None:
            os.environ[self.name] = self.saved
        self.folder.cleanup()

    def test_a_refused_key_is_not_kept(self):
        with mock.patch.object(connections, "try_key", return_value=(False, "it refused that key")):
            ok, said = self.service.save_key("groq", "gsk_wrongwrongwrong")
        self.assertFalse(ok)
        self.assertIn("said no", said)
        self.assertFalse(self.env.exists())
        self.assertNotIn(self.name, os.environ)

    def test_an_accepted_key_works_at_once_and_is_masked_in_status(self):
        with mock.patch.object(connections, "try_key", return_value=(True, "")):
            ok, _ = self.service.save_key("groq", "gsk_goodgoodgood1234")
        self.assertTrue(ok)
        self.assertEqual(os.environ[self.name], "gsk_goodgoodgood1234")
        groq = next(p for p in self.service.status()["providers"] if p["id"] == "groq")
        self.assertTrue(groq["connected"])
        self.assertNotIn("gsk_goodgoodgood1234", str(self.service.status()))

    def test_offline_still_keeps_the_key_and_says_so(self):
        with mock.patch.object(connections, "try_key", return_value=(None, "couldn't reach it")):
            ok, said = self.service.save_key("groq", "gsk_offlineoffline")
        self.assertTrue(ok)
        self.assertIn("couldn't check", said)

    def test_rubbish_is_refused_before_any_network(self):
        with mock.patch.object(connections, "try_key") as tried:
            ok, _ = self.service.save_key("groq", "hi there")
        self.assertFalse(ok)
        tried.assert_not_called()
