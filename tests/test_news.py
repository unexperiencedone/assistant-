"""The instant news briefing: phrase matching, topic filtering, cache writing."""

import json
import tempfile
import unittest
from pathlib import Path

from assistant.ambient import news
from assistant.intents import match_intent

FAKE = {
    "NDTV": [
        {"title": "Budget session opens", "summary": "Parliament meets.", "url": "u1", "source": "NDTV"},
        {"title": "Chip plant approved", "summary": "A semiconductor fab.", "url": "u2", "source": "NDTV"},
    ],
    "The Hindu": [
        {"title": "Monsoon retreats", "summary": "Rain eases.", "url": "u3", "source": "The Hindu"},
    ],
    "Google News": [
        {"title": "Budget session opens", "summary": "Duplicate of the NDTV item.", "url": "u4", "source": "NDTV"},
    ],
}


class NewsPhrases(unittest.TestCase):
    def test_phrases_match(self) -> None:
        for phrase in ("pull a news briefing", "news briefing", "what is the news",
                       "give me the headlines", "any news", "whats the news today"):
            intent = match_intent(phrase)
            self.assertIsNotNone(intent, phrase)
            self.assertEqual(intent.name, "news_briefing", phrase)

    def test_topic_is_captured(self) -> None:
        self.assertEqual(match_intent("news briefing for semiconductors").args, {"topic": "semiconductors"})

    def test_other_commands_still_win(self) -> None:
        self.assertEqual(match_intent("open spotify").name, "open_item")


class NewsFetch(unittest.TestCase):
    def setUp(self) -> None:
        self._real = news._read_feed
        news._read_feed = lambda source, url, timeout: list(FAKE.get(source, []))

    def tearDown(self) -> None:
        news._read_feed = self._real

    def test_picks_across_feeds_without_repeats(self) -> None:
        items, read = news.fetch(3)
        self.assertEqual(read, ["NDTV", "The Hindu", "Google News"])
        titles = [i["title"] for i in items]
        self.assertEqual(len(set(titles)), len(titles))
        self.assertIn("Budget session opens", titles)

    def test_topics_are_a_preference_by_default(self) -> None:
        items, _ = news.fetch(3, ["semiconductor"])
        self.assertEqual(items[0]["title"], "Chip plant approved")
        self.assertEqual(len(items), 3)

    def test_strict_topics_filter(self) -> None:
        items, _ = news.fetch(3, ["semiconductor"], strict=True)
        self.assertEqual([i["title"] for i in items], ["Chip plant approved"])

    def test_strict_topic_with_no_match_is_empty(self) -> None:
        self.assertEqual(news.fetch(3, ["cricket"], strict=True)[0], [])

    def test_unreachable_feeds_are_skipped(self) -> None:
        def boom(source, url, timeout):
            raise OSError("no network")

        news._read_feed = boom
        items, read = news.fetch(3)
        self.assertEqual((items, read), ([], []))
        self.assertIn("couldn't reach", news.to_speech(items))

    def test_cache_keeps_weather_and_markets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ambient.json"
            path.write_text(json.dumps({"weather": {"place": "Kanpur"}, "markets": [{"name": "NIFTY 50"}],
                                        "headlines": [{"title": "old"}]}), encoding="utf-8")
            items, read = news.fetch(2)
            news.write_cache(path, items, read)
            doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(doc["weather"]["place"], "Kanpur")
        self.assertEqual(doc["markets"][0]["name"], "NIFTY 50")
        self.assertEqual(len(doc["headlines"]), 2)
        self.assertEqual(doc["sources_read"], 3)


if __name__ == "__main__":
    unittest.main()
