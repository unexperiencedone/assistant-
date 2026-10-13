"""Finding your projects and reading what they actually are.

A *project* is a folder under one of the whitelisted roots that has a README, a .git
folder, or a package file (package.json, pyproject.toml, requirements.txt, Cargo.toml,
go.mod). A root that is itself a project counts as one; nested repos inside it count too.

Everything a post may claim comes from here, read off the folder, never guessed:

    built    README checkboxes ticked, README feature bullets
    partial  README checkboxes not ticked, TODO/FIXME left in the code
    future   bullets under a Roadmap / Future / Next / TODO heading
    bugs     commit subjects that say fix/bug/crash/broken
    stack    dependencies from the package files, languages by file count

If a section has nothing behind it, it stays empty and the post leaves it out. That is
the guard against an LLM overclaiming: it never gets to invent the lists.

Two safety rules live here too, because they are about which folders, not about posts:
a repo whose commits are all by someone else is marked `mine=False` (client work, a
clone, a course repo), and every bit of text is passed through the same secret
redaction AfterThought uses, plus email addresses, before anything else sees it.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..afterthought.privacy import redact

MARKERS = ("README.md", "readme.md", "README.MD", "README", "README.txt", "package.json",
           "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod")
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next",
             "target", ".idea", ".vscode", "site-packages", ".cache", "coverage"}
CODE = {".py": "Python", ".js": "JavaScript", ".jsx": "React", ".ts": "TypeScript", ".tsx": "React",
        ".java": "Java", ".kt": "Kotlin", ".dart": "Flutter/Dart", ".go": "Go", ".rs": "Rust",
        ".cpp": "C++", ".c": "C", ".cs": "C#", ".php": "PHP", ".rb": "Ruby", ".swift": "Swift",
        ".ipynb": "Jupyter", ".html": "HTML", ".css": "CSS", ".sql": "SQL", ".vue": "Vue", ".svelte": "Svelte"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif")
SHOT_DIRS = ("screenshots", "screenshot", "images", "img", "docs", "assets", "media", "demo", ".github")
SECRETISH = re.compile(r"(?i)(secret|token|password|credential|\.env|apikey|api_key|private)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
FIX = re.compile(r"(?i)\b(fix(?:e[sd])?|bug|crash|broken|hotfix|resolve[sd]?|patch(?:ed)?)\b")
NOISE_COMMIT = re.compile(r"(?i)^(merge|wip|initial commit|first commit|update readme|readme|\.)\b"
                          r"|merge conflict|conflicts?\b|\bmissing (?:file|folder|dir)|gitignore|typo|lint|format(?:ting)?\b")
CHECKBOX = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.+)$")
BULLET = re.compile(r"^\s*[-*]\s+(?!\[)(.+)$")
HEADING = re.compile(r"^\s*#{1,4}\s+(.+?)\s*#*\s*$")
IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)|<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
FEATURE_HEADS = re.compile(r"(?i)\b(features?|what it does|highlights|capabilities|functionality)\b")
FUTURE_HEADS = re.compile(r"(?i)\b(roadmap|future|next steps?|todo|to-?do|planned|coming soon|upcoming)\b")
TODO = re.compile(r"(?:#|//|/\*|<!--)\s*(TODO|FIXME|HACK|XXX)\b[:\s-]*(.{0,80})")
TEMPLATE_README = re.compile(r"(?i)this template provides a minimal setup|getting started with create react app"
                             r"|this is a \[?next\.js\]? project bootstrapped|# react \+ (?:vite|typescript)")
MAX_LIST = 8
MAX_README = 4000


@dataclass
class Project:
    key: str                      # stable identity: the normalised folder path
    name: str
    path: str
    mine: bool = True
    description: str = ""
    readme: str = ""
    stack: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    built: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)
    future: list[str] = field(default_factory=list)
    bugs: list[str] = field(default_factory=list)
    commits: int = 0
    first_commit: str = ""
    last_commit: str = ""
    head: str = ""                # last commit hash, or a hash of the newest mtimes
    links: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    todos: int = 0
    contributors: int = 1         # authors with >=10% of commits; more than one means "we", not "I"
    share: float = 1.0            # your fraction of the commits
    # Everyone else who committed: name, commits, share, areas, examples, and (after
    # `credit()`) their GitHub login. Emails stay here for git queries; never in a post.
    team: list = field(default_factory=list)
    my_part: dict = field(default_factory=dict)   # your own areas and example commits
    repo: str = ""                # "owner/name" on GitHub, when the remote is GitHub
    status_doc: str = ""          # the repo's own progress/audit notes, when it keeps them

    def facts(self) -> dict[str, Any]:
        return asdict(self)


def key_for(path: str | Path) -> str:
    return str(Path(path).resolve()).lower().rstrip("\\/")


def clean(text: str) -> str:
    return EMAIL.sub("[email]", redact(text or ""))


# -- discovery -----------------------------------------------------------------------
def _is_project(folder: Path) -> bool:
    return (folder / ".git").exists() or any((folder / m).is_file() for m in MARKERS)


def _ignored(folder: Path, ignore: list[str]) -> bool:
    text = str(folder).lower()
    name = folder.name.lower()
    for pattern in ignore:
        pattern = pattern.lower().rstrip("\\/")
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(text, pattern) or text.startswith(pattern + "\\"):
            return True
        if text == pattern:
            return True
    return False


def discover(roots: list[str], ignore: list[str] | None = None, depth: int = 2) -> list[Path]:
    """Project folders under the roots, outermost first, never inside ignored folders."""
    ignore = list(ignore or [])
    found: list[Path] = []
    seen: set[str] = set()

    def walk(folder: Path, level: int, inside_repo: bool) -> None:
        if _ignored(folder, ignore) or folder.name.lower() in SKIP_DIRS:
            return
        repo = (folder / ".git").exists()
        # Inside a repo, only a nested repo is a project of its own: a repo's
        # automations/ or docs/ folder with a README is part of it, not a second post.
        counts = repo if inside_repo else _is_project(folder)
        if counts and key_for(folder) not in seen:
            seen.add(key_for(folder))
            found.append(folder)
        if level >= depth:
            return
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return
        for child in children:
            walk(child, level + 1, inside_repo or repo)

    for root in roots:
        folder = Path(root).expanduser()
        if folder.is_dir():
            walk(folder, 0, False)
    return found


# -- reading one project -------------------------------------------------------------
def _git(folder: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _readme(folder: Path) -> tuple[Path | None, str]:
    for name in ("README.md", "readme.md", "Readme.md", "README.MD", "README.rst", "README.txt", "README"):
        path = folder / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                return path, ""
            # A scaffold's README describes the scaffold, not the project.
            return path, "" if TEMPLATE_README.search(text[:600]) else text
    return None, ""


def _sections(readme: str) -> tuple[str, list[str], list[str], list[str], list[str]]:
    """description, feature bullets, ticked, unticked, future bullets."""
    description = ""
    features: list[str] = []
    ticked: list[str] = []
    unticked: list[str] = []
    future: list[str] = []
    heading = ""
    for raw in readme.splitlines():
        line = raw.rstrip()
        head = HEADING.match(line)
        if head:
            heading = head.group(1)
            continue
        box = CHECKBOX.match(line)
        if box:
            (ticked if box.group(1).lower() == "x" else unticked).append(_tidy(box.group(2)))
            continue
        bullet = BULLET.match(line)
        if bullet and bullet.group(1).rstrip().endswith(":"):
            continue                  # a sub-list's label ("Food input:") isn't a feature
        if bullet and heading:
            if FUTURE_HEADS.search(heading):
                future.append(_tidy(bullet.group(1)))
            elif FEATURE_HEADS.search(heading):
                features.append(_tidy(bullet.group(1)))
            continue
        text = line.strip()
        if (not description and text and not text.startswith(("![", "<", "[!", "|", "```", "---", "==="))
                and len(text) > 25 and "|" not in text):
            description = _tidy(text)
    return description, features, ticked, unticked, future


def _tidy(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)       # links -> their words
    text = re.sub(r"[*_`]+", "", text)
    return " ".join(text.split())[:200]


def _package_facts(folder: Path) -> tuple[str, str, list[str], list[str]]:
    """name, description, stack, links."""
    name = description = ""
    stack: list[str] = []
    links: list[str] = []
    pkg = folder / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            name, description = str(data.get("name") or ""), str(data.get("description") or "")
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            stack += [d for d in deps if not d.startswith(("@types/", "eslint", "@eslint"))][:15]
            if data.get("homepage"):
                links.append(str(data["homepage"]))
        except (OSError, ValueError):
            pass
    py = folder / "pyproject.toml"
    if py.is_file():
        try:
            import tomllib

            data = tomllib.loads(py.read_text(encoding="utf-8", errors="replace"))
            project = data.get("project") or (data.get("tool") or {}).get("poetry") or {}
            name = name or str(project.get("name") or "")
            description = description or str(project.get("description") or "")
            deps = project.get("dependencies") or []
            stack += [re.split(r"[<>=!~\[ ;]", d, 1)[0] for d in deps if isinstance(d, str)][:15]
        except (OSError, ValueError):
            pass
    req = folder / "requirements.txt"
    if req.is_file():
        try:
            for line in req.read_text(encoding="utf-8", errors="replace").splitlines()[:40]:
                line = line.strip()
                if line and not line.startswith(("#", "-")):
                    stack.append(re.split(r"[<>=!~\[ ;]", line, 1)[0])
        except OSError:
            pass
    for manifest, label in (("Cargo.toml", "Rust/Cargo"), ("go.mod", "Go modules"), ("pubspec.yaml", "Flutter")):
        if (folder / manifest).is_file():
            stack.append(label)
    seen: list[str] = []
    for item in stack:
        if item and item.lower() not in {s.lower() for s in seen}:
            seen.append(item)
    return name, description, seen[:15], links


# OneDrive "online-only" files: reading one downloads it, so they are never opened.
_CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS


def _walk_files(folder: Path, limit: int = 3000):
    """(path, size, mtime) for local files, skipping dependency folders and cloud stubs."""
    count = 0
    stack = [str(folder)]
    while stack and count < limit:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() not in SKIP_DIRS and not entry.name.startswith(".")                             and not os.path.exists(os.path.join(entry.path, ".git")):
                        stack.append(entry.path)
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if getattr(info, "st_file_attributes", 0) & _CLOUD_ONLY:
                continue
            count += 1
            yield Path(entry.path), info.st_size, info.st_mtime


def _languages_and_todos(folder: Path) -> tuple[list[str], list[str], int, float]:
    counts: Counter[str] = Counter()
    todos: list[str] = []
    total = 0
    newest = 0.0
    opened = 0
    for file, size, mtime in _walk_files(folder):
        label = CODE.get(file.suffix.lower())
        newest = max(newest, mtime)
        if not label:
            continue
        counts[label] += 1
        if file.suffix.lower() in (".ipynb", ".html", ".css") or size > 300_000 or opened >= 400:
            continue
        opened += 1
        try:
            for match in TODO.finditer(file.read_text(encoding="utf-8", errors="ignore")):
                total += 1
                if len(todos) < MAX_LIST and match.group(2).strip():
                    todos.append(_tidy(match.group(2)))
        except OSError:
            continue
    return [name for name, _ in counts.most_common(5)], todos, total, newest


def _images(folder: Path, readme_path: Path | None, readme: str, limit: int = 4) -> list[str]:
    """Screenshots the project already has: README images first, then screenshot folders.

    Never a file whose name looks like it holds a secret, and never one from a remote URL --
    only what is on disk here, so the approval preview shows exactly what would go up.
    """
    picked: list[str] = []

    def take(path: Path) -> None:
        if len(picked) >= limit or not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
            return
        if SECRETISH.search(path.name) or str(path) in picked:
            return
        try:
            size = path.stat().st_size
        except OSError:
            return
        if 10_000 < size <= 8 * 1024 * 1024:   # skip icons and badges; LinkedIn takes up to ~8 MB
            picked.append(str(path))

    base = readme_path.parent if readme_path else folder
    for match in IMAGE_REF.finditer(readme):
        ref = (match.group(1) or match.group(2) or "").split("#")[0].split("?")[0]
        if ref and not re.match(r"(?i)^(https?:|data:)", ref):
            take((base / ref).resolve())
    for name in SHOT_DIRS:
        sub = folder / name
        if sub.is_dir():
            for path in sorted(sub.rglob("*"))[:200]:
                if re.search(r"(?i)(screen|shot|demo|preview|ui|home|dashboard)", path.stem):
                    take(path)
    return picked


def read(folder: Path, owner_emails: set[str] | None = None) -> Project:
    """Everything a post may say about one project, redacted, from the folder itself."""
    folder = Path(folder)
    readme_path, readme = _readme(folder)
    description, features, ticked, unticked, future = _sections(readme)
    pkg_name, pkg_description, stack, links = _package_facts(folder)
    languages, todos, todo_total, newest = _languages_and_todos(folder)

    commits = 0
    contributors = 1
    share = 1.0
    team: list[dict] = []
    my_part: dict = {}
    repo = ""
    first = last = head = ""
    bugs: list[str] = []
    mine = True
    if (folder / ".git").exists():
        commits = int(_git(folder, "rev-list", "--count", "HEAD") or 0)
        head = _git(folder, "rev-parse", "HEAD")
        last = _git(folder, "log", "-1", "--format=%cs")
        first = _git(folder, "log", "--reverse", "--format=%cs", "--max-parents=0").splitlines()[:1]
        first = first[0] if first else ""
        for subject in _git(folder, "log", "-n", "200", "--format=%s").splitlines():
            if FIX.search(subject) and not NOISE_COMMIT.search(subject) and len(bugs) < MAX_LIST:
                bugs.append(_tidy(subject))
        # Commits per author. Someone with a stray commit or two doesn't make it a team
        # project; anyone with a tenth of the history does.
        tally: Counter[str] = Counter()
        for row in _git(folder, "shortlog", "-sne", "HEAD").splitlines():
            found = re.match(r"\s*(\d+)\s+.*<([^>]+)>", row)
            if found:
                tally[found.group(2).strip().lower()] += int(found.group(1))
        total = sum(tally.values())
        if total:
            contributors = max(1, sum(1 for n in tally.values() if n / total >= 0.1))
        if owner_emails and tally:
            own = sum(n for email, n in tally.items() if email in {e.lower() for e in owner_emails})
            mine = own > 0
            share = own / total if total else 1.0
        names = {}
        for row in _git(folder, "shortlog", "-sne", "HEAD").splitlines():
            found = re.match(r"\s*\d+\s+(.*?)\s*<([^>]+)>", row)
            if found:
                names.setdefault(found.group(2).strip().lower(), found.group(1).strip())
        mine_set = {e.lower() for e in (owner_emails or set())}
        for email, count in tally.most_common():
            work = _author_work(folder, email)
            if email in mine_set:
                my_part = {"commits": count, **work}
                continue
            team.append({"name": names.get(email, email.split("@")[0]), "email": email, "commits": count,
                         "share": round(count / total, 2) if total else 0.0, "github": _noreply_login(email),
                         **work})
        remote = _git(folder, "remote", "get-url", "origin")
        github = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$", remote or "")
        if github:
            repo = f"{github.group(1)}/{github.group(2)}"
        if remote.startswith("https://") and "@" not in remote:
            links.append(remote.removesuffix(".git"))
    if not head:
        head = hashlib.sha1(f"{newest:.0f}".encode()).hexdigest()[:12]

    # The README's own title is what the project calls itself ("Ved Code"); the folder
    # name ("the-archive") is often a working name nobody outside has heard.
    title = next((m.group(1) for m in map(HEADING.match, readme.splitlines()[:15]) if m), "")
    title = _tidy(title) if title and 2 <= len(title) <= 40 and not TEMPLATE_README.search(title) else ""
    name = title or (folder.name if folder.name.lower() not in ("src", "app", "code") else (pkg_name or folder.name))
    return Project(
        key=key_for(folder), name=name, path=str(folder), mine=mine,
        description=clean(pkg_description or description),
        readme=clean(readme[:MAX_README]),
        stack=stack, languages=languages,
        built=[clean(x) for x in (ticked + features)][:MAX_LIST],
        partial=[clean(x) for x in (unticked + todos)][:MAX_LIST],
        future=[clean(x) for x in future][:MAX_LIST],
        bugs=[clean(x) for x in bugs],
        commits=commits, first_commit=first, last_commit=last, head=head,
        links=links[:3], images=_images(folder, readme_path, readme), todos=todo_total,
        contributors=contributors, share=round(share, 2), team=team, my_part=my_part, repo=repo,
        status_doc=_status_doc(folder))


def owner_emails() -> set[str]:
    """Who you are in git: the global user.email, which is what your own commits carry."""
    try:
        out = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        email = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        email = ""
    return {email} if email else set()


def worth_posting(project: Project) -> tuple[bool, str]:
    """Whether there's enough here to say something true. The reason when there isn't."""
    if not project.mine:
        return False, "every commit is by someone else"
    if not (project.description or project.built or project.readme.strip()):
        return False, "no README or description to go on"
    if project.commits and project.commits < 3 and not project.readme.strip():
        return False, "too little history"
    return True, ""


# -- who did what --------------------------------------------------------------------
def _noreply_login(email: str) -> str:
    """GitHub's private-email form carries the login: 12345+name@users.noreply.github.com."""
    found = re.match(r"(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$", email or "")
    return found.group(1) if found else ""


def _author_work(folder: Path, email: str) -> dict[str, Any]:
    """Where one person's commits landed, and a few of their commit subjects, from git alone."""
    files = _git(folder, "log", f"--author=<{email}>", "-i", "--name-only", "--format=", "-n", "300")
    areas: Counter[str] = Counter()
    for line in files.splitlines():
        line = line.strip()
        if not line:
            continue
        top = line.split("/")[0] if "/" in line else "(project root)"
        if top.lower() not in SKIP_DIRS:
            areas[top] += 1
    subjects = []
    for subject in _git(folder, "log", f"--author=<{email}>", "-i", "--format=%s", "-n", "60").splitlines():
        if subject.strip() and not NOISE_COMMIT.search(subject) and len(subjects) < 4:
            subjects.append(clean(_tidy(subject)))
    sha = _git(folder, "log", f"--author=<{email}>", "-i", "-1", "--format=%H")
    return {"areas": [a for a, _ in areas.most_common(3)], "examples": subjects, "sha": sha}


def _github_token() -> str:
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        return token
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def credit(project: Project) -> Project:
    """Fill in each collaborator's GitHub login from the commit GitHub knows them by.

    One API call per collaborator (their latest commit), unauthenticated for public repos
    (60 an hour is plenty), with GITHUB_TOKEN or the gh CLI's login for private ones.
    Called when a post is drafted, not on every scan."""
    if not project.repo:
        return project
    import urllib.error
    import urllib.request

    token = _github_token()
    for person in project.team:
        if person.get("github") or not person.get("sha"):
            continue
        request = urllib.request.Request(
            f"https://api.github.com/repos/{project.repo}/commits/{person['sha']}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "nova-showcase",
                     **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            person["github"] = ((data.get("author") or {}).get("login") or "")
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return project


STATUS_DOCS = ("PROGRESS.md", "STATUS.md", "AUDIT.md", "ROADMAP.md", "TODO.md", "docs/STATUS.md",
               "docs/PROGRESS.md", "CHANGELOG.md")


def _status_doc(folder: Path, limit: int = 7000) -> str:
    """The project's own account of what works and what doesn't, if it keeps one.

    This is the best source there is for an honest post: written by the people who
    built it, usually after reading the code. Only the first `limit` characters go to
    the model, which for an audit is its index and the first findings."""
    for name in STATUS_DOCS:
        path = folder / name
        if path.is_file():
            try:
                return clean(path.read_text(encoding="utf-8-sig", errors="replace")[:limit])
            except OSError:
                continue
    return ""
