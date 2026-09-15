"""Instructions every agent session receives when it's driven by voice.

Inside this repo Claude Code also loads CLAUDE.md and Antigravity loads
AGENTS.md on their own. Elsewhere they see neither, so the compact
quick-reference section of AGENTS.md is included as well. AGENTS.md stays the
single source of truth for machine rules.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ..paths import APP_DIR, cli_command, main_command, resource_path

AGENTS_FILE = resource_path("AGENTS.md")
# How AGENTS.md spells the commands; swapped for this install's real paths (e.g. nova-cli.exe).
_REPO_CLI = r"python C:\Assisstant\sysindex.py"
_REPO_MAIN = r"python C:\Assisstant\main.py"
_QUICKREF = re.compile(r"<!-- quickref:start -->(.*?)<!-- quickref:end -->", re.S)

VOICE_INSTRUCTIONS = """You are being driven by voice through a speech assistant called {name}. The user speaks, a speech recognizer transcribes it, and your final text reply for each turn is read aloud.

- Act on requests directly: use your tools to do the work instead of describing what you would do. Ask one short question only when the request is truly ambiguous or the action is destructive.
- Transcripts can contain misheard words ("cloud" for Claude, "get hub" for GitHub). Interpret them sensibly.
- Your final reply is spoken: one to three short sentences in plain words. No markdown, bullet lists, code, tables or long file paths.
- If the user asks you to plan something rather than do it, don't change anything. Reply with the plan in exactly this form, followed by one spoken sentence. Steps run after the previous one unless you name their prerequisites with "(after n, m)", which lets independent steps branch:
[[PLAN: short title]]
1. first step
2. second step
3. third step (after 1)
[[/PLAN]]
- When executing a numbered plan, print [[STEP n START]] as you begin each step and [[STEP n DONE]] or [[STEP n FAILED]] when it ends.
- To open apps or find files, folders or duplicates on this computer, first run: {cli} find|open|dupes ... (details in {agents_file}).
- To click buttons or type in a desktop app, use {main} ui controls|click|type|keys "<window title>" ... (UI Automation by control name, never pixel positions). For websites use the Playwright browser tools. Saved automations: {main} macro list / macro run "<phrase>"."""


@lru_cache(maxsize=1)
def quick_reference() -> str:
    try:
        match = _QUICKREF.search(AGENTS_FILE.read_text(encoding="utf-8"))
    except OSError:
        return ""
    if not match:
        return ""
    return match.group(1).strip().replace(_REPO_CLI, cli_command()).replace(_REPO_MAIN, main_command())


def session_instructions(assistant_name: str, workspace: Path) -> str:
    text = VOICE_INSTRUCTIONS.format(name=assistant_name, cli=cli_command(), main=main_command(), agents_file=AGENTS_FILE)
    try:
        inside_repo = workspace.resolve() == APP_DIR and (APP_DIR / "AGENTS.md").exists()
    except OSError:
        inside_repo = False
    ref = "" if inside_repo else quick_reference()
    return f"{text}\n\n{ref}" if ref else text
