"""The web reach the chat models get, and the state tool.

Deliberately offline. The parsers are tested against captured fixture HTML rather than
a live search, because a test that needs the internet fails for reasons that have
nothing to do with the code and teaches you to ignore it. The live path was verified by
hand against both DuckDuckGo endpoints and three real sites when it was written.
"""

from __future__ import annotations

import unittest

from assistant.agents import web
from assistant.agents.tools import ToolContext, call, schemas

# Shaped like the real DuckDuckGo HTML page, including the <b> tags around matched words
# that used to end a snippet at the first highlighted term.
SEARCH_HTML = """
<html><body>
<div class="result results_links">
  <a class="result__a" href="https://example.com/one">First <b>Result</b></a>
  <a class="result__snippet" href="x"><b>Narendra</b> Modi is the current holder.
     A second sentence follows.</a>
</div>
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Ftwo&amp;rut=abc">Second</a>
  <a class="result__snippet">Another snippet.</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://example.com/three">Same host again</a>
  <a class="result__snippet">Should be dropped as a duplicate host.</a>
</div>
</body></html>
"""

PAGE_HTML = """
<html><head><title>  A Real   Page </title>
<script>var junk = "should never be read";</script>
<style>.a{color:red}</style></head>
<body>
<nav><a href="/">Home</a><a href="/x">Menu</a><a href="/y">Contents</a></nav>
<main>
  <h1>The Heading</h1>
  <p>The first paragraph is long enough to count as real prose on any page.</p>
  <p>A second paragraph, also clearly a sentence.</p>
</main>
</body></html>
"""


class WebSearchParsing(unittest.TestCase):
    def test_reads_titles_urls_and_snippets(self) -> None:
        parser = web._Results()
        parser.feed(SEARCH_HTML)
        self.assertEqual(parser.rows[0]["title"], "First Result")
        self.assertEqual(parser.rows[0]["url"], "https://example.com/one")

    def test_snippet_survives_highlighted_words(self) -> None:
        """The <b> around a matched word must not end the snippet."""
        parser = web._Results()
        parser.feed(SEARCH_HTML)
        snippet = parser.rows[0]["snippet"]
        self.assertIn("Narendra", snippet)
        self.assertIn("second sentence", snippet)

    def test_unwraps_the_redirect_url(self) -> None:
        self.assertEqual(web._real_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Ftwo"),
                         "https://example.org/two")
        self.assertEqual(web._real_url("/relative/only"), "")


class PageReading(unittest.TestCase):
    def test_title_is_read_from_head(self) -> None:
        title, _text = web.to_text(PAGE_HTML)
        self.assertEqual(title, "A Real Page")

    def test_scripts_and_styles_never_appear(self) -> None:
        _title, text = web.to_text(PAGE_HTML)
        self.assertNotIn("junk", text)
        self.assertNotIn("color:red", text)

    def test_navigation_furniture_is_dropped(self) -> None:
        _title, text = web.to_text(PAGE_HTML)
        for item in ("Home", "Menu", "Contents"):
            self.assertNotIn(f"\n{item}\n", f"\n{text}\n")

    def test_the_article_survives(self) -> None:
        _title, text = web.to_text(PAGE_HTML)
        self.assertIn("first paragraph is long enough", text)
        self.assertIn("second paragraph", text)


class UrlGuard(unittest.TestCase):
    """A fetched page is untrusted text, and a model reading it can be talked into
    fetching another URL -- so the loopback and private ranges are refused outright."""

    def test_refuses_this_machine(self) -> None:
        for url in ("http://127.0.0.1:8000/x", "http://localhost/y", "http://[::1]/z",
                    "http://192.168.1.1/", "http://10.0.0.5/", "http://nova.local/"):
            self.assertTrue(web.allowed(url), f"{url} should have been refused")

    def test_refuses_other_schemes(self) -> None:
        for url in ("file:///c:/secrets.txt", "ftp://example.com/x", "data:text/html,hi"):
            self.assertIn("http", web.allowed(url))

    def test_allows_an_ordinary_site(self) -> None:
        self.assertEqual(web.allowed("https://example.com/page"), "")

    def test_read_refuses_without_fetching(self) -> None:
        title, text = web.read("http://127.0.0.1:9333/json/version")
        self.assertEqual(title, "")
        self.assertIn("won't fetch", text)


class StatusTool(unittest.TestCase):
    def test_reports_real_state_only(self) -> None:
        class Runner:
            def status_sentence(self) -> str:
                return "Claude has been working on the audit for 2 minutes."

        class Recorder:
            running = True

            def elapsed(self) -> str:
                return "40 seconds"

        answer = call(ToolContext(runner=Runner(), capture=Recorder()), "nova_status", {})
        self.assertIn("audit", answer)
        self.assertIn("40 seconds", answer)

    def test_idle_when_nothing_runs(self) -> None:
        class Runner:
            def status_sentence(self) -> str:
                return ""

        self.assertIn("No background tasks", call(ToolContext(runner=Runner()), "nova_status", {}))

    def test_hidden_when_nothing_can_report(self) -> None:
        """With no runner the tool could only ever say "idle", which is a lie waiting
        to happen -- so it is not offered at all."""
        names = [s["function"]["name"] for s in schemas(ToolContext())]
        self.assertNotIn("nova_status", names)

    def test_a_broken_service_does_not_break_the_turn(self) -> None:
        class Runner:
            def status_sentence(self) -> str:
                return "One task running."

        class Publisher:
            def waiting(self) -> str:
                raise RuntimeError("database is locked")

        answer = call(ToolContext(runner=Runner(), publisher=Publisher()), "nova_status", {})
        self.assertIn("One task running", answer)


class WebTools(unittest.TestCase):
    def test_search_is_offered_to_every_backend(self) -> None:
        """Web reach must not depend on any local service being wired up: it is the
        whole reason the cheap tier can stop delegating lookups."""
        names = [s["function"]["name"] for s in schemas(ToolContext())]
        self.assertIn("web_search", names)
        self.assertIn("read_page", names)

    def test_empty_search_says_so_rather_than_inviting_a_guess(self) -> None:
        answer = call(ToolContext(), "web_search", {"query": ""})
        self.assertIn("rather than guessing", answer)


if __name__ == "__main__":
    unittest.main()
