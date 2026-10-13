"""Showcase: your unposted projects, turned into LinkedIn drafts one at a time.

    scan the whitelisted roots  ->  diff against the ledger  ->  pick one
    -> write the post (a free model phrases the story; the Built / Partial / Future
       lists come straight from the repo)  ->  stage it at the gate with its screenshots
    -> you say "post it"  ->  LinkedIn  ->  the ledger records it

Nothing here sends. The gate does, and only for the draft you approved.

Pacing is done at drafting time, not at sending time: the background check only drafts
when nothing is already waiting and the last post is at least `min_gap_days` old, so
approving the moment a draft appears still spaces posts out. Asking for one by voice
drafts it regardless, and says when the last post was.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import projects
from .ledger import Ledger
from .projects import Project

GHOSTWRITER = (
    "You are ghostwriting a LinkedIn post for a developer, in their own voice: first person, "
    "plain, specific, no hype, no emojis, no hashtags. Use ONLY the facts given. If a fact is "
    "not given, do not mention it. Never say a feature exists unless it is in the facts.\n"
    "Answer in exactly this shape and nothing else:\n"
    "HOOK: <one line that makes a developer want to read on>\n"
    "WHAT: <2-3 sentences: what it is and who it is for>\n"
    "HOW: <2-3 sentences: how it was built, naming the real stack>\n"
    "BUGS: <1-3 short lines, each starting with '- ', about problems hit and fixed, taken only "
    "from the commit list; write NONE if the list is empty>\n"
    "If a STATUS DOC is given, also add these three, each 2-5 lines starting with '- ', taken ONLY "
    "from the status doc and written so a reader understands them:\n"
    "WORKING: <features the doc says really work end to end>\n"
    "SHOWONLY: <features the doc says are UI only, fake data, unwired or dead>\n"
    "NEXT: <what the doc says is planned or recommended next>"
)
FIELD = re.compile(r"^(HOOK|WHAT|HOW|BUGS|WORKING|SHOWONLY|NEXT)\s*:\s*(.*)$", re.I)
LISTS = ("bugs", "working", "showonly", "next")
NOT_WORDS = {"js", "py", "app", "src", "main", "core", "utils"}


class Showcase:
    def __init__(self, settings: Any, publisher: Any, base_dir: Path, bus: Any = None,
                 ask: Callable[[str], str] | None = None, notify: Callable[[str], None] | None = None) -> None:
        self.settings = settings
        self.publisher = publisher
        self.bus = bus
        self.ask = ask
        self.notify = notify
        path = Path(settings.ledger_path).expanduser()
        self.ledger = Ledger(path if path.is_absolute() else base_dir / path)
        self.base_dir = Path(base_dir)
        self._owner = projects.owner_emails()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = threading.Lock()
        if publisher is not None:
            publisher.after_send.append(self._on_sent)

    # -- the background check -----------------------------------------------------------
    def start(self) -> None:
        if not self.settings.auto_draft or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="showcase", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(120):          # let startup finish before scanning disks
            return
        while not self._stop.is_set():
            try:
                said = self.auto()
                if said and self.notify:
                    self.notify(said)
            except Exception as error:   # a scan must never take the assistant down
                if self.bus is not None:
                    self.bus.log(f"Project showcase check failed: {error}")
            self._stop.wait(max(1.0, float(self.settings.check_hours)) * 3600)

    def auto(self) -> str:
        """Draft the next project if it's time. "" when it isn't."""
        if self._waiting():
            return ""
        gap = self.ledger.days_since_last()
        if gap is not None and gap < float(self.settings.min_gap_days):
            return ""
        if not self._signed_in():
            return ""
        candidates = self.unposted()
        if not candidates:
            return ""
        return self._draft(candidates[0], automatic=True)

    # -- scanning -----------------------------------------------------------------------
    def scan(self) -> list[Project]:
        folders = projects.discover(list(self.settings.roots), list(self.settings.ignore),
                                    int(self.settings.depth))
        return [projects.read(folder, self._owner) for folder in folders]

    def unposted(self) -> list[Project]:
        """Projects worth a post that the ledger has never seen, newest work first."""
        covered = self.ledger.load()
        pending = {d.get("extra", {}).get("project") for d in self._pending()}
        fresh = [p for p in self.scan()
                 if p.key not in covered and p.key not in pending and projects.worth_posting(p)[0]]
        return sorted(fresh, key=lambda p: (p.last_commit or "", p.commits), reverse=True)

    def find(self, name: str) -> Project | None:
        wanted = _norm(name)
        if not wanted:
            return None
        scanned = self.scan()
        for project in scanned:
            if _norm(project.name) == wanted or _norm(Path(project.path).name) == wanted:
                return project
        words = set(wanted.split()) - NOT_WORDS
        best = max(scanned, default=None,
                   key=lambda p: len(words & set(_norm(p.name).split())))
        return best if best and words & set(_norm(best.name).split()) else None

    # -- drafting -----------------------------------------------------------------------
    def draft(self, name: str = "") -> str:
        """Voice and tool entry point: one project, staged for approval. Returns what to say."""
        if self.publisher is None:
            return "Publishing is turned off in the config."
        if not self.settings.roots:
            return "I don't know where your projects are yet: add folders to roots under [showcase] in config.toml."
        if not self._busy.acquire(blocking=False):
            return "I'm already putting a project post together."
        try:
            if name:
                project = self.find(name)
                if project is None:
                    return f"I couldn't find a project called {name} in your project folders."
                ok, why = projects.worth_posting(project)
                if not ok:
                    return f"I'd rather not post {project.name}: {why}."
                if self.ledger.covered(project.key):
                    return f"{project.name} is already on LinkedIn, by my records."
            else:
                waiting = self._pending()
                if waiting:
                    return (f"There's already a project post waiting for you, about "
                            f"{waiting[0].get('extra', {}).get('name', 'a project')}. Say post it, or drop it.")
                candidates = self.unposted()
                if not candidates:
                    return "Every project I can see is already on LinkedIn, or not one I should post."
                project = candidates[0]
            said = self._draft(project)
            gap = self.ledger.days_since_last()
            if gap is not None and gap < float(self.settings.min_gap_days):
                said += f" Your last post was {gap:.0f} days ago, so I'd wait a little before posting it."
            if not self._signed_in():
                said += " LinkedIn isn't signed in yet, so it can't go out until you run: python main.py linkedin auth."
            return said
        finally:
            self._busy.release()

    def _draft(self, project: Project, automatic: bool = False) -> str:
        projects.credit(project)
        text = compose(project, self._phrase(project), hashtags=bool(self.settings.hashtags))
        images = project.images[: int(self.settings.max_images)]
        shots = ""
        if not images and getattr(self.settings, "capture", True):
            images, shots = self._capture(project)
        draft = self.publisher.gate.stage(
            "linkedin_post", text, target="your feed", media=images,
            extra={"project": project.key, "name": project.name, "path": project.path,
                   "head": project.head, "alt": f"Screenshot of {project.name}"})
        if self.bus is not None:
            self.bus.log(f"Drafted a LinkedIn post about {project.name}"
                         f"{' on my own' if automatic else ''}; waiting on you.")
        team = (f" It's a team repo ({project.contributors} people, {project.share:.0%} of the commits are "
                f"yours), so I wrote it as 'we' -- check they're fine with it being posted."
                if project.contributors > 1 else "")
        pictures = (f" with {len(images)} screenshot{'s' if len(images) != 1 else ''} from the repo -- "
                    "glance at them for anything private first" if images else "")
        shots = f" {shots}" if shots else ""
        return (f"Drafted a LinkedIn post about {project.name}{pictures}.{shots}{team} Nothing has gone out. "
                f"Say post it, or drop it. (Draft {draft['id']}.)")

    def _capture(self, project: Project) -> tuple[list[str], str]:
        from . import capture

        try:
            return capture.capture(project, self.base_dir / "data" / "showcase",
                                   dict(getattr(self.settings, "demos", {}) or {}),
                                   limit=int(self.settings.max_images),
                                   run_locally=bool(getattr(self.settings, "run_locally", True)),
                                   install=bool(getattr(self.settings, "install_deps", True)),
                                   logins=dict(getattr(self.settings, "logins", {}) or {}))
        except Exception as error:   # no pictures is a worse post, not a failed one
            return [], f"I couldn't take screenshots: {str(error).splitlines()[0][:120]}"

    def _phrase(self, project: Project) -> dict[str, Any]:
        """The story half, from a free model. Empty fields fall back to the plain template."""
        if self.ask is None:
            return {}
        try:
            answer = self.ask(f"{GHOSTWRITER}\n\nFACTS:\n{facts_block(project)}") or ""
        except Exception:
            return {}
        return parse(answer, project)

    # -- after LinkedIn says yes --------------------------------------------------------
    def _on_sent(self, draft: dict[str, Any], detail: str) -> None:
        extra = draft.get("extra") or {}
        if draft.get("kind") != "linkedin_post" or not extra.get("project"):
            return
        found = re.search(r"\((urn:[^)]+)\)", detail or "")
        self.ledger.record(extra["project"], extra.get("name", ""), extra.get("path", ""),
                           source="api", post=found.group(1) if found else "", head=extra.get("head", ""))

    # -- small helpers ------------------------------------------------------------------
    def _pending(self) -> list[dict[str, Any]]:
        return [d for d in self.publisher.gate.pending(50)
                if d["kind"] == "linkedin_post" and (d.get("extra") or {}).get("project")]

    def _waiting(self) -> bool:
        return bool(self._pending())

    def _signed_in(self) -> bool:
        from . import linkedin

        return linkedin.configured()

    def spoken_unposted(self) -> str:
        fresh = self.unposted()
        if not fresh:
            return "Every project I can see is already on LinkedIn, or not one I should post."
        names = ", ".join(p.name for p in fresh[:5])
        more = f" and {len(fresh) - 5} more" if len(fresh) > 5 else ""
        return f"{len(fresh)} project{'s' if len(fresh) != 1 else ''} not on LinkedIn yet: {names}{more}."

    def state(self) -> dict[str, Any]:
        return {"posted": len(self.ledger.load()), "last_days": self.ledger.days_since_last(),
                "pending": len(self._pending())}


# -- the post itself -----------------------------------------------------------------
def facts_block(project: Project) -> str:
    lines = [f"Name: {project.name}"]
    if project.description:
        lines.append(f"Description: {project.description}")
    if project.languages:
        lines.append(f"Languages: {', '.join(project.languages)}")
    if project.stack:
        lines.append(f"Main libraries (name at most three): {', '.join(project.stack[:6])}")
    if project.my_part:
        part = project.my_part
        lines.append(f"The author's own part: {part.get('commits', 0)} of {project.commits} commits, mostly in "
                     f"{', '.join(part.get('areas') or ['the project'])}; e.g. {'; '.join(part.get('examples') or [])}")
    for person in project.team:
        lines.append(f"Collaborator {person['name']}: {person['commits']} commits, mostly in "
                     f"{', '.join(person.get('areas') or ['the project'])}; e.g. {'; '.join(person.get('examples') or [])}")
    if project.contributors > 1:
        lines.append(f"Team: built with {project.contributors - 1} other main contributor(s). Write 'we' for the "
                     "project as a whole, say which part was the author's, and thank the collaborators by name.")
    if project.commits:
        lines.append(f"Commits: {project.commits}, from {project.first_commit} to {project.last_commit}")
    if project.built:
        lines.append("Working features: " + "; ".join(project.built))
    lines.append("Bug-fix commits: " + ("; ".join(project.bugs) if project.bugs else "(none)"))
    if project.status_doc:
        lines.append(f"STATUS DOC (the team's own audit; trust it over the README):\n{project.status_doc}")
    if project.readme:
        lines.append(f"README excerpt:\n{project.readme[:1500]}")
    return "\n".join(lines)


def parse(answer: str, project: Project) -> dict[str, Any]:
    out: dict[str, Any] = {name: [] for name in LISTS}
    current = ""
    for raw in answer.splitlines():
        line = raw.strip()
        match = FIELD.match(line)
        if match:
            current = match.group(1).lower()
            value = match.group(2).strip()
            if current in LISTS:
                if value and value.upper() != "NONE":
                    out[current].append(value.lstrip("-* ").strip())
            elif value:
                out[current] = value
            continue
        if current in LISTS and line.startswith(("-", "*")):
            out[current].append(line.lstrip("-* ").strip())
        elif current in ("what", "how") and line:
            out[current] = f"{out.get(current, '')} {line}".strip()
    if not project.bugs:
        out["bugs"] = []           # the model may not invent a bug story
    out["bugs"] = [b for b in out["bugs"] if not projects.NOISE_COMMIT.search(b)]
    out["bugs"] = [projects.clean(b) for b in out["bugs"][:3] if b]
    if not project.status_doc:
        for name in ("working", "showonly", "next"):
            out[name] = []         # without the team's own audit these would be guesses
    for name in ("working", "showonly", "next"):
        out[name] = [projects.clean(item) for item in out[name][:5] if item]
    return {k: projects.clean(v) if isinstance(v, str) else v for k, v in out.items()}


def _section(title: str, items: list[str], limit: int = 5) -> str:
    return f"{title}\n" + "\n".join(f"- {item}" for item in items[:limit]) if items else ""


def credits(project: Project) -> str:
    """Who else built it, from the commit log: name, GitHub, and where their work went.

    Deterministic on purpose: credit is the one thing a post must not get wrong.
    LinkedIn @mentions need the person's LinkedIn member id, which can't be looked up
    from a GitHub account, so this names and links them; a real @tag is added by
    editing the post on LinkedIn afterwards."""
    if not project.team:
        return ""
    main = [p for p in project.team if p.get("share", 0) >= 0.1]
    help_ = [p for p in project.team if p.get("share", 0) < 0.1]
    lines = []
    for person in main:
        handle = f" (github.com/{person['github']})" if person.get("github") else ""
        areas = person.get("areas") or []
        focus = f" -- mostly {', '.join(areas[:2])}" if areas and areas[0] != "(project root)" else ""
        lines.append(f"- {person['name']}{handle}: {person['commits']} commits{focus}")
    title = "Built with:" if main else ""
    out = _section(title, [line[2:] for line in lines]) if lines else ""
    if help_:
        names = ", ".join(f"{p['name']}" + (f" (github.com/{p['github']})" if p.get("github") else "")
                          for p in help_)
        out = f"{out}\n\nThanks to {names} for contributing." if out else f"Thanks to {names} for contributing."
    return out.strip()


def compose(project: Project, phrased: dict[str, Any], hashtags: bool = True) -> str:
    """The post. Only the story is the model's; the lists are always the repo's."""
    stack = ", ".join((project.languages + project.stack)[:4])
    team = project.contributors > 1
    hook = phrased.get("hook") or (f"We built {project.name}." if team else f"I built {project.name}.")
    what = phrased.get("what") or project.description or f"{project.name} is a project I've been working on."
    how = phrased.get("how") or (f"Built with {stack}." if stack else "")
    if project.commits and "commit" not in how.lower():
        how = f"{how} {project.commits} commits so far.".strip()
    bugs = phrased.get("bugs") or project.bugs[:3]
    if phrased.get("working") or phrased.get("showonly"):
        status = [_section("What actually works:", phrased.get("working") or []),
                  _section("Still for show / not wired up yet:", phrased.get("showonly") or []),
                  _section("Next:", phrased.get("next") or project.future, 4)]
    else:
        status = [_section("Built:", project.built),
                  _section("Partly there:", project.partial, 4),
                  _section("Next:", project.future, 4)]
    parts = [hook, what, how, _section("What broke along the way:", bugs, 3), *status]
    parts.append(credits(project))
    if project.links:
        parts.append(f"Code: {project.links[0]}")
    if hashtags:
        tags = []
        for word in project.languages[:3]:
            tag = re.sub(r"[^A-Za-z0-9]", "", word.split("/")[0])
            if tag and tag.lower() not in {t.lower() for t in tags}:
                tags.append(tag)
        tags.append("buildinpublic")
        parts.append(" ".join(f"#{t}" for t in tags))
    text = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return projects.clean(text)[:2900]


def _norm(text: str) -> str:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def free_writer(registry: Any) -> Callable[[str], str]:
    """A free model (Groq, then OpenRouter) writing in the owner's voice, with no tools.

    Talk mode is what turns the tools off: a prompt that says "LinkedIn post" would
    otherwise be offered draft_post, and a ghostwriter that stages its own draft is a
    second draft nobody asked for. The persona guard is not run here on purpose -- it
    rewrites "I'm a developer" into Nova's identity, which is wrong for a post written as
    the owner (docs/publishing.md, "two identities")."""
    def ask(prompt: str) -> str:
        for name in ("groq", "openrouter"):
            backend = registry.backends.get(name)
            if backend is None or not backend.is_available():
                continue
            twin = backend.spawn()
            twin.key_lane = "background"
            twin.history = []
            twin.talk = "Follow the instructions exactly and output only what they ask for."
            result = twin.run(prompt, lambda event: None, threading.Event())
            if result.ok and result.summary.strip():
                return result.summary.strip()
        return ""
    return ask
