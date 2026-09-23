"""Checking a message written to go *out*, to somebody who is not the owner.

Everything else in `persona/` protects Nova's own voice: that it says who it is, that it
does not borrow an identity, that it owns a failure instead of blaming the plumbing. A
message to a stranger needs the opposite protections, and a live test showed why.

Asked to draft outreach for the owner's web-design work, Groq produced:

    "I'm Nova, a local web-designer helping small Lucknow businesses...
     at a special introductory rate (or even free for the first month)."

Two failures in one paragraph. It signed as Nova, when an email selling the owner's
services has to come from the owner. And it invented commercial terms -- a rate, and a
free month -- that nobody authorised. The second is the dangerous one: an assistant
offering a stranger a month of free work under a real person's name is a liability, not
an awkward sentence.

The rule against it already existed. `CUSTOMER_REGISTER` says "Never commit to a price,
a deadline or a change of scope: say you will get it confirmed." It simply was not
applied to drafting, because drafting was not a path anything checked.

So this checks. Instructing a model not to promise things is necessary and, on the
evidence of this project, not sufficient: the same instruct-and-hope pattern has failed
for skills, for handovers and for recipes. A draft that trips any of these is handed
back with the reason rather than staged, and the model gets to write it again.
"""

from __future__ import annotations

import re

# Money, in the forms a draft actually uses.
_PRICE = re.compile(
    r"(?:[₹$€£]\s?\d|\b\d+\s?(?:k|thousand|lakh|lac|crore)\b(?!\s*(?:words|characters))"
    r"|\b(?:rs\.?|inr|usd|eur|gbp)\s?\d"
    r"|\b\d+\s*(?:rupees|dollars|euros|pounds)\b)", re.I)
# Giving work away, or discounting it. "Free" is the one that cost the most in testing.
_FREE = re.compile(
    r"\b(?:free\s+(?:of\s+charge|trial|month|week|website|site|sample|mockup|audit|consultation)"
    r"|for\s+free|no\s+charge|at\s+no\s+cost|complimentary|on\s+the\s+house"
    r"|\d+\s*%\s*(?:off|discount)|discount(?:ed)?\s+(?:rate|price)"
    r"|introductory\s+(?:rate|price|offer)|special\s+(?:rate|price|offer)"
    r"|half\s+price|money[\s-]back)\b", re.I)
# Promising when it will be done.
_DEADLINE = re.compile(
    r"\b(?:within\s+\d+\s*(?:hours?|days?|weeks?)|in\s+(?:just\s+)?\d+\s*(?:hours?|days?|weeks?)"
    r"|by\s+(?:tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next\s+week)"
    r"|same[\s-]day|overnight|guaranteed?\s+(?:delivery|in|by))\b", re.I)
# Promising an outcome nobody can promise.
_GUARANTEE = re.compile(
    r"\b(?:guarantee[ds]?|guaranteeing|promise\s+(?:you|that)|we\s+will\s+double"
    r"|rank\s+(?:you\s+)?(?:first|number\s*1|#1|top)|100\s*%\s+(?:satisfaction|success))\b", re.I)


def _nova_as_author(text: str, nova: str) -> re.Pattern[str]:
    """"I'm Nova" in a message that is supposed to be from a person."""
    name = re.escape(nova or "Nova")
    return re.compile(rf"\b(?:I(?:'m|’m| am)\s+{name}\b"
                      rf"|(?:regards|thanks|sincerely|best)\s*,?\s*\n?\s*{name}\s*$"
                      rf"|this\s+is\s+{name}\s+(?:writing|reaching|getting)\b)", re.I | re.M)


def problems(text: str, nova: str = "Nova", owner: str = "") -> list[str]:
    """Everything wrong with this draft, in the words to hand back to the model.

    Empty means it is safe to stage. Each entry says what to do instead, because a
    complaint a model cannot act on just produces the same draft again.
    """
    text = text or ""
    found: list[str] = []

    if _nova_as_author(text, nova).search(text):
        who = owner or "the person whose account this is"
        found.append(f"it is signed as {nova}, but this goes out from {who} -- "
                     f"write it in the first person as {who} and never name {nova}")
    if _PRICE.search(text):
        found.append("it names a price. Say the price will be confirmed, and give no figure")
    if _FREE.search(text):
        found.append("it offers something free, discounted or at a special rate. "
                     "Offer nothing: say you would be glad to discuss what it would take")
    if _DEADLINE.search(text):
        found.append("it commits to a timescale. Say timings can be agreed once the work is clear")
    if _GUARANTEE.search(text):
        found.append("it guarantees a result. Describe what you would do, not what it will achieve")
    return found


def verdict(text: str, nova: str = "Nova", owner: str = "") -> str:
    """"" when the draft is safe to stage, otherwise the reason and what to fix."""
    found = problems(text, nova, owner)
    if not found:
        return ""
    listed = "; ".join(f"{i}. {reason}" for i, reason in enumerate(found, 1))
    return ("This draft can't go out as written -- " + listed +
            ". Rewrite it and draft it again.")
