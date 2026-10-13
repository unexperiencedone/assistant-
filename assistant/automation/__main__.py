"""Command line for UI automation and automations (terse output for agents).

    nova-cli ui windows [--filter spot]
    nova-cli ui controls "Calculator" [--filter plus] [--type Button] [--limit 40]
    nova-cli ui click "Calculator" "Five" [--auto-id ID] [--type Button] [--index 0] [--exact]
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
        p.add_argument("--exact", action="store_true", help="no prefix/substring fallback on the name")
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


def word_main(argv: list[str]) -> int:
    from pathlib import Path

    from .office import OfficeError, write_document

    parser = argparse.ArgumentParser(prog="word", description="Create a Word document from Markdown and open it in Word.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("write")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="Markdown file with the content (# headings, - bullets, paragraphs)")
    src.add_argument("--text", help="content inline (for short documents)")
    p.add_argument("--title", default="")
    p.add_argument("--out", help="where to save the .docx (default: Documents\\<title>.docx)")
    p.add_argument("--hidden", action="store_true", help="don't show Word; just save the file")
    args = parser.parse_args(argv)

    content = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    try:
        path = write_document(content, args.title, Path(args.out) if args.out else None, visible=not args.hidden)
    except (OfficeError, OSError) as exc:
        print(f"failed: {exc}")
        return 1
    print(f"saved\t{path}")
    return 0


def web_main(argv: list[str]) -> int:
    """Your own Chrome, through the Nova extension and the running Nova (extension.py).

    Output is the extension's numbered element map, then only what changed after each
    action -- a fraction of what a full page snapshot costs a model to read.
    """
    import json
    import os
    import urllib.error
    import urllib.request
    from pathlib import Path

    from ..config import load_settings
    from ..ui import auth

    parser = argparse.ArgumentParser(prog="web", description=web_main.__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("tabs")
    p = sub.add_parser("tab"); p.add_argument("id", type=int)
    p = sub.add_parser("open"); p.add_argument("url"); p.add_argument("--new-tab", action="store_true")
    p = sub.add_parser("map"); p.add_argument("--all", action="store_true", help="every element, not just ~60")
    p = sub.add_parser("find"); p.add_argument("query")
    p = sub.add_parser("read"); p.add_argument("--ref", type=int); p.add_argument("--selector", default="")
    p.add_argument("--limit", type=int, default=3000)
    p = sub.add_parser("click"); p.add_argument("ref", nargs="?"); p.add_argument("--text", default="")
    p.add_argument("--trusted", action="store_true", help="real mouse input via the debugger, for stubborn sites")
    p = sub.add_parser("fill"); p.add_argument("ref"); p.add_argument("value"); p.add_argument("--enter", action="store_true")
    p = sub.add_parser("form", help="web form 3=Ada 4=ada@x.com 9=India --submit 12")
    p.add_argument("pairs", nargs="+", metavar="REF=VALUE"); p.add_argument("--submit", type=int)
    p = sub.add_parser("press"); p.add_argument("key"); p.add_argument("--trusted", action="store_true")
    p = sub.add_parser("scroll"); p.add_argument("direction", nargs="?", default="down", choices=["down", "up", "top", "bottom"])
    p.add_argument("--ref", type=int)
    sub.add_parser("back")
    args = parser.parse_args(argv)

    def target(ref=None, text=""):
        if ref not in (None, ""):
            return {"ref": int(str(ref).strip("[]"))}
        return {"text": text} if text else {}

    # The CLI is driven by the person or by Claude Code, who ask before risky clicks
    # themselves (AGENTS.md section 6), so nothing here is guarded.
    calls = {
        "tabs": lambda: ("tabs", {}),
        "tab": lambda: ("use_tab", {"tab": args.id}),
        "open": lambda: ("open", {"url": args.url, "new_tab": args.new_tab}),
        "map": lambda: ("snapshot", {"all": args.all}),
        "find": lambda: ("find", {"query": args.query}),
        "read": lambda: ("read", {"limit": args.limit, **target(args.ref), **({"selector": args.selector} if args.selector else {})}),
        "click": lambda: ("click", {"target": {**target(args.ref, args.text), **({"trusted": True} if args.trusted else {})}}),
        "fill": lambda: ("fill", {"target": target(args.ref), "value": args.value, "submit": args.enter}),
        "form": lambda: ("form", {"fields": [{"ref": int(k.strip("[]")), "value": v}
                                             for k, v in (pair.split("=", 1) for pair in args.pairs)],
                                  "submit_ref": args.submit}),
        "press": lambda: ("press", {"key": args.key, "trusted": args.trusted}),
        "scroll": lambda: ("scroll", {"direction": args.direction, "target": target(args.ref) or None}),
        "back": lambda: ("back", {}),
    }

    settings = load_settings()
    token_path = Path(os.path.expandvars(settings.ui.token_file)).expanduser()
    token = auth.read(token_path if token_path.is_absolute() else settings.base_dir / token_path)
    url = f"http://127.0.0.1:{settings.ui.web_port}/api/ext"
    headers = {auth.HEADER_NAME: token, "Content-Type": "application/json"}
    try:
        if args.cmd == "status":
            request = urllib.request.Request(url, headers=headers)
        else:
            method, params = calls[args.cmd]()
            request = urllib.request.Request(url, data=json.dumps({"method": method, "params": params}).encode(),
                                             headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
    except (OSError, ValueError) as exc:
        print(f"Nova isn't reachable on port {settings.ui.web_port} ({exc}). Is it running?")
        return 1

    if args.cmd == "status":
        print("connected" if body.get("connected") else "not connected", body.get("version") or "", body.get("agent") or "")
        print(f"{body.get('calls', 0)} calls since Nova started" + (f"; last: {body['last']}" if body.get("last") else ""))
        return 0 if body.get("connected") else 1
    if not body.get("ok"):
        print(f"failed: {body.get('error', body)}")
        return 1
    result = body.get("result") or {}
    if result.get("risky"):  # never set from here, but say so plainly if it ever is
        print(f"refused as risky: {result.get('page')}")
        return 1
    if args.cmd == "click":
        # The rungs Chrome can't do: an app link through Windows, then UI Automation.
        from .extension import finish_click

        def changes() -> dict:
            request = urllib.request.Request(url, data=json.dumps({"method": "changes", "params": {}}).encode(),
                                             headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read()).get("result") or {}
            except (OSError, ValueError):
                return {}

        result = finish_click(result, changes)
    print(result.get("page") or result.get("text") or result.get("line") or "done")
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
