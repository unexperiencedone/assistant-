"""`python main.py memory ...`: AfterThought from a shell, for Claude sessions and you.

    memory search "payments rollout" [--limit 5]
    memory remember "Acme rollout moved to Oct 15"
    memory status
    memory watch Chrome | memory unwatch Chrome
    memory forget <id>
    memory consolidate

Search loads the embedding model (a few seconds the first time). It reads the same
SQLite file the running Nova writes, so what you add here is there for Nova too.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from ..config import load_settings
from ..events import EventBus
from .service import AfterThoughtService


def _service() -> AfterThoughtService:
    settings = load_settings()
    path = Path(os.path.expandvars(settings.afterthought.db_path)).expanduser()
    return AfterThoughtService(path if path.is_absolute() else settings.base_dir / path, EventBus(),
                               settings.afterthought)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="memory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    keep = sub.add_parser("remember")
    keep.add_argument("text")
    sub.add_parser("status")
    for name in ("watch", "unwatch"):
        sub.add_parser(name).add_argument("app")
    sub.add_parser("forget").add_argument("id", type=int)
    sub.add_parser("consolidate")
    args = parser.parse_args(argv)

    service = _service()
    try:
        if args.cmd == "search":
            hits = service.recall(args.query, args.limit)
            if not hits:
                print("no memory")
                return 1
            for h in hits:
                m, src = h["memory"], (h["memory"].get("sources") or [{}])[0]
                print(f"#{m['id']}\t{m['tier']}\t{h['similarity']:.2f}\t{datetime.fromtimestamp(m['start']):%Y-%m-%d %H:%M}"
                      f"\t{src.get('app') or m['app']}: {src.get('title') or m['title']}\t{h['focus']}")
        elif args.cmd == "remember":
            print(service.remember_now(args.text))
        elif args.cmd == "status":
            counts = service.store.counts()
            print(f"watching\t{', '.join(service.consent()) or '(nothing)'}")
            print(f"short\t{counts['short']}\nlong\t{counts['long']}\npaused\t{service.paused()}")
        elif args.cmd in ("watch", "unwatch"):
            print(f"{args.cmd}\t{service.set_consent(args.app, args.cmd == 'watch')}")
        elif args.cmd == "forget":
            print(service.forget(args.id))
        else:
            print(service.consolidate())
        return 0
    finally:
        service.store.close()


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
