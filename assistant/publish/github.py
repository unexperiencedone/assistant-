"""The GitHub arm: Nova's hands on the one platform that cannot punish a mistake.

Everything here goes through the `gh` CLI, which carries its own login -- no token of
ours to store, leak or refresh. `gh` is not installed by default on this machine, so
every entry point starts by asking whether it is there and says so plainly when it is
not, rather than failing three layers down with a FileNotFoundError.

Reads happen freely. Writes (a release, a pushed commit, a rewritten profile README)
are staged through `publish/gate.py` and only run once you have said yes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

TIMEOUT = 60
INSTALL_HINT = ("I don't have the GitHub CLI here. Install it with "
                "winget install --id GitHub.cli -e, then run gh auth login once.")


def available() -> bool:
    return shutil.which("gh") is not None


def signed_in() -> tuple[bool, str]:
    """Whether `gh` can act as you right now, and what it said."""
    if not available():
        return False, INSTALL_HINT
    ok, out = run(["auth", "status"])
    return ok, out


def run(args: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    """One `gh` call. Returns (worked, output) and never raises for an ordinary failure."""
    if not available():
        return False, INSTALL_HINT
    try:
        done = subprocess.run(["gh", *args], cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"gh {args[0]} took longer than {TIMEOUT} seconds and I stopped waiting."
    except OSError as error:
        return False, f"I couldn't run gh: {error}"
    output = (done.stdout or "").strip() or (done.stderr or "").strip()
    return done.returncode == 0, output


def api(path: str, fields: dict[str, Any] | None = None, method: str = "") -> tuple[bool, Any]:
    """The REST API through gh's own credentials. Returns parsed JSON when it can."""
    args = ["api", path]
    if method:
        args += ["--method", method]
    for key, value in (fields or {}).items():
        args += ["-f", f"{key}={value}"]
    ok, out = run(args)
    if not ok:
        return False, out
    try:
        return True, json.loads(out)
    except ValueError:
        return True, out


# -- reading -------------------------------------------------------------------------
def repos(limit: int = 10) -> tuple[bool, list[dict[str, Any]]]:
    """Your repositories, most recently pushed first."""
    ok, out = run(["repo", "list", "--limit", str(limit), "--json",
                   "name,description,pushedAt,isPrivate,url"])
    if not ok:
        return False, []
    try:
        return True, json.loads(out)
    except ValueError:
        return False, []


def recent_activity(limit: int = 5) -> str:
    """"What changed in my repos" -- a spoken sentence, or the honest reason there isn't one."""
    ok, rows = repos(limit)
    if not ok:
        return signed_in()[1] if not available() else "I couldn't reach GitHub just now."
    if not rows:
        return "No repositories came back."
    names = [f"{r['name']}, pushed {str(r.get('pushedAt') or '')[:10]}" for r in rows]
    return f"{len(rows)} repositories, most recent first: " + "; ".join(names) + "."


# -- writing (each one is staged, never called straight from a request) ---------------
def commit_and_push(folder: Path, message: str) -> tuple[bool, str]:
    """Commit whatever is staged-or-modified in a repo and push it to its remote."""
    for args in (["add", "-A"], ["commit", "-m", message], ["push"]):
        try:
            done = subprocess.run(["git", *args], cwd=str(folder), capture_output=True,
                                  text=True, timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, f"git {args[0]} failed: {error}"
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            if args[0] == "commit" and any("nothing to commit" in line for line in detail):
                return False, "There was nothing to commit."
            return False, f"git {args[0]} failed: {detail[-1] if detail else 'no output'}"
    return True, f"Committed and pushed: {message}"


def release(repo: str, tag: str, title: str = "", notes: str = "") -> tuple[bool, str]:
    args = ["release", "create", tag, "--repo", repo, "--title", title or tag]
    args += ["--notes", notes] if notes else ["--generate-notes"]
    return run(args)


def update_profile_readme(text: str, folder: Path) -> tuple[bool, str]:
    """Your GitHub profile README: the special <user>/<user> repository, cloned at `folder`."""
    folder = Path(folder)
    readme = folder / "README.md"
    if not (folder / ".git").is_dir():
        return False, f"{folder} isn't a git checkout, so I won't write a README into it."
    try:
        readme.write_text(text, encoding="utf-8")
    except OSError as error:
        return False, f"I couldn't write the README: {error}"
    return commit_and_push(folder, "docs(profile): update")
