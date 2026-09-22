"""Voice assistant that sends what you say to Claude Code (or Antigravity).

    python main.py                      # talk to it: canvas in the browser + terminal dashboard
    python main.py --mode window        # canvas in a native window, with a tray icon
    python main.py --tray               # start hidden in the tray (what autostart uses)
    python main.py --text               # type instead of talking
    python main.py demo                 # scripted canvas demo: no mic, no Claude usage
    python main.py ui controls "Calculator"     # list / click / type into desktop app controls
    python main.py macro run "calculator demo"  # run an automation from automations/
    python main.py sysindex find "invoice"      # local index CLI
    python main.py voice-check          # is this still the same Nova? (assistant/persona/drift.py)
    python main.py autostart install|remove|status

Works from any folder, from `python -m assistant`, and as the packaged Nova.exe / nova-cli.exe.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # importable from any working directory


def _ensure_output_streams() -> Path | None:
    """A windowed .exe (or pythonw) has no console: send output to a log file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        if sys.platform == "win32":
            sys.stdout.reconfigure(encoding="utf-8")  # rich symbols on older consoles
        return None
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Nova"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "nova.log"
    stream = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream
    return log_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    from assistant.config import DEFAULT_CONFIG

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to the settings file")
    parser.add_argument("--text", action="store_true", help="type commands instead of using the microphone")
    parser.add_argument("--mode", choices=["browser", "window", "tray"],
                        help="canvas in the browser, a native window, or start hidden in the tray")
    parser.add_argument("--tray", action="store_const", const="tray", dest="mode", help="shortcut for --mode tray")
    parser.add_argument("--backend", choices=["claude", "antigravity"], help="agent CLI to start with")
    parser.add_argument("--workspace", help="folder the agent CLI works in")
    parser.add_argument("--no-web", action="store_true", help="disable the canvas server (browser mode only)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    parser.add_argument("--no-dashboard", action="store_true", help="plain terminal output instead of the live dashboard")
    return parser.parse_args(argv)


def _bring_existing_instance_forward(port: int) -> None:
    """A second launch asks the first one to show itself. That is a request like any
    other now, so it carries the same shared secret."""
    import urllib.request

    from assistant.paths import APP_DIR
    from assistant.ui import auth

    token = auth.read(APP_DIR / "data" / "nova_token")
    headers = {auth.HEADER_NAME: token} if token else {}
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/api/show", method="POST", headers=headers),
            timeout=3)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    log_path = _ensure_output_streams()

    # Sub-commands shared by `python main.py ...` and nova-cli.exe.
    if argv and argv[0] == "sysindex":
        from assistant.system.__main__ import main as sysindex_main

        return sysindex_main(argv[1:])
    if argv and argv[0] in ("ui", "macro", "word"):
        from assistant.automation.__main__ import macro_main, ui_main, word_main

        return {"ui": ui_main, "macro": macro_main, "word": word_main}[argv[0]](argv[1:])
    if argv and argv[0] == "models":
        from assistant.agents.chat_api import GroqAgent, OpenRouterAgent, list_models
        from assistant.config import load_settings

        settings = load_settings()
        which = (argv[1] if len(argv) > 1 else "groq").lower()
        agent_type = OpenRouterAgent if which.startswith("open") else GroqAgent
        config = settings.agents.openrouter if agent_type is OpenRouterAgent else settings.agents.groq
        print(list_models(agent_type(config, settings.workspace, False, settings.assistant.name)))
        return 0
    if argv and argv[0] == "stats":
        from assistant.stats import main as stats_main

        return stats_main(argv[1:])
    if argv and argv[0] in ("voice-check", "voicecheck"):
        from assistant.persona.__main__ import main as drift_main

        return drift_main(argv[1:])
    if argv and argv[0] == "voices":
        from assistant.audio.tts import list_installed_voices

        print(list_installed_voices())
        return 0
    if argv and argv[0] == "demo":
        from assistant.ui.demo import main as demo_main

        return demo_main(argv[1:])
    if argv and argv[0] == "autostart":
        from assistant.autostart import main as autostart_main

        return autostart_main(argv[1:])

    args = parse_args(argv)
    from assistant.config import load_settings
    from assistant.ui.win32 import acquire_single_instance

    settings = load_settings(args.config)
    if args.mode:
        settings.ui.mode = args.mode
    if args.backend:
        settings.agents.default = args.backend
    if args.workspace:
        settings.agents.workspace = args.workspace
    if args.no_web:
        settings.ui.web_dashboard = False
    if args.no_browser:
        settings.ui.open_browser = False
    if args.no_dashboard or log_path:
        settings.ui.terminal_dashboard = False

    if not args.text and not acquire_single_instance():
        # Nova is already running (e.g. started at logon): show it instead of starting a second copy.
        _bring_existing_instance_forward(settings.ui.web_port)
        print("Nova is already running; brought its window forward.")
        return 0

    from assistant.app import VoiceAssistant

    assistant = VoiceAssistant(settings, text_mode=args.text)
    assistant.run()
    if assistant.shell:
        # Nova's own cleanup is done (window closed, agent stopped, index committed). Normal
        # interpreter teardown then spends ~15 s finalizing the .NET runtime pywebview loads
        # for WebView2, so skip it.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
