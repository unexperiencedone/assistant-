"""`python main.py voice-check` -- ask the current backend the probes and score the replies.

Run it after switching backends, after editing character.md, or every few weeks. It
costs one short turn per probe, which is why it is a command you run rather than
something that happens on a timer behind your back.
"""

from __future__ import annotations

import argparse
import sys
import threading

from ..paths import APP_DIR
from . import drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voice-check", description=__doc__)
    parser.add_argument("--backend", default="", help="which agent answers (default: the configured one)")
    parser.add_argument("--quiet", action="store_true", help="just the score line")
    args = parser.parse_args(argv or [])

    from ..agents import AgentRegistry
    from ..config import load_settings

    settings = load_settings()
    registry = AgentRegistry(settings)
    name = args.backend or settings.agents.default
    backend = registry.backends.get(name)
    if backend is None:
        print(f"There is no backend called {name}. I have: {', '.join(registry.backends)}")
        return 2
    if not backend.is_available():
        print(f"{name} isn't available on this machine right now, so there is nothing to check.")
        return 2

    def ask(question: str) -> str:
        result = backend.spawn().run(question, lambda event: None, threading.Event())
        return result.summary if result.ok else ""

    run = drift.check(ask)
    comparison = drift.record(APP_DIR / "data" / "drift.json", run)
    print(drift.spoken(run, comparison))
    if not args.quiet:
        for result in run["results"]:
            mark = "ok  " if not result["faults"] else "drift"
            print(f"\n[{mark}] {result['ask']}")
            print(f"        {result['reply'][:300] or '(nothing)'}")
            for fault in result["faults"]:
                print(f"        -> {fault}")
    registry.close()
    return 0 if run["score"] == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
