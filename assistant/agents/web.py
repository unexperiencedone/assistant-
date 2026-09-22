"""Web reach for the chat models: a search, and a page read.

Without these two, every question about something current has to go to Claude Code --
the slow, expensive tier -- because a chat model with no internet can only guess or
delegate. Giving Groq a search and a reader is what lets the cheap tier finish the job
itself, which is the entire point of the cascade in AGENTS.md.

No API key and no new dependency, both deliberate. Search goes through DuckDuckGo's HTML
endpoint, which needs neither; pages are fetched with urllib and reduced to text by
stdlib's own HTML parser.

Everything here fails soft, returning empty rather than raising. A model handed an
exception retries the same call; a model handed "no results" says so and moves on.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser

_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Nova/1.0"
# Both endpoints need the query POSTed as a form: a GET hands back the DuckDuckGo home
# page with no results in it, which is a silent nothing rather than an error. Verified
# against both, in that order -- the lite page is the fallback when the first is empty.
SEARCH_URL = "https://html.duckduckgo.com/html/"
SEARCH_FALLBACK_URL = "https://lite.duckduckgo.com/lite/"

# Tags whose contents are never prose. Without this a page reads back as a wall of
# minified JavaScript, which burns the model's context and tells it nothing. <head> is
# deliberately absent: it holds <title>, and skipping it loses the one line of a page
# that reliably says what the page is.
_SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "form"}
# The article itself, when the page marks it. Every real site does, and reading only
# this is the difference between an article and forty lines of navigation menu.
_MAIN = {"main", "article"}
# Tags that end a line. HTML has no newlines of its own, so without these a page comes
# back as one run-on paragraph and the model cannot tell a heading from a sentence.
_BREAK = {"p", "br", "div", "li", "tr", "section", "article", "header", "footer",
          "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}


def _private(host: str) -> bool:
    """True for anything that is really this machine or this network.

    A fetched page is untrusted text, and a model reading it can be talked into fetching
    another URL. That makes the reader a route to Nova's own canvas server and anything
    else bound to localhost, so the loopback and private ranges are refused outright
    rather than left to a prompt to discourage.
    """
    host = (host or "").strip().lower().strip("[]")
    if not host or host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # a name that will not resolve is not worth fetching
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast):
            return True
    return False


def allowed(url: str) -> str:
    """Empty string if the URL is safe to fetch, otherwise the reason it is not."""
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return "that isn't a URL I can read"
    if parts.scheme not in ("http", "https"):
        return "I can only read http and https pages"
    if _private(parts.hostname or ""):
        return "that address is on this machine or this network, so I won't fetch it"
    return ""


def _get(url: str, timeout: float, form: dict[str, str] | None = None) -> str:
    """Fetch a page. With `form` it is a POST, which is what the search endpoints want."""
    headers = {
        "User-Agent": _AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        kind = (response.headers.get_content_type() or "").lower()
        if kind and not (kind.startswith("text/") or "xml" in kind or "json" in kind):
            return ""  # a PDF or an image read as text is noise, not content
        charset = response.headers.get_content_charset() or "utf-8"
        # Cap the read: some pages are tens of megabytes and none of that is useful.
        return response.read(2_000_000).decode(charset, errors="replace")


class _Stripper(HTMLParser):
    """HTML to something a model can read: prose, with line breaks where they belong."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []      # the whole page, as a fallback
        self.main: list[str] = []       # just the <main>/<article> region, when there is one
        self.title = ""
        self._depth = 0        # how deep inside a skipped tag we are
        self._main = 0         # how deep inside the article we are
        self._in_title = False

    def _emit(self, text: str) -> None:
        self.parts.append(text)
        if self._main:
            self.main.append(text)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP:
            self._depth += 1
        elif tag == "title":
            self._in_title = True
        else:
            if tag in _MAIN:
                self._main += 1
            if tag in _BREAK:
                self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._depth = max(0, self._depth - 1)
        elif tag == "title":
            self._in_title = False
        else:
            if tag in _BREAK:
                self._emit("\n")
            if tag in _MAIN:
                self._main = max(0, self._main - 1)

    def handle_data(self, data: str) -> None:
        if self._depth:
            return
        if self._in_title:
            self.title += data
        elif data.strip():
            self._emit(data)


def _prose(lines: list[str]) -> str:
    """Drop the furniture: menu items, breadcrumbs, one-word links.

    A navigation link is a line of one or two words with no sentence in it, and a page
    has dozens. Keeping them means the model reads "Main menu, Contents, Random article"
    instead of the article, so a line earns its place by being a sentence or by being
    long enough to be a heading worth keeping.
    """
    kept: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or (kept and line == kept[-1]):
            continue
        sentence = line[-1] in ".!?:" or len(line.split()) >= 6
        if sentence or len(line) >= 40:
            kept.append(line)
    return "\n".join(kept)


def to_text(html: str) -> tuple[str, str]:
    """(title, prose) for a page. The article is preferred over the whole document."""
    stripper = _Stripper()
    try:
        stripper.feed(html)
    except Exception:
        return "", " ".join(re.sub(r"<[^>]+>", " ", html).split())

    def clean(parts: list[str]) -> str:
        text = "".join(parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\s*\n\s*", "\n", text)
        return re.sub(r"\n{2,}", "\n", text).strip()

    # Prefer the marked-up article, but only if it actually held the page's content --
    # some sites wrap a sidebar in <article> and the real text lives elsewhere.
    article = _prose(clean(stripper.main).split("\n"))
    whole = _prose(clean(stripper.parts).split("\n"))
    text = article if len(article) >= 400 or len(article) >= len(whole) // 2 else whole
    return " ".join(stripper.title.split()), text


class _Results(HTMLParser):
    """DuckDuckGo's HTML page, which is plain enough to read without a browser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._want = ""   # "title" or "snippet" while inside one
        self._buf = ""
        self._href = ""
        # The tag that opened the capture. DuckDuckGo wraps the matched words in <b>,
        # so closing on any end tag ends the snippet at the first highlighted word --
        # which silently turns a whole sentence into "Narendra Modi is the current".
        self._tag = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        # "result__a" is the full HTML page, "result-link" the lite one. Both are read
        # the same way, so one parser covers the fallback without a second class.
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._want, self._buf, self._tag = "title", "", tag
            self._href = _real_url(attributes.get("href") or "")
        elif "result__snippet" in classes or "result-snippet" in classes:
            self._want, self._buf, self._tag = "snippet", "", tag

    def handle_endtag(self, tag: str) -> None:
        if not self._want or tag != self._tag:
            return
        text = " ".join(self._buf.split())
        if self._want == "title":
            if text and self._href:
                self.rows.append({"title": text, "url": self._href, "snippet": ""})
        elif self.rows and not self.rows[-1]["snippet"]:
            self.rows[-1]["snippet"] = text[:400]
        self._want, self._buf, self._tag = "", "", ""

    def handle_data(self, data: str) -> None:
        if self._want:
            self._buf += data


def _real_url(href: str) -> str:
    """DuckDuckGo wraps every result in a redirect; the real URL is the uddg parameter."""
    if "uddg=" not in href:
        return href if href.startswith("http") else ""
    try:
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg", [""])[0]
    except ValueError:
        return ""
    return target if target.startswith("http") else ""


def search(query: str, count: int = 5, timeout: float = 10.0) -> list[dict[str, str]]:
    """Top results for a query: title, url and the engine's own snippet.

    The snippet matters more than it looks. It is often the whole answer, so a model can
    reply from the search alone without spending a second call reading the page.
    """
    query = " ".join((query or "").split())
    if not query:
        return []
    parser = _Results()
    for url in (SEARCH_URL, SEARCH_FALLBACK_URL):
        try:
            html = _get(url, timeout, form={"q": query})
        except Exception:
            continue
        parser = _Results()
        try:
            parser.feed(html)
        except Exception:
            continue
        if parser.rows:
            break
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for row in parser.rows:
        host = urllib.parse.urlparse(row["url"]).netloc.lower()
        if host in seen:          # five results from one site is one result
            continue
        seen.add(host)
        rows.append(row)
        if len(rows) >= max(1, count):
            break
    return rows


def read(url: str, limit: int = 2500, timeout: float = 12.0) -> tuple[str, str]:
    """(title, text) for one page, truncated to `limit` characters.

    The cap is what keeps this affordable: the model needs the top of an article, not
    the comment section, and an untruncated page can outrun the whole context window.
    """
    refusal = allowed(url)
    if refusal:
        return "", refusal
    try:
        html = _get(url, timeout)
    except Exception as error:
        return "", f"I couldn't open that page ({type(error).__name__})."
    if not html:
        return "", "That page isn't text I can read."
    title, text = to_text(html)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " ..."
    return title, text or "That page had no readable text."
