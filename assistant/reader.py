"""Deciding when an answer is a document rather than a sentence.

Speech is a lossy channel. `spoken_reply` takes the first three sentences and caps them
at 450 characters, which is right for "the torch is on" and quietly destructive for a
piece of research: the rest is on the canvas, but the canvas is behind whatever window
you are actually working in, so the content exists and cannot be reached at the moment
it is wanted.

This module answers one question -- *is this a document?* -- so that when it is, the
whole thing goes to an always-on-top reader while the voice says a short true summary.
Nothing is dropped, and the two channels stop competing: reading is for detail, speech
is for knowing there is detail to read.

What counts as a document is deliberately structural rather than just long. A rambling
four-hundred-word answer is not a report and opening a window over the user's work for
it would be an irritation; a piece with headings, a table, a diagram or a figure caption
is one, because something took the trouble to organise it. The formatting those pieces
are expected to follow is not invented here -- it is `docs/document_standards.md`, the
same standard already enforced for Word output.
"""

from __future__ import annotations

import re

# Markdown structure. Any one of these means the answer was organised, not just long.
_HEADING = re.compile(r"^#{1,4}\s+\S", re.M)
_TABLE = re.compile(r"^\|.+\|\s*$\n^\|[\s:|-]+\|\s*$", re.M)
_FENCE = re.compile(r"^```", re.M)
_MERMAID = re.compile(r"^```\s*mermaid", re.M | re.I)
# "Figure 3: Something — the takeaway", which document_standards.md requires.
_FIGURE = re.compile(r"^(?:figure|fig\.?|table)\s*\d+\s*[:.—-]", re.M | re.I)
_BULLETS = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S", re.M)

# What matters is the channel, not only the length. A table and a diagram cannot be
# spoken *at all* -- a voice reading "pipe dash dash pipe" conveys nothing -- so those go
# on screen at almost any size. A figure caption says something was laid out, which earns
# a window fairly early. Headings and bullets are only organisation: a heading over two
# sentences is still two sentences. And unstructured prose has to be long enough that
# speech was going to discard most of it regardless.
UNSPEAKABLE_CHARS = 120  # has a table or a diagram: speech cannot carry these at all
STRONG_CHARS = 400       # has numbered figure captions
MIN_CHARS = 700          # has headings, or bullets with code
LONG_CHARS = 1800        # no structure at all


def signals(text: str) -> dict[str, bool]:
    """Which document markers are present. Useful for explaining a decision."""
    text = text or ""
    return {
        "headings": bool(_HEADING.search(text)),
        "table": bool(_TABLE.search(text)),
        "diagram": bool(_MERMAID.search(text)),
        "code": bool(_FENCE.search(text)),
        "figures": bool(_FIGURE.search(text)),
        "bullets": len(_BULLETS.findall(text)) >= 4,
    }


def is_document(text: str, strong_chars: int = STRONG_CHARS, min_chars: int = MIN_CHARS,
                long_chars: int = LONG_CHARS, unspeakable_chars: int = UNSPEAKABLE_CHARS) -> bool:
    """True when this answer should be read rather than only heard.

    Sorted by what the *voice* can do with it, not by length alone. A table or a diagram
    is unspeakable -- reading a table aloud conveys nothing at all -- so it goes on
    screen at almost any size. Numbered figures mean it was laid out, which qualifies
    early. Headings and bullets are organisation, so they need real length behind them.
    Unstructured prose needs enough length that speech would have discarded most of it
    regardless, silently discarding an answer being the failure this exists to prevent.
    """
    text = (text or "").strip()
    size = len(text)
    if size < min(unspeakable_chars, strong_chars, min_chars, long_chars):
        return False
    found = signals(text)
    if (found["table"] or found["diagram"]) and size >= unspeakable_chars:
        return True
    if found["figures"] and size >= strong_chars:
        return True
    if (found["headings"] or (found["bullets"] and found["code"])) and size >= min_chars:
        return True
    return size >= long_chars


def title_of(text: str, fallback: str = "Nova") -> str:
    """A name for the window: the first heading, or the first real sentence."""
    text = (text or "").strip()
    heading = _HEADING.search(text)
    if heading:
        line = text[heading.start():].splitlines()[0]
        cleaned = line.lstrip("#").strip(" *_`")
        if cleaned:
            return cleaned[:80]
    for line in text.splitlines():
        line = line.strip(" *_`#>-")
        if len(line) > 12:
            return (line[:77] + "...") if len(line) > 80 else line
    return fallback


def gist(text: str, sentences: int = 2) -> str:
    """The spoken line that accompanies a document: enough to know what arrived.

    Headings, tables, code and diagrams are stripped rather than read out -- a voice
    reading "pipe dash dash pipe" is worse than one that says there is a table.
    """
    text = text or ""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)       # fences, diagrams included
    text = re.sub(r"^\|.*$", " ", text, flags=re.M)           # table rows
    # Heading lines go entirely. Keeping their words runs the title into the first
    # sentence ("Aux vs Bluetooth Summary Aux output is...") and the window already
    # shows the title anyway.
    text = re.sub(r"^#{1,6}\s+.*$", " ", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*_`>]+", "", text)
    text = " ".join(text.split())
    parts = re.split(r"(?<=[.!?])\s+", text)
    said = " ".join(parts[:max(1, sentences)]).strip()
    return said[:300].rsplit(" ", 1)[0] + "..." if len(said) > 300 else said
