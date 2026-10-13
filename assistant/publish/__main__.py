"""`python main.py linkedin ...`: sign in, see what's unposted, draft, keep the ledger.

    auth                 sign in through the browser (once, then every ~60 days)
    status               is the sign-in good, and how long for
    scan                 every project found, and whether it's posted
    draft [project]      stage a post for the newest unposted project (or the named one)
    seed <Shares.csv>    mark projects already posted, from LinkedIn's data export
    mark <project>       mark one as already posted (by hand)
    skip <project>       never post this one
    unmark <project>     forget it, so it can be posted
    ledger               what the ledger holds
    pending              drafts waiting at the gate
    approve [id]         send a waiting draft (the same as saying "post it")
    drop [id]            discard one

Drafts staged here land in the same SQLite file the running app reads, so "post it"
by voice works on a draft made from the terminal, and the other way round.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import load_settings
from . import linkedin, linkedin_auth, projects


def _services(with_writer: bool = False):
    from .service import PublishService
    from .showcase import Showcase, free_writer

    settings = load_settings()
    db = Path(settings.publish.db_path).expanduser()
    publisher = PublishService(db if db.is_absolute() else settings.base_dir / db)
    ask = None
    if with_writer:
        from ..agents.registry import AgentRegistry

        ask = free_writer(AgentRegistry(settings))
    showcase = Showcase(settings.showcase, publisher, settings.base_dir, ask=ask)
    publisher.showcase = showcase
    return publisher, showcase


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    command = argv[0] if argv else "status"
    rest = " ".join(argv[1:]).strip()

    if command == "auth":
        load_settings()                      # reads .env
        ok, detail = linkedin_auth.sign_in()
        print(detail)
        if ok:
            ok, who = linkedin.member_urn()
            print(f"Posting as {who}." if ok else who)
        return 0 if ok else 1

    if command == "status":
        load_settings()
        ok, detail = linkedin.check()
        print(detail)
        return 0 if ok else 1

    if command in ("approve", "drop", "pending"):
        publisher, _ = _services()
        if command == "pending":
            for draft in publisher.gate.pending(20):
                print(f"[{draft['id']}] {draft['kind']} {len(draft['media'])} image(s)\n{draft['text']}\n")
            print(publisher.waiting())
        elif command == "approve":
            print(publisher.approve(int(rest) if rest.isdigit() else None))
        else:
            print(publisher.discard(int(rest) if rest.isdigit() else None))
        publisher.close()
        return 0

    publisher, showcase = _services(with_writer=command == "draft")
    try:
        if command == "scan":
            covered = showcase.ledger.load()
            for project in showcase.scan():
                ok, why = projects.worth_posting(project)
                entry = covered.get(project.key)
                mark = entry["status"] if entry else ("unposted" if ok else f"skipped: {why}")
                print(f"{project.name:32} {mark:28} {project.path}")
        elif command == "draft":
            print(showcase.draft(rest))
            for draft in publisher.gate.pending(50)[-1:]:
                print("\n" + draft["text"])
                for path in draft["media"]:
                    print(f"  image: {path}")
        elif command == "seed":
            path = Path(rest.strip('"'))
            if not path.is_file():
                print(f"No file at {path}. In the export it's called Shares.csv.")
                return 1
            marked = showcase.ledger.seed_from_export(path, showcase.scan())
            print(f"Marked {len(marked)} as already posted: {', '.join(marked) or 'none'}")
        elif command in ("mark", "skip", "unmark"):
            project = showcase.find(rest)
            if project is None:
                print(f"No project called {rest!r} under your roots.")
                return 1
            if command == "unmark":
                print(("Forgot " if showcase.ledger.forget(project.key) else "Wasn't marked: ") + project.name)
            else:
                showcase.ledger.record(project.key, project.name, project.path, source="manual",
                                       head=project.head, status="posted" if command == "mark" else "skip")
                print(f"{project.name}: {'already posted' if command == 'mark' else 'never post'}.")
        elif command == "ledger":
            for entry in showcase.ledger.entries():
                print(f"{entry['name']:32} {entry['status']:8} {entry['source']:7} {entry.get('post', '')}")
        else:
            print(__doc__)
            return 2
    finally:
        publisher.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
