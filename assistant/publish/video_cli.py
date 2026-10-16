"""`python main.py youtube ...`: explainer videos of your projects, and the YouTube gate.

    make <project>       have Claude Code make the video in the Remotion studio (long:
                         script, narration, render), then stage it for approval
    brief <project>      print the brief that job gets, without running anything
    stage <youtube.json> stage a finished video's package (the video job does this itself)
    pending              YouTube drafts waiting for you

Approving ("post it", or Post now in Studio) uploads through YouTube Studio in your
Chrome; the Nova extension has to be connected, which means Nova has to be running.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from ..config import load_settings
from . import video


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    command = argv[0] if argv else "pending"
    rest = " ".join(argv[1:]).strip().strip('"')

    from .__main__ import _services

    settings = load_settings()
    publisher, showcase = _services()
    try:
        if command == "stage":
            if not rest:
                print("usage: youtube stage <path to youtube.json>")
                return 2
            print(publisher.draft_youtube(rest))
            return 0
        if command == "pending":
            drafts = [d for d in publisher.gate.pending(50) if d["kind"] == "youtube_video"]
            for d in drafts:
                print(f"[{d['id']}] {d['extra'].get('title')}  ({d['media'][0]})")
            print(f"{len(drafts)} video(s) waiting." if drafts else "No videos waiting.")
            return 0
        if command in ("make", "brief"):
            project = video.project_for(showcase, rest)
            if project is None:
                print(f"I couldn't find a project called {rest!r} in the showcase roots.")
                return 1
            prompt = video.brief(project.name, project.path, settings.video.folder, settings.video.render_concurrency)
            if command == "brief":
                print(prompt)
                return 0
            from ..agents.claude_cli import ClaudeCliAgent

            agent = ClaudeCliAgent(settings.agents.claude, Path(settings.video.folder), False, "")
            print(f"Making a video about {project.name} in {settings.video.folder}. This takes a while.")
            result = agent.run(prompt, lambda e: print(f"  {e.kind}: {e.text[:120]}") if e.kind == "tool" else None,
                               threading.Event())
            agent.close()
            print(result.summary or result.detail)
            return 0 if result.ok else 1
        print(__doc__)
        return 2
    finally:
        publisher.close()
