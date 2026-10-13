"""One shape for what every brain reports about a turn.

Each backend already says what a turn cost, in its own words:

    Claude Code   result line: usage {input_tokens, output_tokens, cache_read_input_tokens,
                  cache_creation_input_tokens, output_tokens_details.thinking_tokens},
                  modelUsage {<model>: ...}, total_cost_usd (list price)
    Antigravity   result.usage {input_tokens, output_tokens, thinking_tokens, cache_read_tokens}
    Groq/OpenRouter  the provider's own usage, summed over the turn's rounds
                  {prompt_tokens, completion_tokens, rounds} (chat_api.ChatAgent._note_usage)

Nothing here estimates a token count: every number is the provider's. Thinking tokens
are a part of output, never added on top of it.
"""

from __future__ import annotations

from typing import Any

FREE_BACKENDS = ("groq", "openrouter")
# Antigravity has no per-turn price: it runs on its own plan's quota. It is counted as a
# subscription brain like Claude, with tokens but no dollar figure.
SUBSCRIPTION_BACKENDS = ("claude", "antigravity")


def backend_key(label: str) -> str:
    """'Claude Code' -> claude, 'Groq' -> groq. Labels are what the bus carries."""
    lower = (label or "").lower()
    for key in ("claude", "antigravity", "groq", "openrouter"):
        if key in lower:
            return key
    return lower.split()[0] if lower.split() else "unknown"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def empty() -> dict[str, Any]:
    return {"model": "", "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking": 0,
            "cost_usd": 0.0, "rounds": 0}


def from_claude(obj: dict[str, Any]) -> dict[str, Any]:
    """A Claude Code result line (raw), or the result data claude_cli passes along."""
    out = empty()
    usage = obj.get("usage") or {}
    out["input"] = _int(usage.get("input_tokens"))
    out["output"] = _int(usage.get("output_tokens"))
    out["cache_read"] = _int(usage.get("cache_read_input_tokens"))
    out["cache_write"] = _int(usage.get("cache_creation_input_tokens"))
    out["thinking"] = _int((usage.get("output_tokens_details") or {}).get("thinking_tokens"))
    models = obj.get("modelUsage") or obj.get("models") or {}
    names = list(models) if isinstance(models, dict) else list(models)
    out["model"] = ", ".join(names)[:80]
    # cost_usd is the turn's own cost (claude_cli and the backfill work it out);
    # total_cost_usd in a raw line is the process's running total, so it is only
    # right for a process's first turn.
    cost = obj["cost_usd"] if "cost_usd" in obj else obj.get("total_cost_usd")
    out["cost_usd"] = float(cost or 0.0)
    out["rounds"] = _int(obj.get("num_turns", obj.get("turns")))
    return out


def from_antigravity(usage: dict[str, Any]) -> dict[str, Any]:
    out = empty()
    out["input"] = _int(usage.get("input_tokens"))
    out["output"] = _int(usage.get("output_tokens"))
    out["cache_read"] = _int(usage.get("cache_read_tokens"))
    out["thinking"] = _int(usage.get("thinking_tokens"))
    return out


def from_chat(usage: dict[str, Any], model: str = "") -> dict[str, Any]:
    out = empty()
    out["input"] = _int(usage.get("prompt_tokens"))
    out["output"] = _int(usage.get("completion_tokens"))
    out["rounds"] = _int(usage.get("rounds"))
    out["model"] = model
    return out


def from_result(backend: str, data: dict[str, Any] | None) -> dict[str, Any]:
    """Whatever the runner's result event carried, for this backend."""
    data = data or {}
    if backend == "claude":
        return from_claude(data)
    if backend == "antigravity":
        return from_antigravity(data.get("usage") or {})
    if backend in FREE_BACKENDS:
        return from_chat(data.get("usage") or {}, str(data.get("model") or ""))
    return empty()


def total(row: dict[str, Any]) -> int:
    """Every token the provider processed for the turn: fresh input, cached input both
    ways, and output. Cache reads are cheap per token but they are still the context the
    model read, which is the number that grows when a turn is wasteful."""
    return row["input"] + row["cache_read"] + row["cache_write"] + row["output"]
