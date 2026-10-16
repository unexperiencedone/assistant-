"""Explainer videos of the owner's projects, made by Claude Code in the Remotion studio.

The studio (`[video] folder`, C:\\video-comp) already holds the craft: the remotion-*
skills, impeccable for the look, Fish narration aligned by whisper.cpp, and finished
films (AirGated, Nova) to learn the structure from. Nova's part is the brief and the
hand-off: one `claude -p` session working *in that folder* on one project, run through
the task pool so it gets a lane on the canvas and narrates its milestones like any job.

The session ends by staging the result itself (`python main.py youtube stage ...`), so
the video lands in Studio as a draft: nothing is uploaded until the owner says so.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PACKAGE_SCHEMA = """{
  "project": "<project name>",
  "video": "<absolute path to the .mp4>",
  "thumbnail": "<absolute path to a 1280x720 .png>",
  "title": "<under 100 characters, no < or >, honest, no clickbait>",
  "description": "<what it is, how it works in plain words, what's unfinished, the repo link if public, one question for viewers>",
  "chapters": [["00:00", "<first chapter>"], ["00:27", "<next>"]],
  "tags": ["<10-15 tags>"],
  "pinned_comment": "<one honest question for viewers>",
  "visibility": "public"
}"""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


def brief(project_name: str, project_path: str | Path, studio: str | Path, concurrency: int = 3,
          extra: str = "") -> str:
    """The whole job, in one prompt, for a Claude session whose working folder is the studio."""
    s = slug(project_name)
    out = Path(studio) / "out" / s
    pascal = "".join(part.capitalize() for part in s.split("-")) or "Project"
    return f"""Make a YouTube explainer video about my project "{project_name}" ({project_path}), then stage it for my approval.

What the video is
- An informative explainer of the project's architecture and how it works, in technical terms that anyone can follow: plain words first, the technical detail one layer down.
- 4 to 6 minutes, 1920x1080, 30 fps, narrated.
- Learn the structure and craft from the films already in this studio (src/airgated, src/nova and their scripts, PRODUCT.md, DESIGN.md) and use the skills installed for this: remotion-best-practices and the other remotion-* skills for the build, impeccable for the look. No generic "AI" colours: give the project its own visual identity.
- Story: the problem, the architecture (a clear diagram built up step by step), each important component and the data flow, the hard part or the bug that taught something, what's honestly unfinished, what's next.

Facts and honesty
- Read the project itself first: README, docs, reports, and the code where it matters. Every claim and number on screen comes from the project; never invent figures, users or results.
- No screen recordings exist unless the project has them; anything recreated is shown as an illustration, never passed off as real footage.
- Never show secrets: no keys, tokens, .env contents, passwords or personal data.
- If the project has an evidence/ folder or photos of real people (for example student faces), never use them.

How to build it
- Put the code in src/{s}/ with a composition "{pascal}Explained", registered in src/Root.tsx beside the others. Don't change the existing films.
- Narration: write the script as src/{s}/script.json and generate it the way the others were made: node --env-file=.env scripts/generate-voiceover.ts --script src/{s}/script.json --out public/voiceover/{s}
- Check free RAM before rendering and render with --concurrency={concurrency} or lower: npx remotion render {pascal}Explained "{out / (s + '.mp4')}" --crf=18 --concurrency={concurrency}
- Render a 1280x720 thumbnail still to "{out / 'thumbnail.png'}" (a {pascal}Thumbnail composition).
- Watch for render errors and fix them; a video that doesn't render isn't finished.

The YouTube package
- Write "{out / 'youtube.json'}" in exactly this shape (absolute paths, chapters matching the real scene times):
{PACKAGE_SCHEMA}
- Then stage it, which puts it in Nova's Studio for me to watch and approve: python C:\\Assisstant\\main.py youtube stage "{out / 'youtube.json'}"
- Do NOT upload or publish anything anywhere yourself.
{extra}
When you're done, reply in one or two spoken sentences: what the video covers, how long it is, and that it's waiting in Studio."""


def project_for(showcase: Any, name: str):
    """The project folder for a spoken name, through the same lookup the LinkedIn showcase uses
    (it already skips the folders that must never be shown)."""
    if showcase is None or not name:
        return None
    found = showcase.find(name)
    if found is not None:
        return found
    # "airgated" for AirGated, "civic pulse" for CivicPulse: compare without spaces.
    squash = lambda text: re.sub(r"[^a-z0-9]", "", text.lower())
    wanted = squash(name)
    return next((p for p in showcase.scan() if wanted and squash(p.name) == wanted), None)
