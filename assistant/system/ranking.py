"""Ranking: how well a name matches the query, plus how much you use the item.

Frecency uses exponential decay. Each use adds 1 point, and points lose half
their value every `half_life` seconds. Storing just (score, score_at) is
equivalent to summing every past use's decayed weight:

    score_now = score * 0.5 ** ((now - score_at) / half_life)
    on use:     score = score_now + weight;  score_at = now

So an app you opened 20 times last month but never since gradually yields to
one you've opened 5 times this week, with O(1) storage and O(1) updates.

Habits fade at different speeds: an app you use daily is a habit for months, a
file belongs to this week's project. So each kind has its own half-life.

Sorting in SQL needs a key that doesn't change as time passes. Taking log2 of
the decayed score gives one:

    log2(score_now) = log2(score) + score_at / half_life - now / half_life

The last term is the same for every row, so ordering by `rank_key =
log2(score) + score_at / half_life` is exactly ordering by the decayed score, at
any moment, for rows that share a half-life. It is stored and indexed.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

DEFAULT_HALF_LIFE_DAYS = 14.0
DAY = 86400.0


@dataclass
class HalfLives:
    """Seconds after which past use counts half, per kind (app, file, folder, command)."""
    by_kind: dict[str, float] = field(default_factory=dict)  # days
    default_days: float = DEFAULT_HALF_LIFE_DAYS

    def seconds(self, kind: str) -> float:
        return max(0.01, float(self.by_kind.get(kind, self.default_days))) * DAY

    def signature(self) -> str:
        """Changes whenever stored rank keys would need recomputing."""
        return f"{self.default_days}|" + ",".join(f"{k}={v}" for k, v in sorted(self.by_kind.items()))


def rank_key(score: float, score_at: float, half_life: float) -> float | None:
    """Time-invariant sort key (see module docstring). None for never-used rows."""
    return math.log2(score) + score_at / half_life if score > 0 else None

_SPLIT = re.compile(r"[\s_\-.()\[\]]+")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def normalize(text: str) -> str:
    return " ".join(_SPLIT.split(text.lower())).strip()


def stem(name: str) -> str:
    """Name without a file extension ('Report.final.pdf' -> 'Report.final')."""
    base, dot, ext = name.rpartition(".")
    return base if dot and base and 1 <= len(ext) <= 5 and " " not in ext else name


def decayed(score: float, score_at: float, now: float, half_life: float) -> float:
    if score <= 0:
        return 0.0
    return score * 0.5 ** (max(0.0, now - score_at) / half_life)


def bump(score: float, score_at: float, half_life: float, weight: float = 1.0, now: float | None = None) -> tuple[float, int]:
    now = time.time() if now is None else now
    return decayed(score, score_at, now, half_life) + weight, int(now)


def demote(score: float, score_at: float, half_life: float, now: float | None = None) -> tuple[float, int]:
    """You said "no, the other one": halve what this item had earned."""
    now = time.time() if now is None else now
    return decayed(score, score_at, now, half_life) * 0.5, int(now)


_QUERY_ALIASES = {"ms": "microsoft", "vs": "visual studio", "vscode": "visual studio code", "cmd": "command prompt"}


def match_score(query: str, name: str) -> float:
    """0 = no match, 100 = exact. Tuned so the obvious intent wins:
    exact > prefix > word prefix > all words present > substring > initials > subsequence."""
    q = normalize(query)
    expanded = " ".join(_QUERY_ALIASES.get(w, w) for w in q.split())
    if expanded != q:
        return max(_match(q, name), _match(expanded, name))
    return _match(q, name)


def _match(q: str, name: str) -> float:
    if not q:
        return 0.0
    full = normalize(name)
    base = normalize(stem(name))
    if q in (base, full):
        return 100.0
    if base.startswith(q) or full.startswith(q):
        return 85.0 - min(10.0, (len(base) - len(q)) * 0.2)
    words = normalize(_CAMEL.sub(" ", stem(name))).split()
    if any(w.startswith(q) for w in words):
        return 70.0
    q_words = q.split()
    if len(q_words) > 1 and all(any(w.startswith(qw) for w in words) for qw in q_words):
        return 65.0
    if len(q_words) > 1 and _aligns(q_words, words):
        return 60.0  # "vs code" -> Visual Studio Code
    if q in full:
        return 50.0
    if len(q_words) > 1 and all(len(qw) >= 2 and qw in full for qw in q_words):
        return 45.0
    compact = q.replace(" ", "")
    if len(compact) >= 2 and "".join(w[0] for w in words).startswith(compact):
        return 40.0  # "vsc" -> Visual Studio Code
    if len(compact) >= 3 and _is_subsequence(compact, full.replace(" ", "")):
        return 20.0
    return 0.0


def _aligns(q_words: list[str], words: list[str]) -> bool:
    """Each query word, in order, is a prefix of a name word or the initials of consecutive name words."""
    if not q_words:
        return True
    qw, rest = q_words[0], q_words[1:]
    for i in range(len(words)):
        if words[i].startswith(qw) and _aligns(rest, words[i + 1:]):
            return True
        span = words[i:i + len(qw)]
        if len(qw) >= 2 and len(span) == len(qw) and "".join(w[0] for w in span) == qw and _aligns(rest, words[i + len(qw):]):
            return True
    return False


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(ch in it for ch in needle)


def rank(match: float, frecency: float, kind: str, path: str, prefer_kind: str | None = None) -> float:
    """Combine match quality with usage. Usage can lift a decent match above a
    slightly better one you never use, but can't rescue a poor match."""
    if match <= 0:
        return 0.0
    usage = min(45.0, 15.0 * math.log2(1.0 + frecency))
    kind_bonus = 8.0 if prefer_kind and kind == prefer_kind else 0.0
    depth_penalty = min(8.0, max(0, path.count("\\") + path.count("/") - 4) * 0.5)
    return match + usage * (match / 100.0) + kind_bonus - depth_penalty
