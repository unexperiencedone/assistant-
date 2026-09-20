"""Nova's character, and the identity it keeps.

Three layers, cheapest first, matching the routing cascade:

1. **An instant intent.** "Who are you" is a fixed question with a fixed answer, so it
   never reaches a model at all and no backend gets the chance to introduce itself.
2. **A system prompt on every backend.** `seed()` for small, fast models where context
   costs; `character()` for the CLI agents, which can afford the whole document.
3. **A guard on the way out** (`guard.py`), for the phrasings that slip past both.

Worth being honest about the limits of this. A big model's character is trained in,
which is why it holds up under pressure and across long conversations. Nova cannot train
its backends, so what it has instead is a description, some worked examples, and a net.
Examples do most of the work, especially on small models: when the voice drifts, add a
row to the table in character.md rather than another adjective.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..paths import resource_path

CHARACTER_FILE = resource_path("assistant", "persona", "character.md")
SEED_FILE = resource_path("assistant", "persona", "seed.md")

# Registers: the same character, different formality. "owner" is the person whose
# laptop this is; "customer" is anyone reached through work done on their behalf.
# Only the formality changes -- never the values, and never the honesty.
OWNER, CUSTOMER = "owner", "customer"

CUSTOMER_REGISTER = """
You are speaking to a customer, not to the person who owns this machine. Be warm and
professional, explain things in terms of their business rather than your internals, and
use their name if you know it. Never commit to a price, a deadline or a change of scope:
say you will get it confirmed. Everything else about you stays exactly the same.
"""

OWNER_REGISTER = """
You are speaking to the person whose machine this is. Be direct and informal. Technical
detail is welcome when it is useful. If they ask which model handled something, or what
went wrong underneath, tell them plainly -- that is their machine and their business.
"""


# Set once at startup from config, so the name and maker do not have to be threaded
# through every backend constructor that happens to build a prompt.
_DEFAULTS = {"name": "Nova", "maker": ""}


def configure(name: str, maker: str) -> None:
    _DEFAULTS["name"] = name or "Nova"
    _DEFAULTS["maker"] = maker or ""


@lru_cache(maxsize=8)
def _read(path: str) -> str:
    try:
        return resource_path(*path.split("/")).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def character(name: str = "", maker: str = "", register: str = OWNER) -> str:
    """The full character document, for backends that can afford it."""
    return _compose("assistant/persona/character.md", name, maker, register)


def seed(name: str = "", maker: str = "", register: str = OWNER) -> str:
    """The compressed version, for small models where every token of context counts."""
    return _compose("assistant/persona/seed.md", name, maker, register)


def _compose(path: str, name: str, maker: str, register: str) -> str:
    name, maker = name or _DEFAULTS["name"], maker or _DEFAULTS["maker"]
    text = _read(path)
    if not text:
        return ""
    text = text.replace("{name}", name or "Nova").replace("{maker}", maker or "its maker")
    return f"{text}\n{CUSTOMER_REGISTER if register == CUSTOMER else OWNER_REGISTER}".strip()


# The four things people actually ask, and the honest answer to each. Assembled here
# rather than generated, so the answer to "who are you" cannot drift between backends,
# between runs, or with the weather.
ASKS_ORIGIN = re.compile(
    r"\b(?:who|what)\s+(?:built|made|created|wrote|designed|owns|develops?|developed)\s+(?:you|u|this|nova)\b"
    r"|\byour\s+(?:maker|creator|developer|owner|company)\b"
    r"|\bwhere\s+(?:do\s+)?you\s+come\s+from\b", re.I)

ASKS_MECHANICS = re.compile(
    r"\bhow\s+(?:do|does|did|are|is|can)\s+(?:you|u|nova|this|it)\s*\w*\s*work"
    r"|\bhow\s+(?:do|does)\s+(?:you|u|nova|this|it)\b"
    r"|\bhow\s+(?:were|was)\s+(?:you|nova|this|it)\s+(?:made|built|created)\b"
    r"|\bwhat\s+can\s+you\s+do\b"
    r"|\bexplain\s+(?:yourself|how\s+you\s+work)\b", re.I)

ASKS_STACK = re.compile(
    r"\bwhat\s+(?:ai|model|llm|engine|tech(?:nology)?|stack|system)\b"
    r"|\b(?:which|what)\s+\w*\s*model\b"
    r"|\bare\s+you\s+(?:built|based|running|powered)\s+on\b"
    r"|\bwhat\s+(?:are\s+you|is\s+nova)\s+(?:built|based|running)\s+on\b"
    r"|\b(?:powered|runs?)\s+by\b", re.I)


def about(said: str = "", name: str = "", maker: str = "", register: str = OWNER) -> str:
    """The answer to an identity question, however much of it was asked.

    Every sentence is true and fixed. The stack sentence in particular is never a denial:
    someone who sincerely asks what Nova runs on gets a straight answer, because the
    first thing this character is for is not lying.
    """
    name, maker = name or _DEFAULTS["name"], maker or _DEFAULTS["maker"]
    said = said or ""
    parts = [identity_line(name, maker, register)]

    if ASKS_ORIGIN.search(said):
        parts.append(f"{maker or 'My maker'} built me."
                     if register == CUSTOMER else
                     f"{maker or 'My maker'} built me, and you have been shaping me since.")
    if ASKS_MECHANICS.search(said):
        parts.append("Most of what you ask me I handle here on this machine in a fraction "
                     "of a second, and I save the things you repeat so they cost nothing "
                     "next time. Anything genuinely new, I think through properly.")
    if ASKS_STACK.search(said):
        parts.append(f"{maker or 'My maker'} builds me on AI models from a few providers, "
                     "with a lot of my own machinery around them.")
    return " ".join(parts)


def identity_line(name: str = "", maker: str = "", register: str = OWNER) -> str:
    """What Nova says when asked who it is. Never generated, so it cannot drift."""
    name, maker = name or _DEFAULTS["name"], maker or _DEFAULTS["maker"]
    if register == CUSTOMER:
        return (f"I'm {name}, an assistant made by {maker or 'the team'}. "
                "Someone on the team checks anything important before it goes out.")
    return (f"I'm {name}. I live on this laptop and your phone, and I get quicker at "
            "your routines the more we work together.")
