"""Entity tags: the names, files, tickets and figures a memory is about.

No NLP model: capitalised phrases, emails, handles, domains, file names, ticket ids,
dates and amounts, plus the people and projects from your profile. Tags do two jobs:
they let two memories about "Rahul" and "the rollout" find each other even when the
sentences around them differ, and the garbage filter treats a chunk with no entity
and little text as noise.
"""

from __future__ import annotations

import re

_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "this", "that", "these", "those", "i", "you", "we",
    "he", "she", "it", "they", "my", "your", "our", "on", "in", "at", "to", "for", "of", "with", "from",
    "new", "tab", "untitled", "home", "google", "chrome", "edge", "microsoft", "windows", "file", "edit",
    "view", "help", "settings", "search", "inbox", "page", "today", "yesterday", "tomorrow", "monday",
    "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october", "november", "december", "nova",
    "ok", "yes", "no", "hi", "hello", "dear", "thanks", "regards", "please",
}
_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                                      # emails
    re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,30}"),                                  # handles
    re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b"),                                 # tickets: NOVA-123
    re.compile(r"#\d{2,6}\b"),                                                     # PR / issue numbers
    re.compile(r"\b[\w-]+\.(?:py|js|jsx|ts|tsx|md|pdf|docx?|xlsx?|pptx?|csv|json|toml|ya?ml|java|cpp|c|go|rs|ipynb)\b",
               re.I),                                                              # file names
    re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|in|org|io|dev|ai|edu|gov|net|co)\b", re.I),  # domains
    re.compile(r"(?:₹|rs\.?\s?|inr\s?|\$|usd\s?)\d[\d,]*(?:\.\d+)?\s?(?:k|lakh|crore|cr|m|bn)?", re.I),  # money
    re.compile(r"\b\d{1,2}\s(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I),     # dates
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s\d{1,2}\b", re.I),
]
_CAPITALISED = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,3}|[A-Z]{2,6}\d*)\b")


def extract(text: str, known: list[str] | None = None, limit: int = 24) -> list[str]:
    """Lower-cased tags, most specific first, without duplicates."""
    text = text or ""
    found: list[str] = []
    for pattern in _PATTERNS:
        found += [m.group(0) for m in pattern.finditer(text)]
    lowered = text.lower()
    for name in known or []:
        if name and len(name) > 2 and re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            found.append(name)
    for match in _CAPITALISED.finditer(text):
        phrase = match.group(1)
        words = [w for w in phrase.split() if w.lower() not in _STOP]
        if words:
            found.append(" ".join(words))
    out: list[str] = []
    for tag in found:
        tag = " ".join(tag.lower().strip(" .,:;()[]").split())
        if len(tag) > 2 and tag not in _STOP and tag not in out:
            out.append(tag)
    return out[:limit]


def _same(x: str, y: str) -> bool:
    """"acme corp" is "client acme corp": equal, or one inside the other on word boundaries."""
    if x == y:
        return True
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    return len(short) >= 4 and f" {short} " in f" {long} "


def overlap(a: list[str] | set[str], b: list[str] | set[str]) -> int:
    """How many tags of `a` name something in `b`."""
    others = list(b)
    return sum(1 for tag in set(a) if any(_same(tag, other) for other in others))
