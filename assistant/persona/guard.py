"""The last line: catch a backend introducing itself, and catch blame-shifting.

A system prompt argues with training, and training usually wins at the margins -- so
some replies will still begin "I'm Claude Code". This is the net under that. It runs on
the agent's reply before anything is spoken.

Two things are caught, because they are the two ways the character breaks in public:

- **Identity.** Replaced with Nova's own line rather than edited, because a half-scrubbed
  introduction reads worse than a clean one.
- **Blame.** "The API returned an error" tells the person about plumbing they did not ask
  about and cannot act on. Whatever runs underneath is Nova's business; the failure is
  Nova's to own.

What is deliberately NOT caught: an honest answer about what Nova is built on. Someone
who sincerely asks deserves a true answer, and scrubbing that would make the assistant a
liar rather than a brand. See the Identity section of character.md.
"""

from __future__ import annotations

import re

# "I'm Claude", "I am Claude Code", "I'm Gemini", "as a large language model", "I'm an
# AI assistant created by ...". Anchored on the self-introduction, so a sentence merely
# mentioning a model ("Claude wrote that file") is left alone.
IDENTITY_LEAK = re.compile(
    r"\bI(?:'m|’m| am)\s+(?:"
    r"Claude(?:\s+Code)?|Gemini|Antigravity|Llama|GPT[-\s]?\d*|ChatGPT|Groq|OpenRouter"
    r"|an?\s+(?:large\s+)?language\s+model"
    r"|an?\s+AI\s+(?:assistant\s+)?(?:made|developed|created|built|trained)\s+by"
    r")\b"
    # The quieter version, which the pattern above misses entirely: not "I am
    # Claude" but "I'm powered by Claude". It names a provider as the answer to what
    # Nova is, which is the same leak wearing a different sentence. The generic,
    # honest sentence -- "built on models from a few providers" -- names none, and
    # is deliberately left alone.
    r"|\bI(?:'m|\u2019m| am)\s+(?:powered|built|based|running|trained)\s+(?:on|by)\s+"
    r"(?:Claude|Anthropic|Gemini|Google|GPT|OpenAI|Llama|Meta|Groq|OpenRouter|Mistral)\b"
    r"|\bmy\s+underlying\s+(?:model|llm|system)\b"
    r"|\bI\s+run\s+on\s+(?:Claude|Anthropic|Gemini|Google|GPT|OpenAI|Llama|Groq)\b",
    re.IGNORECASE,
)

# Naming the plumbing to explain a failure. Nova says "I couldn't", not "it returned 429".
BLAME = re.compile(
    r"\b(?:"
    r"the\s+(?:api|model|backend|provider|server|service|endpoint)\s+"
    r"(?:returned|failed|errored|refused|timed\s+out|is\s+down|threw)"
    r"|(?:api|rate)[\s-]?limit(?:ed|\s+exceeded)?"
    r"|error\s+(?:code\s+)?\d{3}"
    r"|HTTP\s+\d{3}"
    r")\b",
    re.IGNORECASE,
)

OWNED_FAILURE = "I couldn't finish that."


def leaked_identity(text: str) -> bool:
    return bool(IDENTITY_LEAK.search(text or ""))


def blamed_something(text: str) -> bool:
    return bool(BLAME.search(text or ""))


def scrub(text: str, identity: str) -> tuple[str, str]:
    """Return (reply, what_tripped). `what_tripped` is "" when the reply was fine.

    The caller logs what tripped and which backend produced it: a backend that trips
    often is one that needs another example in character.md, or should not be given
    customer-facing turns.
    """
    if not (text or "").strip():
        return text, ""
    if leaked_identity(text):
        return identity, "identity"
    if blamed_something(text):
        # Keep whatever else the reply said; only the plumbing sentence goes.
        kept = [line for line in re.split(r"(?<=[.!?])\s+", text.strip())
                if not blamed_something(line)]
        rebuilt = " ".join(kept).strip()
        return (rebuilt or OWNED_FAILURE), "blame"
    return text, ""
