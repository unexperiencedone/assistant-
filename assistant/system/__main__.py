"""Command-line access to the local index, built for agents and scripts.

Output is deliberately terse (one result per line, no headers) so an agent
spends as few tokens as possible reading it.
Exit codes: 0 = success/found, 1 = nothing found, 2 = error.

    python sysindex.py find "invoice" --kind file --ext pdf --limit 5
    python sysindex.py open "visual studio code"
    python sysindex.py dupes "%USERPROFILE%\\Downloads" --min-size 1MB
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..config import load_settings
from .duplicates import format_groups, human_size, parse_size
from .service import LocalSystem


def _system(cmd: str) -> LocalSystem:
    settings = load_settings()
    system = LocalSystem(settings.local, settings.base_dir, log=lambda text, level="info": print(text, file=sys.stderr))
    if cmd in ("find", "open", "top") and not system.db.get_meta("apps_indexed_at"):
        # Fresh install: build the index once instead of answering "not found".
        print("Building the index for the first time...", file=sys.stderr)
        system.reindex_apps()
        system.reindex_files(full=True)
    return system


def _row(hit, verbose: bool) -> str:
    if not verbose:
        return f"{hit.kind}\t{hit.path}"
    size = "" if hit.size is None else human_size(hit.size)
    return f"{hit.kind}\t{hit.score:.0f}\t{size}\t{hit.path}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sysindex", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("find", help="ranked name search over indexed apps, files and folders")
    p.add_argument("query")
    p.add_argument("--kind", choices=["app", "file", "folder"])
    p.add_argument("--ext", help="file extension filter, e.g. pdf")
    p.add_argument("--under", help="only results inside this folder")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("-v", "--verbose", action="store_true", help="include score and size")

    p = sub.add_parser("open", help="open the best match and remember the choice")
    p.add_argument("query")
    p.add_argument("--kind", choices=["app", "file", "folder"])
    p.add_argument("--dry-run", action="store_true", help="print what would open without opening")

    p = sub.add_parser("reveal", help="show a path selected in File Explorer")
    p.add_argument("path")

    p = sub.add_parser("used", help="record that a path was used (improves ranking)")
    p.add_argument("path")

    p = sub.add_parser("top", help="most used items by frecency")
    p.add_argument("--kind", choices=["app", "file", "folder"])
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("dupes", help="find duplicate files under a folder (never deletes)")
    p.add_argument("root")
    p.add_argument("--min-size", default="1KB")
    p.add_argument("--limit", type=int, default=15, help="groups to print")
    p.add_argument("--fresh", action="store_true", help="walk the disk instead of using the index")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("index", help="refresh the index now")
    p.add_argument("--full", action="store_true", help="re-list every folder and prune")
    p.add_argument("--apps-only", action="store_true")

    sub.add_parser("stats", help="index size and freshness")

    args = parser.parse_args(argv)
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    system = _system(args.cmd)

    if args.cmd == "find":
        hits = system.find(args.query, kind=args.kind, ext=args.ext, under=args.under, limit=args.limit)
        print("\n".join(_row(h, args.verbose) for h in hits) or "not found")
        return 0 if hits else 1

    if args.cmd == "open":
        result = system.open(args.query, kind=args.kind, dry_run=args.dry_run)
        if result.ok and result.hit:
            print(f"{'would open' if args.dry_run else 'opened'}\t{result.hit.kind}\t{result.hit.path}")
            return 0
        print(result.message)
        for alt in result.alternatives[:3]:
            print(f"maybe\t{alt.kind}\t{alt.path}")
        return 1

    if args.cmd == "reveal":
        if not os.path.exists(args.path):
            print("not found")
            return 1
        system.reveal(args.path)
        system.record_path(args.path)
        print("revealed")
        return 0

    if args.cmd == "used":
        print("recorded" if system.record_path(args.path) else "not found")
        return 0

    if args.cmd == "top":
        hits = system.top(args.kind, args.limit)
        print("\n".join(f"{h.kind}\t{h.frecency:.2f}\t{h.path}" for h in hits) or "no usage yet")
        return 0 if hits else 1

    if args.cmd == "dupes":
        root = os.path.expandvars(args.root)
        if not os.path.isdir(root):
            print(f"not a folder: {root}")
            return 2
        groups = system.duplicates(root, min_size=parse_size(args.min_size), fresh=args.fresh)
        if args.json:
            print(json.dumps([{"size": g.size, "paths": g.paths} for g in groups[: args.limit]]))
        else:
            print(format_groups(groups, args.limit, system.duplicate_finder.skipped))
        return 0 if groups else 1

    if args.cmd == "index":
        print(f"{system.reindex_apps()} apps")
        if not args.apps_only:
            print(system.reindex_files(full=args.full).summary())
        return 0

    if args.cmd == "stats":
        for key, value in system.stats().items():
            print(f"{key}\t{value}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
