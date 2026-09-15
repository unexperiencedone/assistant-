# Nova: talk to Claude Code

Say what you want and it gets done. Nova keeps one live **Claude Code** session open (`claude -p` in stream-json mode) and sends everything you say straight into it. Claude does the work with its tools and its short reply is read aloud. It runs on your Claude subscription through the CLI's own login: **no API key, no API billing**.

A **live canvas** shows each request as a graph while it happens: what you said, how it was routed, the plan steps (with branches), each action Claude takes, and the result. Nodes change color as they go from pending to running to done or failed.

Instant commands (stop, cancel, switch agent, open an app, find a file) are handled locally in milliseconds and use none of your Claude usage. **Antigravity** (`agy`) can be used instead of Claude.

```
python main.py                        # microphone + canvas in your browser + terminal dashboard
python main.py --mode window          # canvas in a native window, with a tray icon
python main.py --tray                 # start hidden in the tray (Ctrl+Alt+N shows the canvas)
python main.py --text                 # type instead of talking
python main.py demo                   # scripted canvas demo: no mic, no Claude usage (--loop, --speed 2)
python main.py sysindex find "invoice" --kind file   # local index CLI (also used by agents)
python main.py autostart install      # start Nova in the tray at every logon
```

Install: `pip install -r requirements.txt`. The first voice run downloads the Whisper `base.en` model (about 150 MB). Make sure `claude` works in a terminal first.

## Things to say

| You say | What happens |
|---|---|
| "Create a notes file with a shopping list" / "fix the failing test" / anything | Sent straight to Claude Code, which does it; its reply is spoken |
| (while it's working) "also check the python version" | Queued and sent as soon as the current turn ends |
| "Let's plan a notes app with login" | Planning mode: Claude drafts a plan without changing anything; it appears on the canvas as a graph |
| "Remove step 2" / "read the plan" / "go ahead" | Edit or read the plan locally, then run it; steps light up as Claude works |
| "Status" / "cancel the task" / "stop" | Progress report, kill the turn (the conversation resumes next time), stop talking |
| "Use Antigravity" / "use Claude" / "new session" | Switch agent / start a fresh conversation |
| "Open Spotify" / "find my resume" / "open 2" | Local index, instant; falls through to Claude if nothing matches |
| "Find duplicates in downloads" / "my most used apps" | Local duplicate scan / frecency list |
| "Show the canvas" | Opens the canvas window (or browser tab) |
| "Goodbye" | Quits Nova |

Set `wake_word = "nova"` in `config.toml` if it should only react when addressed.

## Clicking buttons in apps and web pages

Three ways, from fastest to most flexible:

| | How | Speed / cost |
|---|---|---|
| **Automations** | TOML scripts in `automations/`, triggered by their phrases ("calculator demo", "search wikipedia for black holes", "next song"). Each step lights up on the canvas; a failed step turns red and says why. | ~1-8 s, no Claude usage |
| **Claude + desktop apps** | Just ask ("in Calculator, work out 12 times 4"). Claude lists the window's controls and clicks them by name with `main.py ui ...` (Windows UI Automation, no pixel guessing). | ~45 s |
| **Claude + websites** | Just ask. Claude drives a browser with the Playwright MCP tools already in your Claude Code setup. | ~20 s+ |

```
python main.py ui controls "Calculator" --filter plus   # Type, Name, AutomationId of clickable controls
python main.py ui click "Calculator" "Five"
python main.py macro run "search wikipedia for ada lovelace"
```

See `automations/README.md` for the step reference (desktop: `open`, `click`, `type`, `keys`, `read`, `media`; web: `browser_goto`, `browser_click`, `browser_fill`...). Web steps drive your installed Edge with a separate Nova profile, and the window stays open afterwards.

## Permissions

`[agents.claude] permission_mode = "auto"` (default): Claude Code approves safe actions itself, including shell commands, and runs them. Risky ones are denied, and Claude tells you, so you can answer by voice. `acceptEdits` allows file edits only (shell commands are denied headless). `bypassPermissions` skips all checks, so use it with care. To always allow specific tools, add entries like `"Bash(npm test:*)"` to `allowed_tools`.

## Pipeline

```
voice / canvas text ─▶ intents.py (classify: local command or agent)
                         ├─ local ─▶ system/ (index, open, dupes)          ─┐
                         └─ agent ─▶ Claude Code session (plans as a DAG)   │
                                       └─▶ agent_runner.py (execute, infer   │
                                           which step each action is on)    │
event bus ─▶ planning/graph.py (nodes + edges) ─▶ ui/server.py /ws ─▶ React Flow canvas
```

Plans come back from Claude as `[[PLAN: title]] 1. ... 2. ... (after 1) [[/PLAN]]`. A step without `(after ...)` depends on the previous one. While executing, Claude prints `[[STEP n START]]` / `[[STEP n DONE]]`. Models often run several steps' actions back to back and print markers only at the end, so the runner also matches each action's target (such as `style.css`) against the step texts to light up the right step live.

## Desktop app

- **Window mode** hosts the canvas in a native WebView2 window (pywebview). Closing the window hides it to the tray instead of quitting.
- **Tray icon** (pystray): its ring color follows Nova's status (green listening, amber thinking, violet working, slashed when muted). The menu has Show canvas, Mute microphone, Cancel task and Quit.
- **Hotkey**: `hotkey = "ctrl+alt+n"` under `[ui]` shows or hides the window.
- **Single instance**: launching Nova while it's running just brings the existing window forward.

## Packaging and autostart

```
powershell -ExecutionPolicy Bypass -File packaging\build.ps1     # -> dist\Nova\
dist\Nova\nova-cli.exe autostart install                         # Task Scheduler, at logon, tray only
dist\Nova\nova-cli.exe autostart status | remove
```

`dist\Nova\` is a PyInstaller `--onedir` build containing `Nova.exe` (no console: tray and window) and `nova-cli.exe` (console: `--text`, `sysindex`, `autostart`). Settings are in `dist\Nova\config.toml`, and logs go to `%LOCALAPPDATA%\Nova\nova.log`. The autostart task starts 20 s after logon, restarts Nova up to 3 times if it crashes, and runs with normal user rights (no UAC prompt).

## How it's put together

```
main.py                   CLI flags and sub-commands -> Settings -> VoiceAssistant
sysindex.py               local index CLI, usable from any folder
canvas/                   React Flow + dagre frontend (npm run build -> assistant/ui/canvas_dist)
packaging/                nova.spec + build.ps1 (PyInstaller)
assistant/
  app.py                  wiring; worker thread for the input queue, main thread for the window/tray
  controller.py           wake word -> instant local commands -> agent session; queues, planning mode
  intents.py              regex command matching (~0 ms, no model call)
  agent_runner.py         one agent turn at a time, plan markers + step inference, spoken progress
  events.py / state.py    pub/sub bus and the snapshot the UIs render
  config.py / paths.py    typed settings; source vs packaged-exe paths
  autostart.py            Task Scheduler install/remove/status
  audio/                  microphone.py, transcriber.py (Whisper/Google), tts.py (SAPI/pyttsx3)
  agents/                 claude_cli.py (live session), antigravity_cli.py, rules.py, registry.py
  planning/               plan.py (DAG plan, markers), graph.py (live nodes + edges)
  system/                 SQLite index: db.py, indexer.py, apps.py, search.py, ranking.py,
                          duplicates.py, service.py, voice.py, __main__.py (CLI)
  ui/                     server.py (FastAPI + WebSocket), shell.py (window + tray),
                          win32.py (hotkey, single instance), icon.py, console.py (rich)
AGENTS.md / CLAUDE.md     machine rules for agents: cheapest commands for find/open/dupes
```

**Latency.** The Claude session starts when Nova starts, so it's ready when you first speak. Each request is a new turn in the same process: no CLI start-up cost, and Claude remembers the whole conversation. In testing, simple questions took about 2 s and small file tasks 6–15 s. If Claude hasn't answered after 1.5 s, Nova says "On it." Setting `model = "sonnet"` makes turns faster.

**Local index and ranking.** Apps come from `Get-StartApps` and launch via `shell:AppsFolder`; files and folders come from your user folders, with FTS5 trigram search. Ranking combines match quality with frecency (each use adds 1 point, and points halve every `half_life_days`). Repeat commands skip search through an in-memory cache backed by SQLite. Re-indexing is incremental (unchanged folders aren't listed again). Duplicates are narrowed by size, then first/last 64 KB, then a full BLAKE2b hash, with hashes cached.
