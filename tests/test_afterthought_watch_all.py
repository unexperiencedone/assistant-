"""Watching every app: what is read, and what never is, whatever the setting says."""

import unittest
from types import SimpleNamespace

from assistant.afterthought import privacy
from assistant.afterthought.service import AfterThoughtService as AfterThought

SOCIAL = ["whatsapp", "instagram"]
SOCIAL_TITLES = [r"\bwhatsapp\b", r"\binstagram\b", r"web\.whatsapp\.com", r"instagram\.com"]


def denied(process, title):
    return privacy.denied(process, title, set(SOCIAL), SOCIAL_TITLES)


class WatchAll(unittest.TestCase):
    def test_excluded_social_apps_and_their_tabs_are_never_read(self):
        self.assertTrue(denied("WhatsApp.exe", "Chats"))
        self.assertTrue(denied("chrome.exe", "WhatsApp - Google Chrome"))
        self.assertTrue(denied("msedge.exe", "Instagram - Microsoft Edge"))

    def test_linkedin_and_work_apps_are_read(self):
        self.assertFalse(denied("chrome.exe", "Feed | LinkedIn - Google Chrome"))
        self.assertFalse(denied("Code.exe", "showcase.py - Assisstant"))
        self.assertFalse(denied("WINWORD.EXE", "Report.docx - Word"))

    def test_the_built_in_private_list_still_wins(self):
        self.assertTrue(denied("chrome.exe", "New Incognito tab - Google Chrome"))
        self.assertTrue(denied("chrome.exe", "HDFC Bank NetBanking"))
        self.assertTrue(denied("Bitwarden.exe", "Vault"))

    def test_watch_all_watches_without_an_opt_in(self):
        service = AfterThought.__new__(AfterThought)          # only the consent logic, no models
        service.store = SimpleNamespace(meta=lambda key, default=None: [])
        service.settings = SimpleNamespace(watch_all=True)
        self.assertTrue(service.watched("Notepad"))
        service.settings = SimpleNamespace(watch_all=False)
        self.assertFalse(service.watched("Notepad"))


if __name__ == "__main__":
    unittest.main()
