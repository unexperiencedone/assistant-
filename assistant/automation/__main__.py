"""Command line for UI automation and automations (terse output for agents).

    nova-cli ui windows [--filter spot]
    nova-cli ui controls "Calculator" [--filter plus] [--type Button] [--limit 40]
    nova-cli ui click "Calculator" "Five" [--auto-id ID] [--type Button] [--index 0]
    nova-cli ui type "Notepad" "hello world" [--into "Text editor"]
    nova-cli ui keys "Spotify" "^l"              # ^ Ctrl  % Alt  + Shift  {ENTER} {TAB}
    nova-cli ui read "Calculator" --auto-id CalculatorResults

    nova-cli macro list
    nova-cli macro run "calculator demo"
    nova-cli macro run "search wikipedia for alan turing"

(From source: `python main.py ui ...` / `python main.py macro ...`.)
Exit codes: 0 ok, 1 not found / failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import sys


def automations_dir():
    import os
    from pathlib import Path

    from ..config import load_settings

    settings = load_settings()
    folder = Path(os.path.expandvars(settings.automations.folder)).expanduser()
    return folder if folder.is_absolute() else settings.base_dir / folder


def ui_main(argv: list[str]) -> int:
    from . import desktop

    parser = argparse.ArgumentParser(prog="ui", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("windows"); p.add_argument("--filter", default="")
    p = sub.add_parser("controls"); p.add_argument("window"); p.add_argument("--filter", default="")
    p.add_argument("--type", default=""); p.add_argument("--limit", type=int, default=40)
    p.add_argument("--all", action="store_true", help="include non-interactive elements")
    for name in ("click", "read"):
        p = sub.add_parser(name); p.add_argument("window"); p.add_argument("name", nargs="?", default="")
        p.add_argument("--auto-id", default=""); p.add_argument("--type", default=""); p.add_argument("--index", type=int, default=0)
    p = sub.add_parser("type"); p.add_argument("window"); p.add_argument("text"); p.add_argument("--into", default="")
    p = sub.add_parser("keys"); p.add_argument("window"); p.add_argument("keys")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "windows":
            rows = desktop.list_windows(args.filter)
            print("\n".join(f"{title}\t{pid}" for title, pid in rows) or "no windows")
        elif args.cmd == "controls":
            rows = desktop.list_controls(args.window, args.filter, args.type, args.limit, not args.all)
            print("\n".join(r.row() for r in rows) or "no matching controls")
        elif args.cmd == "click":
            print("clicked\t" + desktop.click(args.window, args.name, args.auto_id, args.type, args.index))
        elif args.cmd == "read":
            print(desktop.read(args.window, args.name, args.auto_id))
        elif args.cmd == "type":
            desktop.type_text(args.window, args.text, args.into)
            print("typed")
        elif args.cmd == "keys":
            desktop.send_keys(args.window, args.keys)
            print("sent")
    except desktop.AutomationError as exc:
        print(exc)
        return 1
    return 0


def macro_main(argv: list[str]) -> int:
    from ..events import EventBus
    from .macros import MacroRunner, load_macros

    parser = argparse.ArgumentParser(prog="macro")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("run"); p.add_argument("phrase", help="a trigger phrase or the automation's name")
    args = parser.parse_args(argv)

    macros = load_macros(automations_dir())
    if args.cmd == "list":
        for m in macros:
            print(f"{m.name}\t{'; '.join(m.phrases)}\t{m.path.name}")
        return 0

    for macro in macros:
        params = macro.match(args.phrase)
        if params is not None:
            break
    else:
        print(f"No automation matches '{args.phrase}'. Try: macro list")
        return 1

    bus = EventBus()
    bus.subscribe("macro_step", lambda e: print(f"  step {e.data['index'] + 1}: {e.data['status']} {e.data.get('detail', '')}".rstrip()))

    def open_app(name: str) -> bool:
        from ..config import load_settings
        from ..system import LocalSystem

        settings = load_settings()
        return LocalSystem(settings.local, settings.base_dir).open(name, kind="app").ok

    runner = MacroRunner(bus, open_app=open_app, say=lambda text: print(f"  says: {text}"))
    ok, summary = runner.run(macro, params, args.phrase)
    print(summary)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(ui_main(sys.argv[1:]))
