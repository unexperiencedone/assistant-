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
    schedule <id> <when> approve a draft to go out later ("thursday 10am", "tomorrow 9:30am")
    unschedule <id>      put it back to waiting
    scheduled            what's lined up
    due                  send whatever is due now (Nova does this by itself while running)
    edit <urn> <file>    replace a live post's text with the contents of a file (images stay)
    drop [id]            discard one
    articles <project>   write the portfolio Story + Technical pieces (Claude Code, long)
    articles-brief <p>   print that job's brief without running it
    articles-ready <p>   stage the portfolio push for approval (the article job runs this)

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

    if command == "edit":
        load_settings()
        urn, _, file = rest.partition(" ")
        path = Path(file.strip().strip('"'))
        if not urn.startswith("urn:li:") or not path.is_file():
            print("Usage: edit <urn:li:...> <text file>")
            return 2
        ok, detail = linkedin.update_text(urn, path.read_text(encoding="utf-8").strip())
        print(detail)
        return 0 if ok else 1

    if command in ("schedule", "unschedule", "scheduled", "due"):
        publisher, _ = _services()
        try:
            if command == "schedule":
                draft_id, _, when = rest.partition(" ")
                print(publisher.schedule(int(draft_id), when.strip().strip('"')) if draft_id.isdigit()
                      else "Usage: schedule <draft id> <when>")
            elif command == "unschedule":
                print(publisher.gate.unschedule(int(rest))[1] if rest.isdigit() else "Usage: unschedule <id>")
            elif command == "scheduled":
                import time as _time

                for draft in publisher.gate.scheduled():
                    when = _time.strftime("%a %d %b %H:%M", _time.localtime(draft["send_at"]))
                    print(f"[{draft['id']}] {when}  {(draft['extra'] or {}).get('name', '')}  "
                          f"{len(draft['media'])} image(s)")
            else:
                print("\n".join(publisher.send_due()) or "Nothing is due.")
        finally:
            publisher.close()
        return 0

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

    if command in ("articles", "articles-brief", "articles-ready"):
        return _articles(command, rest)

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


def _articles(command: str, rest: str) -> int:
    """Portfolio articles for a project (publish/articles.py)."""
    import threading

    from . import articles, video

    settings = load_settings()
    publisher, showcase = _services()
    try:
        name = rest.strip().strip('"')
        if command == "articles-ready":
            print(articles.stage(publisher, settings.articles.portfolio, name))
            return 0
        found = video.project_for(showcase, name)
        # A project already posted or drafted carries its post text: the facts made public.
        posts = [d for d in publisher.gate.recent(60) + publisher.gate.pending(50) + publisher.gate.scheduled()
                 if d["kind"] == "linkedin_post" and (d.get("extra") or {}).get("name", "").lower() == name.lower()]
        post_text = posts[0]["text"] if posts else ""
        path = found.path if found is not None else None
        display = found.name if found is not None else name
        repo = articles.repo_url(post_text)
        if found is None and not repo:
            print(f"I couldn't find {name!r} on disk or a GitHub link for it in a post.")
            return 1
        prompt = articles.brief(display, path, settings.articles.portfolio, post_text, repo)
        if command == "articles-brief":
            print(prompt)
            return 0
        from ..agents.claude_cli import ClaudeCliAgent

        agent = ClaudeCliAgent(settings.agents.claude, Path(settings.articles.portfolio), False, "")
        print(f"Writing the {display} articles in {settings.articles.portfolio}. This takes a while.", flush=True)
        result = agent.run(prompt, lambda e: print(f"  {e.kind}: {' '.join(e.text.split())[:140]}", flush=True)
                           if e.kind in ("tool", "text") else None, threading.Event())
        agent.close()
        print(result.summary or result.detail)
        return 0 if result.ok else 1
    finally:
        publisher.close()

