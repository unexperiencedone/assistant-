"""What was spent, what was probably saved, and where the next saving is.

**Spent** is measured: the providers' own token counts, and Claude Code's own cost
figure. That figure is the *list price* of the tokens. The Claude plan here is a flat
subscription, so it is not money leaving an account; it is how much of the plan a turn
used, in the one unit that compares across turns.

**Saved** is an estimate, and says so. Everything is valued from what turns cost *on
this machine*, never from a price table that could go stale:

- a free model or Antigravity turn replaced a Claude turn: valued at the median Claude
  turn of the same task type (all Claude turns when the type is unknown); the floor uses
  the 25th percentile.
- a scripted answer (automation, instant command) replaced whatever would otherwise
  have run. If Claude has done that kind of request here, that is the Claude median for
  the type. Otherwise it is what an unscripted request goes to first, a free-model
  turn: no money, the median free turn's tokens.

The browser saving is in tokens only: characters the extension did not send compared
with the page's full text, divided by four.
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from . import normalize
from .ledger import UsageLedger

CHARS_PER_TOKEN = 4
DAY = 86400.0


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


class Baseline:
    """What a Claude turn costs here: overall, and per task type."""

    def __init__(self, claude_turns: list[dict[str, Any]], agent_turns: list[dict[str, Any]] | None = None) -> None:
        free = [normalize.total(t) for t in (agent_turns or [])
                if t["ok"] and not t["escalated"] and t["backend"] in normalize.FREE_BACKENDS]
        # Where an unscripted request goes first: the free tier. No money, these tokens.
        self.free_median_tokens = int(statistics.median(free)) if free else 0
        paid = [t for t in claude_turns if t["ok"] and not t["escalated"] and t["cost_usd"] > 0]
        costs = [t["cost_usd"] for t in paid]
        tokens = [normalize.total(t) for t in paid]
        self.samples = len(paid)
        self.median = statistics.median(costs) if costs else 0.0
        self.floor = _quantile(costs, 0.25)
        self.median_tokens = int(statistics.median(tokens)) if tokens else 0
        by_type: dict[str, list[float]] = defaultdict(list)
        by_type_tokens: dict[str, list[int]] = defaultdict(list)
        for t in paid:
            if t["task_type"]:
                by_type[t["task_type"]].append(t["cost_usd"])
                by_type_tokens[t["task_type"]].append(normalize.total(t))
        self.by_type = {k: statistics.median(v) for k, v in by_type.items()}
        self.by_type_tokens = {k: int(statistics.median(v)) for k, v in by_type_tokens.items()}

    def cost(self, task_type: str = "") -> float:
        return self.by_type.get(task_type, self.median)

    def tokens(self, task_type: str = "") -> int:
        return self.by_type_tokens.get(task_type, self.median_tokens)

    def known(self, task_type: str) -> bool:
        return task_type in self.by_type

    def as_dict(self) -> dict[str, Any]:
        return {"median_usd": round(self.median, 4), "floor_usd": round(self.floor, 4),
                "median_tokens": self.median_tokens, "samples": self.samples,
                "task_types_priced": len(self.by_type), "free_median_tokens": self.free_median_tokens}


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def summary(ledger: UsageLedger, days: float | None = 30, aliases: dict[str, str] | None = None,
            automated: set[str] | None = None, now: float | None = None) -> dict[str, Any]:
    """Everything the Usage screen and `main.py usage` show, for the last `days` (None: all)."""
    now = now or time.time()
    since = now - days * DAY if days else 0.0
    aliases = aliases or {}
    automated = automated or set()

    all_turns = ledger.rows("turns")
    baseline = Baseline([t for t in all_turns if t["backend"] == "claude"], all_turns)
    turns = [t for t in all_turns if t["ts"] >= since]
    free = ledger.rows("free", since)
    browser = ledger.rows("browser", since)

    # -- spent ------------------------------------------------------------------------
    by_backend: dict[str, dict[str, Any]] = {}
    for t in turns:
        b = by_backend.setdefault(t["backend"], {"backend": t["backend"], "turns": 0, "failed": 0, "input": 0,
                                                 "output": 0, "cache_read": 0, "cache_write": 0, "thinking": 0,
                                                 "tokens": 0, "cost_usd": 0.0, "seconds": 0.0, "delegated": 0})
        b["turns"] += 1
        b["failed"] += 0 if t["ok"] else 1
        b["delegated"] += t["delegated"]
        for key in ("input", "output", "cache_read", "cache_write", "thinking", "cost_usd", "seconds"):
            b[key] += t[key]
        b["tokens"] += normalize.total(t)
    for b in by_backend.values():
        b["avg_tokens"] = b["tokens"] // max(1, b["turns"])
        b["avg_cost_usd"] = round(b["cost_usd"] / max(1, b["turns"]), 4)
        b["cost_usd"] = round(b["cost_usd"], 4)
        b["free"] = b["backend"] in normalize.FREE_BACKENDS
    claude = by_backend.get("claude", {})
    cache_base = claude.get("input", 0) + claude.get("cache_read", 0) + claude.get("cache_write", 0)

    spent = {
        "cost_usd": round(sum(t["cost_usd"] for t in turns), 4),
        "tokens": sum(normalize.total(t) for t in turns),
        "paid_tokens": sum(normalize.total(t) for t in turns if t["backend"] in normalize.SUBSCRIPTION_BACKENDS),
        "free_tokens": sum(normalize.total(t) for t in turns if t["backend"] in normalize.FREE_BACKENDS),
        "turns": len(turns),
        "hidden_delegated_usd": round(sum(t["cost_usd"] for t in turns if t["delegated"]), 4),
        "failed_usd": round(sum(t["cost_usd"] for t in turns if not t["ok"]), 4),
        "failed_turns": sum(1 for t in turns if not t["ok"]),
        "cache_hit": round(claude.get("cache_read", 0) / cache_base, 3) if cache_base else None,
        "by_backend": sorted(by_backend.values(), key=lambda b: -b["tokens"]),
    }

    # -- saved -------------------------------------------------------------------------
    def key_of(name: str) -> str:
        return aliases.get(name, name).strip().lower().replace(" ", "_")

    sources = {
        "scripted": {"source": "scripted", "label": "Automations and instant commands", "count": 0,
                     "usd": 0.0, "floor_usd": 0.0, "tokens": 0},
        "free_model": {"source": "free_model", "label": "Free models (Groq, OpenRouter)", "count": 0,
                       "usd": 0.0, "floor_usd": 0.0, "tokens": 0},
        "antigravity": {"source": "antigravity", "label": "Antigravity instead of Claude", "count": 0,
                        "usd": 0.0, "floor_usd": 0.0, "tokens": 0},
        "extension": {"source": "extension", "label": "Browser extension (page text not sent)", "count": 0,
                      "usd": 0.0, "floor_usd": 0.0, "tokens": 0},
    }
    daily: dict[str, dict[str, Any]] = {}

    def day(ts: float) -> dict[str, Any]:
        return daily.setdefault(_day(ts), {"day": _day(ts), "spent_usd": 0.0, "saved_usd": 0.0, "claude": 0,
                                           "antigravity": 0, "free_model": 0, "scripted": 0, "tokens": 0})

    by_automation: dict[str, dict[str, Any]] = {}
    for f in free:
        task = key_of(f["name"])
        known = baseline.known(task)
        value = baseline.cost(task) * f["count"] if known else 0.0
        s = sources["scripted"]
        s["count"] += f["count"]
        s["usd"] += value
        s["floor_usd"] += value
        s["tokens"] += (baseline.tokens(task) if known else baseline.free_median_tokens) * f["count"]
        d = day(f["ts"])
        d["scripted"] += f["count"]
        d["saved_usd"] += value
        label = f["name"] or f["route"]
        a = by_automation.setdefault(label, {"name": label, "route": f["route"], "count": 0, "usd": 0.0})
        a["count"] += f["count"]
        a["usd"] += value

    for t in turns:
        d = day(t["ts"])
        d["tokens"] += normalize.total(t)
        d["spent_usd"] += t["cost_usd"]
        if t["backend"] == "claude":
            d["claude"] += 1
            continue
        source = "antigravity" if t["backend"] == "antigravity" else "free_model"
        d[source] += 1
        if not t["ok"] or t["escalated"]:
            continue  # it failed, or Claude did the job anyway: nothing was saved
        value = baseline.cost(t["task_type"])
        s = sources[source]
        s["count"] += 1
        s["usd"] += value
        s["floor_usd"] += baseline.cost(t["task_type"]) if baseline.known(t["task_type"]) else baseline.floor
        s["tokens"] += max(0, baseline.tokens(t["task_type"]) - normalize.total(t))
        d["saved_usd"] += value

    by_method: dict[str, dict[str, Any]] = {}
    for b in browser:
        saved_tokens = max(0, b["page"] - b["sent"]) // CHARS_PER_TOKEN
        s = sources["extension"]
        s["count"] += 1
        s["tokens"] += saved_tokens
        m = by_method.setdefault(b["method"], {"method": b["method"], "calls": 0, "sent_tokens": 0, "page_tokens": 0})
        m["calls"] += 1
        m["sent_tokens"] += b["sent"] // CHARS_PER_TOKEN
        m["page_tokens"] += b["page"] // CHARS_PER_TOKEN

    for s in sources.values():
        s["usd"] = round(s["usd"], 4)
        s["floor_usd"] = round(s["floor_usd"], 4)
    requests = sum(f["count"] for f in free)
    answered = requests + len(turns)
    no_paid = requests + sum(1 for t in turns if t["backend"] not in normalize.SUBSCRIPTION_BACKENDS)
    saved = {
        "usd": round(sum(s["usd"] for s in sources.values()), 4),
        "floor_usd": round(sum(s["floor_usd"] for s in sources.values()), 4),
        "tokens": sum(s["tokens"] for s in sources.values()),
        "sources": list(sources.values()),
        "requests": answered,
        "without_paid_model": round(no_paid / answered, 3) if answered else None,
    }

    # -- where it goes, and where to save next ----------------------------------------
    types: dict[str, dict[str, Any]] = {}
    for t in turns:
        if not t["task_type"]:
            continue
        k = types.setdefault(t["task_type"], {"task_type": t["task_type"], "turns": 0, "claude_turns": 0,
                                              "cost_usd": 0.0, "tokens": 0, "backends": set()})
        k["turns"] += 1
        k["claude_turns"] += t["backend"] == "claude"
        k["cost_usd"] += t["cost_usd"]
        k["tokens"] += normalize.total(t)
        k["backends"].add(t["backend"])
    task_types = []
    for k in types.values():
        k["backends"] = sorted(k["backends"])
        k["cost_usd"] = round(k["cost_usd"], 4)
        k["automated"] = k["task_type"] in automated
        task_types.append(k)
    task_types.sort(key=lambda k: (-k["cost_usd"], -k["tokens"]))
    # Repeated, paid, and not yet a script: the requests a saved automation would take
    # off the bill entirely (AGENTS.md section 12 promotes these on its own after three).
    next_savings = [k for k in task_types if k["claude_turns"] >= 2 and not k["automated"]][:5]

    top_turns = sorted((t for t in turns if t["cost_usd"] > 0), key=lambda t: -t["cost_usd"])[:8]
    recent = sorted(turns, key=lambda t: -t["ts"])[:30]

    return {
        "window": {"days": days, "since": since, "now": now},
        "spent": spent,
        "saved": saved,
        "baseline": baseline.as_dict(),
        "daily": _every_day(daily, since, now),
        "task_types": task_types[:20],
        "next_savings": next_savings,
        "automations": sorted(({**a, "usd": round(a["usd"], 4)} for a in by_automation.values()),
                              key=lambda a: -a["usd"])[:10],
        "browser": {"calls": len(browser), "sent_tokens": sum(b["sent"] for b in browser) // CHARS_PER_TOKEN,
                    "page_tokens": sum(b["page"] for b in browser) // CHARS_PER_TOKEN,
                    "by_method": sorted(by_method.values(), key=lambda m: -m["calls"])},
        "top_turns": [_turn(t) for t in top_turns],
        "recent": [_turn(t) for t in recent],
        "history_from": all_turns[0]["ts"] if all_turns else None,
    }


def _every_day(daily: dict[str, dict[str, Any]], since: float, now: float) -> list[dict[str, Any]]:
    """One entry per calendar day, quiet days included, so a chart's spacing is time and
    not just the days something happened. Starts at the window (or the first recorded day)."""
    if not daily:
        return []
    start = datetime.strptime(min(daily), "%Y-%m-%d") if not since else datetime.fromtimestamp(since)
    start = max(start, datetime.strptime(min(daily), "%Y-%m-%d"))
    out, day = [], start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day.timestamp() <= now:
        key = day.strftime("%Y-%m-%d")
        out.append(daily.get(key) or {"day": key, "spent_usd": 0.0, "saved_usd": 0.0, "claude": 0, "antigravity": 0,
                                      "free_model": 0, "scripted": 0, "tokens": 0})
        day += timedelta(days=1)
    return out


def _turn(t: dict[str, Any]) -> dict[str, Any]:
    return {"ts": t["ts"], "backend": t["backend"], "model": t["model"], "task_type": t["task_type"],
            "request": t["request"], "ok": bool(t["ok"]), "seconds": round(t["seconds"], 1),
            "tokens": normalize.total(t), "input": t["input"], "output": t["output"],
            "cache_read": t["cache_read"], "cache_write": t["cache_write"], "cost_usd": round(t["cost_usd"], 4),
            "delegated": bool(t["delegated"]), "escalated": bool(t["escalated"])}


# -- text, for `python main.py usage` -------------------------------------------------------
def _n(value: float) -> str:
    value = float(value)
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(value) >= size:
            return f"{value / size:.1f}{unit}"
    return f"{value:.0f}"


def text(report: dict[str, Any]) -> str:
    spent, saved, base = report["spent"], report["saved"], report["baseline"]
    days = report["window"]["days"]
    lines = [f"Usage, {'last ' + str(int(days)) + ' days' if days else 'all time'}", "=" * 46, "",
             "SPENT (measured)",
             f"  Claude, at list price   ${spent['cost_usd']:.2f}   (a subscription: this is plan usage, not a bill)",
             f"  tokens, all brains      {_n(spent['tokens'])}   paid brains {_n(spent['paid_tokens'])}, "
             f"free models {_n(spent['free_tokens'])}"]
    if spent["cache_hit"] is not None:
        lines.append(f"  Claude cache hit rate   {spent['cache_hit'] * 100:.0f}% of input came from cache")
    if spent["failed_usd"]:
        lines.append(f"  on failed turns         ${spent['failed_usd']:.2f}   ({spent['failed_turns']} turns that did not finish the job)")
    if spent["hidden_delegated_usd"]:
        lines.append(f"  of which delegated      ${spent['hidden_delegated_usd']:.2f}   (handed over by a free model)")
    lines += ["", f"  {'brain':<13}{'turns':>6}{'tokens':>9}{'avg tok':>9}{'cost':>9}"]
    for b in spent["by_backend"]:
        lines.append(f"  {b['backend']:<13}{b['turns']:>6}{_n(b['tokens']):>9}{_n(b['avg_tokens']):>9}"
                     f"{'free' if b['free'] else '$' + format(b['cost_usd'], '.2f'):>9}")
    lines += ["", "SAVED (estimated)",
              f"  likely ${saved['usd']:.2f}, at least ${saved['floor_usd']:.2f}   · "
              f"{_n(saved['tokens'])} tokens not spent on Claude"]
    if saved["without_paid_model"] is not None:
        lines.append(f"  {saved['without_paid_model'] * 100:.0f}% of {saved['requests']} requests never reached a paid brain")
    for s in saved["sources"]:
        if s["count"]:
            money = f"${s['usd']:.2f}" if s["source"] != "extension" else "      "
            lines.append(f"  {s['label']:<40}{s['count']:>5}  {money:>8}  {_n(s['tokens']):>7} tok")
    lines += ["", f"  method: a free-model or Antigravity turn is valued at the median Claude turn here",
              f"  (${base['median_usd']:.3f} from {base['samples']} turns; {base['task_types_priced']} task types "
              f"priced on their own; floor ${base['floor_usd']:.3f}).",
              f"  A scripted answer counts as a Claude turn only for kinds Claude has done here; otherwise",
              f"  as the free-model turn it replaced: $0, {_n(base['free_median_tokens'])} tokens."]
    if report["next_savings"]:
        lines += ["", "WHERE TO SAVE NEXT (repeated Claude work with no automation yet)"]
        for k in report["next_savings"]:
            lines.append(f"  {k['task_type']:<28}{k['claude_turns']:>3} turns  ${k['cost_usd']:.2f}")
    if report["top_turns"]:
        lines += ["", "MOST EXPENSIVE TURNS"]
        for t in report["top_turns"][:5]:
            when = datetime.fromtimestamp(t["ts"]).strftime("%d %b %H:%M")
            lines.append(f"  {when}  ${t['cost_usd']:.2f}  {_n(t['tokens']):>6}  {t['request'][:60]}")
    return "\n".join(lines)
