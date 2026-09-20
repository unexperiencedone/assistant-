"""Count the system, so a document about it cannot quietly go stale.

`python main.py stats` prints what Table 1 of docs/overview.md claims. Anything written
down by hand drifts from the code within weeks; this is the same numbers, counted now.
"""

from __future__ import annotations

import json
from pathlib import Path

from .paths import APP_DIR

SKIP = {"node_modules", ".git", ".venv", "__pycache__", "canvas_dist", "dist", "build"}


def _files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if path.suffix.lower() not in suffixes or not path.is_file():
            continue
        if any(part in SKIP for part in path.parts):
            continue
        found.append(path)
    return found


def _lines(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
    return total


def collect(root: Path | None = None) -> dict[str, int]:
    root = root or APP_DIR
    python = _files(root / "assistant", (".py",)) + _files(root / "phone", (".py",))
    frontend = _files(root / "canvas" / "src", (".jsx", ".js", ".css"))
    tests = _files(root / "tests", (".py",))
    return {
        "python_modules": len(python),
        "python_lines": _lines(python),
        "frontend_files": len(frontend),
        "frontend_lines": _lines(frontend),
        "test_files": len([p for p in tests if p.name.startswith("test_")]),
        "automations": len(list((root / "automations").glob("*.toml"))),
        "docs": len(list((root / "docs").glob("*.md"))),
    }


def routes_report() -> str:
    """The week-by-week split of free routes versus model turns."""
    from .routes import RouteCounter, chart

    return chart(RouteCounter(APP_DIR / "data" / "routes.json"))


def main(argv: list[str] | None = None) -> int:
    if argv and argv[0] == "--routes":
        print(routes_report())
        return 0
    counts = collect()
    if argv and argv[0] == "--json":
        print(json.dumps(counts, indent=2))
        return 0
    rows = [
        ("Python", f"{counts['python_modules']} modules, ~{counts['python_lines']:,} lines"),
        ("Frontend", f"{counts['frontend_files']} files, ~{counts['frontend_lines']:,} lines"),
        ("Tests", f"{counts['test_files']} files"),
        ("Saved automations", str(counts["automations"])),
        ("Docs", str(counts["docs"])),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label.ljust(width)}  {value}")
    # The test count itself comes from running them, not from counting files.
    print("\n(test count: python -m unittest discover -s tests)")
    return 0
