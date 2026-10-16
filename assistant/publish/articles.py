"""Portfolio articles for every project that goes on LinkedIn: a Story and a Technical piece.

The owner's rule: whatever is posted on LinkedIn also gets written up on the portfolio
(aakshantkumar.vercel.app). The Story is for the people the project serves; the Technical
piece is for engineers, and it is the one that must not sell the work short: it is built
from *every* doc page in the repo, then the code, and goes as deep as the project does.

A Claude Code session works in the portfolio repo (`[articles] portfolio`) on one project's
file, `lib/writing/<slug>.ts`, so jobs for different projects never touch the same file. It
checks the site builds, then stages a commit-and-push of the portfolio at the publishing
gate. Nothing is published until that push is approved; approving it redeploys the site.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

GITHUB = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


def brief(project_name: str, project_path: str | Path | None, portfolio: str | Path,
          post_text: str = "", repo_url: str = "", extra: str = "") -> str:
    s = slug(project_name)
    portfolio = Path(portfolio)
    target = portfolio / "lib" / "writing" / f"{s}.ts"
    where = (f"The project is at {project_path}." if project_path and Path(project_path).is_dir() else
             f"The project isn't on this machine: clone {repo_url or 'its GitHub repo'} into a temporary folder "
             "outside the portfolio and work from that.")
    post = f"\nThe LinkedIn post about it, for tone and for the facts already made public:\n---\n{post_text.strip()}\n---\n" \
        if post_text.strip() else ""
    return f"""Write (or rewrite) the two portfolio articles for my project "{project_name}": a Story piece and a Technical piece. {where}
You are working in my portfolio repo ({portfolio}). Its rules are in DESIGN.md, PRODUCT.md and AGENTS.md there: follow them.
{post}
How the portfolio holds articles
- Each project is one file, lib/writing/<slug>.ts, exporting an `Article` (the types are in lib/articles.ts). This one is {target} with slug "{s}". Register it in the `articles` array in lib/articles.ts if it isn't there.
- Blocks available: h, p, list, callout, image, flow and sequence diagrams (components/Diagram.tsx: square ink nodes on a grid, one `focus` node per figure, no arrow crossing a box), table, and code (a real excerpt from the repo with its source path). Inline `code` and **bold** work inside text.
- Images go in public/writing/{s}/. Use the project's own screenshots and figures where they exist; never a photo of a real person, never an evidence/ folder, never anything with secrets on it.
- Read lib/writing/ved-code.ts for the shape, but don't copy its depth: the existing pieces were too thin, and that is the problem this job fixes.

Research first, all of it
- Read EVERY documentation page in the project: README, everything under docs/, design notes, reports and PDFs, ADRs, CHANGELOG, comments that explain decisions. Then the code that matters: entry points, the main pipeline, the data model, configuration, tests. Use git log for how it evolved.
- Every fact, number and claim comes from the repo. No invented users, metrics or results. If something is unfinished, say so plainly.

The Technical piece: as detailed and explainable as the project deserves
- Written for engineers who want to understand how it really works, in clear language: define each idea when it first appears, then go deep.
- Cover, as far as the repo supports: the problem and constraints; the full architecture (a system diagram); every major component, what it does and why it was built that way; the data model; the main flows end to end (sequence diagrams); the key algorithms or models, with the actual parameters; configuration and deployment; testing and how quality is measured, with the real numbers; security and privacy; performance; the failures, bugs and dead ends and what they taught; trade-offs and alternatives considered; what is still open and what comes next.
- Use code blocks for the few excerpts that explain the design best (trim them, keep the source path). Use tables for comparisons and results.
- Pace it with a figure or table every few paragraphs, each introduced in the text before it, each with a takeaway. Expect several thousand words for a substantial project; length follows substance, never padding.

The Story piece
- For the people the project is for: who has the problem, what it is like, what changes with this, why it matters. Honest about where it stands. Plain language, a few strong figures or screenshots.

Finish
- Run `npx tsc --noEmit` and `npm run build` in the portfolio and fix anything that breaks. A piece that doesn't build isn't finished.
- Then stage the portfolio push for my approval (this does not publish anything):
  python C:\\Assisstant\\main.py linkedin articles-ready "{project_name}"
- Do NOT commit, push or deploy yourself.
{extra}
When you're done, reply in one or two spoken sentences: what the two pieces cover and roughly how long the technical one is."""


def repo_url(text: str) -> str:
    """The project's GitHub link, from a post or a README."""
    m = GITHUB.search(text or "")
    return m.group(0) if m else ""


def stage(publisher: Any, portfolio: str | Path, project_name: str) -> str:
    """The push of the portfolio, waiting at the gate. What Nova says about it."""
    portfolio = Path(portfolio)
    if not (portfolio / ".git").exists():
        return f"The portfolio at {portfolio} isn't a git repo, so there's nothing to push."
    draft = publisher.gate.stage("github_push", f"Writing: {project_name}, the story and the technical piece",
                                 target=str(portfolio),
                                 extra={"name": f"{project_name} articles", "articles": slug(project_name)})
    return (f"The {project_name} articles are written and waiting in Studio. Approving pushes the "
            f"portfolio, which redeploys the site. Draft {draft['id']}.")
