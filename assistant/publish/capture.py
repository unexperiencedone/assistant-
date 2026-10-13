"""Screenshots for a project post, taken from the project itself running.

Order of preference, stopping at the first that gives pictures:

    1. the live deployment: a URL from [showcase] demos, package.json "homepage", a
       Vercel/Netlify/... link in the README, or the GitHub repo's homepage field;
    2. the project run locally: `npm run dev` (Next, Vite, CRA) or `streamlit run`,
       on a free port, stopped again afterwards; a plain index.html is opened as a file.

Which pages to visit comes from the code, not guesses: Next.js app/ and pages/
routes, React Router `path="..."` props. A page that bounces to /login is logged into
when there are credentials for it -- SHOWCASE_LOGIN_<NAME>=email:password in .env for a
private account, or [showcase.logins] in config.toml for a public demo account -- and
skipped when there aren't. Error pages and near-identical
shots are dropped.

The browser is the installed Edge or Chrome (Playwright's `channel`), so nothing extra
is downloaded. Screenshots land in data/showcase/<project>/ and still go through the
approval gate: a logged-in page can show an account name, and only a person can judge
whether a screen is fine to post.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .projects import Project

HOSTS = re.compile(r"https?://[\w.-]+\.(?:vercel\.app|netlify\.app|github\.io|pages\.dev|onrender\.com|"
                   r"railway\.app|fly\.dev|herokuapp\.com|web\.app|firebaseapp\.com|streamlit\.app|"
                   r"surge\.sh|glitch\.me|replit\.app)[^\s)\"'<>]*", re.I)
SKIP_ROUTE = re.compile(r"(?i)(^|/)(api|login|logout|signin|sign-in|signup|sign-up|register|auth|admin|"
                        r"terms|privacy|404|500|_[\w-]+|reset|forgot|verify|callback)(/|$)")
# Pages that say little about what the project does: shot last, if at all.
LOW_ROUTE = re.compile(r"(?i)(changelog|manifesto|about|contact|onboarding|settings|profile|docs?|faq|"
                       r"pricing|blog|legal|help|support|dashboard)")
LOGIN_PATH = re.compile(r"(?i)/(login|signin|sign-in|auth)\b")
VIEWPORT = {"width": 1440, "height": 900}
CREDS_PREFIX = "SHOWCASE_LOGIN_"


# -- where it lives ------------------------------------------------------------------
def demo_url(project: Project, overrides: dict[str, str] | None = None) -> str:
    for key, url in (overrides or {}).items():
        if _slug(key) in (_slug(project.name), _slug(Path(project.path).name)):
            return url.rstrip("/")
    pkg = Path(project.path) / "package.json"
    if pkg.is_file():
        try:
            home = json.loads(pkg.read_text(encoding="utf-8", errors="replace")).get("homepage") or ""
            if home.startswith("http"):
                return home.rstrip("/")
        except ValueError:
            pass
    found = HOSTS.search(project.readme or "")
    if found:
        return _origin(found.group(0))
    if project.repo:
        try:
            request = urllib.request.Request(f"https://api.github.com/repos/{project.repo}",
                                             headers={"User-Agent": "nova-showcase"})
            with urllib.request.urlopen(request, timeout=15) as response:
                home = json.loads(response.read().decode("utf-8")).get("homepage") or ""
            if home.startswith("http"):
                return home.rstrip("/")
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return ""


def _origin(url: str) -> str:
    match = re.match(r"(https?://[^/]+)", url)
    return match.group(1) if match else url


def routes(folder: Path, limit: int = 12) -> list[str]:
    """Pages the app has, read from its source: "/" first, shallow before deep."""
    found: set[str] = {"/"}
    for app in (folder / "src" / "app", folder / "app"):
        if app.is_dir():
            for page in app.rglob("page.*"):
                if page.suffix not in (".tsx", ".jsx", ".ts", ".js") or "node_modules" in page.parts:
                    continue
                parts = [p for p in page.parent.relative_to(app).parts if not p.startswith("(")]
                if any(p.startswith("[") or p.startswith("@") for p in parts):
                    continue
                found.add("/" + "/".join(parts))
    for pages in (folder / "src" / "pages", folder / "pages"):
        if pages.is_dir():
            for page in pages.rglob("*.*"):
                if page.suffix not in (".tsx", ".jsx", ".js") or "[" in str(page) or page.stem.startswith("_"):
                    continue
                rel = page.relative_to(pages).with_suffix("").as_posix()
                found.add("/" + ("" if rel == "index" else rel.removesuffix("/index")))
    src = folder / "src"
    if src.is_dir() and len(found) == 1:
        for file in list(src.rglob("*.jsx"))[:300] + list(src.rglob("*.tsx"))[:300]:
            try:
                for path in re.findall(r"path=[\"'](/[\w/-]*)[\"']", file.read_text(encoding="utf-8", errors="ignore")):
                    found.add(path)
            except OSError:
                continue
    usable = [r for r in found if r == "/" or not SKIP_ROUTE.search(r)]
    return sorted(usable, key=lambda r: (r != "/", bool(LOW_ROUTE.search(r)), r.count("/"), r))[:limit]


# -- running it here ---------------------------------------------------------------
def _free_port(start: int = 3100) -> int:
    for port in range(start, start + 200):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port")


def _up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status < 500
    except urllib.error.HTTPError as error:
        return error.code < 500
    except (urllib.error.URLError, OSError):
        return False


class LocalRun:
    """The project's own dev server, for as long as the `with` block lasts."""

    def __init__(self, folder: Path, install: bool = True, wait: float = 180) -> None:
        self.folder = Path(folder)
        self.install = install
        self.wait = wait
        self.process: subprocess.Popen | None = None
        self.url = ""
        self.reason = ""

    def __enter__(self) -> "LocalRun":
        command = self._command()
        if command is None:
            return self
        if command == "file":
            self.url = (self.folder / "index.html").resolve().as_uri()
            return self
        port = _free_port()
        env = {**os.environ, "PORT": str(port), "BROWSER": "none", "NODE_ENV": "development"}
        log = open(self.folder / ".nova-showcase-run.log", "w", encoding="utf-8", errors="replace")
        self.process = subprocess.Popen(
            [part.replace("{port}", str(port)) for part in command], cwd=self.folder, env=env,
            stdout=log, stderr=subprocess.STDOUT, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        url = f"http://localhost:{port}"
        deadline = time.time() + self.wait
        while time.time() < deadline:
            if self.process.poll() is not None:
                self.reason = "the dev server exited (see .nova-showcase-run.log in the project)"
                break
            if _up(url):
                self.url = url
                time.sleep(3)            # first compile of the first page
                break
            time.sleep(2)
        else:
            self.reason = f"it didn't answer on {url} within {self.wait:.0f}s"
        return self

    def _command(self) -> list[str] | str | None:
        pkg = self.folder / "package.json"
        if pkg.is_file():
            try:
                scripts = json.loads(pkg.read_text(encoding="utf-8", errors="replace")).get("scripts") or {}
            except ValueError:
                scripts = {}
            npm = shutil.which("npm.cmd") or shutil.which("npm")
            if not npm:
                self.reason = "npm isn't installed"
                return None
            if not (self.folder / "node_modules").is_dir():
                if not self.install:
                    self.reason = "its dependencies aren't installed"
                    return None
                done = subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=self.folder,
                                      capture_output=True, text=True, timeout=900,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if done.returncode != 0:
                    self.reason = "npm install failed"
                    return None
            script = "dev" if "dev" in scripts else "start" if "start" in scripts else ""
            if not script:
                self.reason = "package.json has no dev or start script"
                return None
            text = scripts[script]
            if "next" in text or "vite" in text:
                return [npm, "run", script, "--", "--port", "{port}"]
            return [npm, "run", script]          # CRA and friends read PORT from the env
        for name in ("streamlit_app.py", "app.py", "main.py", "Home.py"):
            file = self.folder / name
            if file.is_file() and "streamlit" in file.read_text(encoding="utf-8", errors="ignore"):
                exe = shutil.which("streamlit")
                if exe:
                    return [exe, "run", name, "--server.port", "{port}", "--server.headless", "true"]
        if (self.folder / "index.html").is_file():
            return "file"
        self.reason = "I don't know how to start it (no dev script, Streamlit app or index.html)"
        return None

    def __exit__(self, *_exc: Any) -> None:
        if self.process is not None and self.process.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# -- taking the pictures -----------------------------------------------------------
def credentials(project: Project, logins: dict[str, str] | None = None) -> tuple[str, str] | None:
    """A login for the project's demo: .env first (private), then [showcase.logins] (public demo accounts)."""
    names = (project.name, Path(project.path).name)
    values = [(os.environ.get(CREDS_PREFIX + _slug(n).upper()) or "").strip() for n in names]
    values += [v for k, v in (logins or {}).items() if _slug(k) in {_slug(n) for n in names}]
    for value in values:
        if ":" in value:
            user, _, password = value.partition(":")
            return user.strip(), password.strip()
    return None


def _launch(playwright: Any) -> Any:
    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch(channel=channel)
        except Exception:
            continue
    return playwright.chromium.launch()


def _log_in(page: Any, base: str, creds: tuple[str, str]) -> bool:
    page.goto(base + "/login", wait_until="networkidle", timeout=30000)
    user = page.locator("input[type=email], input[name=email], input[name=username], input[autocomplete=username]")
    password = page.locator("input[type=password]")
    if not user.count() or not password.count():
        return False
    user.first.fill(creds[0])
    password.first.fill(creds[1])
    password.first.press("Enter")
    try:
        page.wait_for_url(lambda url: not LOGIN_PATH.search(url), timeout=20000)
    except Exception:
        return False
    page.wait_for_load_state("networkidle")
    return True


def shoot(base: str, paths: list[str], out: Path, creds: tuple[str, str] | None = None,
          limit: int = 4) -> tuple[list[str], list[str]]:
    """Screenshots of the given pages. Returns (files, notes about what was skipped)."""
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    notes: list[str] = []
    sizes: list[int] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        logged_in = False
        for path in paths:
            if len(files) >= limit:
                break
            try:
                response = page.goto(base + path, wait_until="networkidle", timeout=45000)
                if LOGIN_PATH.search(page.url.replace(base, "")) and path != "/":
                    if creds and not logged_in:
                        logged_in = _log_in(page, base, creds)
                        if not logged_in:
                            notes.append("logging in failed")
                            creds = None
                    if not logged_in:
                        notes.append(f"{path} needs a login")
                        continue
                    response = page.goto(base + path, wait_until="networkidle", timeout=45000)
                status = response.status if response else 200
                title = page.title() or ""
                if status >= 400 or re.search(r"\b(404|not found|error)\b", title, re.I):
                    notes.append(f"{path} returned {status}")
                    continue
                page.wait_for_timeout(2000)       # entrance animations
                name = re.sub(r"[^\w-]+", "-", path.strip("/")) or "home"
                file = out / f"{len(files) + 1:02d}-{name}.png"
                page.screenshot(path=str(file))
                size = file.stat().st_size
                if any(abs(size - other) / max(other, 1) < 0.01 for other in sizes):
                    file.unlink()                 # same screen under another route
                    continue
                sizes.append(size)
                files.append(str(file))
            except Exception as error:
                notes.append(f"{path}: {str(error).splitlines()[0][:80]}")
        browser.close()
    return files, notes


def capture(project: Project, out_root: Path, overrides: dict[str, str] | None = None,
            limit: int = 4, run_locally: bool = True, install: bool = True,
            logins: dict[str, str] | None = None) -> tuple[list[str], str]:
    """Screenshots of the project running, and one sentence about where they came from."""
    out = out_root / _slug(project.name)
    if out.is_dir():
        shutil.rmtree(out, ignore_errors=True)
    paths = routes(Path(project.path))
    creds = credentials(project, logins)
    base = demo_url(project, overrides)
    if base:
        files, notes = shoot(base, paths, out, creds, limit)
        if files:
            return files, _said(f"from the live site ({base})", notes)
    if not run_locally:
        return [], "There's no live site to screenshot."
    with LocalRun(Path(project.path), install=install) as run:
        if not run.url:
            return [], f"I couldn't run it locally: {run.reason}."
        files, notes = shoot(run.url, paths, out, creds, limit)
    return files, _said("from running it locally", notes) if files else f"Running it locally showed nothing usable ({'; '.join(notes[:3])})."


def _said(where: str, notes: list[str]) -> str:
    login = [n for n in notes if "needs a login" in n]
    extra = f" {len(login)} page{'s' if len(login) != 1 else ''} need a login I don't have." if login else ""
    return f"Screenshots {where}.{extra}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
