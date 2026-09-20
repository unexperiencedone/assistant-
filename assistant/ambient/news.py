"""Headlines without an agent turn.

The full briefing (weather, markets, cited summaries) is still the agent's job --
see `service.py`. This module does only the one part a script can do honestly:
read a few news RSS feeds and take their titles and their own one-line blurbs.
Nothing is summarised or invented here, so "news briefing" can be an instant
command that costs nothing.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# Feeds are read in order and interleaved, so one noisy site can't fill the briefing.
FEEDS: list[tuple[str, str]] = [
    ("NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("Google News", "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"),
]
_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Nova/1.0"
_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return " ".join(_TAGS.sub(" ", text or "").replace("&nbsp;", " ").split())


def _read_feed(source: str, url: str, timeout: float) -> list[dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": _AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        root = ET.fromstring(response.read())
    items = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title", ""))
        if not title:
            continue
        # Google News appends " - Publisher" to every title; the publisher is already a field.
        publisher = _clean(item.findtext("source", "")) or source
        if title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].rstrip()
        items.append({
            "title": title,
            "summary": _clean(item.findtext("description", ""))[:300],
            "url": (item.findtext("link", "") or "").strip(),
            "source": publisher,
        })
    return items


def fetch(count: int = 3, topics: list[str] | None = None, timeout: float = 8.0,
          strict: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    """Top `count` headlines, plus the names of the feeds that actually answered.

    `topics` are a preference: matching headlines come first, then the rest fill up.
    `strict` makes them a filter instead -- for when the user named the topic out
    loud and would rather hear nothing than hear something else.

    Feeds that time out or fail to parse are skipped rather than failing the whole
    briefing; an empty list means every one of them was unreachable.
    """
    per_feed: list[list[dict[str, Any]]] = []
    read: list[str] = []
    for source, url in FEEDS:
        try:
            items = _read_feed(source, url, timeout)
        except Exception:
            continue
        if items:
            per_feed.append(items)
            read.append(source)
    wanted = [t.lower() for t in (topics or [])]
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = int(time.time())
    # Round-robin across the feeds, preferring the configured topics on the first pass.
    passes = (True,) if wanted and strict else (True, False) if wanted else (False,)
    for match_topics in passes:
        for row in range(max((len(f) for f in per_feed), default=0)):
            for feed in per_feed:
                if row >= len(feed) or len(picked) >= count:
                    continue
                item = feed[row]
                key = item["title"].lower()[:60]
                if key in seen:
                    continue
                text = f"{item['title']} {item['summary']}".lower()
                if match_topics and not any(t in text for t in wanted):
                    continue
                seen.add(key)
                picked.append({**item, "at": now})
            if len(picked) >= count:
                break
    return picked[:count], read


def write_cache(path: Path, headlines: list[dict[str, Any]], read: list[str]) -> None:
    """Put the headlines in the briefing cache, leaving weather and markets as they were."""
    data: dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    data["headlines"] = headlines
    data["reading"] = read
    data["sources_read"] = len(read)
    data["updated_at"] = int(time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def to_speech(headlines: list[dict[str, Any]]) -> str:
    if not headlines:
        return "I couldn't reach any of the news feeds just now."
    lead = f"Top {len(headlines)} right now. " if len(headlines) > 1 else ""
    return lead + " ".join(f"{h['source']}: {h['title']}." for h in headlines)
