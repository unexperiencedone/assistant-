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
| "Status" / "cancel the task" / "cancel all tasks" / "stop" | Progress on every running task, kill one or all of them, stop talking |
| "Move step 2 down" / "move step 1 to 3" | Rearrange the plan (or drag the step on the canvas) |
| "Use Antigravity" / "use Claude" / "new session" | Switch agent / start a fresh conversation |
| "Open Spotify" / "find my resume" / "open 2" | Local index, instant; falls through to Claude if nothing matches |
| "Find duplicates in downloads" / "my most used apps" | Local duplicate scan / frecency list |
| "Show the canvas" | Opens the canvas window (or browser tab) |
| "Goodbye" | Quits Nova |

Set `wake_word = "nova"` in `config.toml` if it should only react when addressed.

## The canvas in detail

- **Click any node** for an inspector panel with the full, untruncated text: every action with its complete command or path, the agent's whole reply, and copy buttons. Nothing is shortened except for display.
- **Every turn is logged raw** to `data/sessions/<date>/<backend>/turn-NN.jsonl` (the agent's complete stream), so the record survives restarts and truncation. `keep_days` in `[sessions]` prunes old ones.
- **History rail** keeps the last 20 requests; click one to see its graph again.
- **Several tasks at once, within reason:** errands — open a file, search for something, play a song, send a mail — start straight away even while something else is working. Big jobs (write, tidy, refactor, research, running a plan) never double up: ask for a second one and Nova says "that's a big one, so I'll start it once X is done" and queues it. Each running task gets its own agent session, canvas lane and tab, so the conversations never mix. Only one task talks out loud; the others report when they finish ("write the report: done…"). "Cancel the task" stops the one you're following, "cancel all tasks" stops everything. `max_parallel` under `[agents]` (default 2) caps the total; the sorting lives in `assistant/triage.py`.
- **Rearrange the plan** by dragging a step to a new position, with the inspector's Move up/down buttons, or by saying "move step 2 down" or "move step 1 to 3". Dependencies are renumbered to follow their steps, and a dependency that would point forwards is dropped.
- **Cost meter** shows what the session has cost and how many turns it took. **Blocked actions** get their own banner instead of a grey log row.
- **Mic level** while listening, **minimap** for big plans, **theme toggle** (system/light/dark), **filterable activity feed** with a short/full text switch, and **quick action chips** that adapt to the automations and commands you actually use.
- Conversation and plan are **restored after a restart**.

## Voice

Nova speaks with the Windows voice (SAPI), and only that. Free cloud voices sound better for about four sentences and then rate-limit, which leaves the assistant silent in the middle of a reply; a local voice is instant, free and never fails.

```toml
[voice]
engine = "auto"                # auto | sapi (Windows) | pyttsx3
voice_contains = "Zira"        # the installed voices: python main.py voices
rate = 0                       # -10..10
```

- **"Stop" still works**, cutting speech within about 0.2 s.
- **Punctuated for the ear.** Every brain is told to write commas where a person would pause, and Nova normalizes what it says: markdown removed, line breaks become sentence breaks, "50%" becomes "50 percent", `notes.txt` stays one word. Speech engines take their rhythm from punctuation, so this is most of what makes a local voice sound human.

## Four brains: Claude Code, Groq, OpenRouter, Antigravity

Say **"use claude"**, **"use groq"**, **"use openrouter"** or **"use antigravity"** to switch. The canvas header shows which one is active.

| | What it is | Use it for |
|---|---|---|
| **Claude Code** (default) | A brain with hands already attached: terminal, file editing, web. Runs on your Claude subscription. | Coding, editing files, multi-step work |
| **Groq** | Open models on Groq's own chips, answering in about a second, acting through *Nova's* tools. | Chat, quick questions, opening things, clicking in apps, automations, Word documents |
| **OpenRouter** | The same loop against OpenRouter's free models. | A fallback when Groq is rate-limited |
| **Antigravity** | Google's `agy` CLI. | An alternative coding agent |

Groq and OpenRouter are only model APIs, so Nova supplies the loop and the hands: `find_items`, `open_item`, `find_duplicates`, `list_windows`, `list_controls`, `click_control`, `type_in_app`, `press_keys`, `read_control`, `list_automations`, `run_automation`, `write_word`, and `delegate_to_claude`. That last one is the rule that keeps it sane: anything needing a terminal, file edits or several steps goes to Claude Code rather than being reinvented with a small model.

```toml
[agents.groq]
model = "openai/gpt-oss-120b"    # what this key can run: python main.py models
api_key_env = "GROQ_API_KEY"
max_tool_calls = 6

[agents.openrouter]
model = "openrouter/free"        # or "nvidia/nemotron-3.5-lightning:free"
api_key_env = "OPENROUTER_API_KEY"
```

Both keys go in a `.env` file next to `config.toml` (gitignored, never committed):

```
GROQ_API_KEY=gsk_...
OPENROUTER_API_KEY=sk-or-v1-...
```

**Pick a model that supports tool calling**, or it can only talk. On Groq today that means `openai/gpt-oss-120b` (default), `qwen/qwen3.8-27b` or `openai/gpt-oss-20b` — Llama chat models aren't offered on this key, and the Llama-based `groq/compound` models reject tool calls outright. `python main.py models` (add `openrouter` for the other provider) lists what the key can actually run, which is the only reliable answer.

Measured on Groq: "capital of Japan" answered in 0.5 s, a `find_items` lookup in 2 s, two errands in parallel both answered in about 2 s. Free tiers rate-limit, so expect the occasional "Groq is rate limiting me right now", which Nova says out loud; if a model garbles a tool call, the turn is retried once without tools so you still get an answer.

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

---

# Reproducing this setup from scratch

Everything below has been run on the machine Nova was built on. Where a step cannot be
verified from here — anything on the phone — it says so, and says what "working" looks
like, so you can tell the difference between done and probably done.

## 1. Prerequisites

| Needs | Why | Check |
|---|---|---|
| Windows 10/11 | UI Automation and the tray live here | — |
| Python 3.11 | everything | `python --version` |
| `claude` CLI, logged in | the main brain; uses your subscription, not an API key | `claude -p "say hi"` |
| Node 18+ | only to rebuild the canvas | `node --version` |
| A microphone | only for voice; `--text` works without one | — |

Optional: `agy` (Antigravity) as an alternative agent, Tailscale for the phone, Termux +
Termux:API on an Android phone.

## 2. Install

```powershell
git clone <this repo> C:\Assisstant
cd C:\Assisstant
pip install -r requirements.txt
```

The first voice run downloads the Whisper `base.en` model, about 150 MB, once.

## 3. Secrets

Copy `.env.example` to `.env` and fill in only what you need. **Nothing in it is
required to start Nova** — each block switches on one capability, and a missing key
disables that feature rather than breaking startup.

```powershell
copy .env.example .env
```

- `GROQ_API_KEY` — free. Enables the intent router (see *Routing*, below). Without it
  Nova still works; unrecognised phrasings just go to Claude instead.
- `OPENROUTER_API_KEY` — free models as an alternative brain.
- `NOVA_PHONE_TOKEN` — only for the phone bridge, printed by the bridge on the phone.

`.env` is git-ignored. So is `data/`, which holds your profile, tokens and counts.
**A file named `env` (no dot) is not ignored by default** — don't create one; that
mistake was made here and it held a live token.

## 4. First run

```powershell
python main.py --text --no-browser     # simplest: typed input, no canvas, no mic
python main.py                         # the real thing
```

Say or type `status`. If it answers, the loop works.

## 5. Checks

```powershell
python -m unittest discover -s tests   # 267 tests, ~12s
python -m compileall -q assistant
python main.py stats                   # module and line counts
python main.py stats --routes          # which routes answered, by week
```

`stats --routes` is the one worth watching over time — see *Routing*.

## 6. The canvas

The built canvas ships in `assistant/ui/canvas_dist/`, so a fresh clone needs no Node.
Only rebuild after editing `canvas/src/`:

```powershell
cd canvas
npm install
npm run build
```

Use **PowerShell** for this, not Git Bash: Git Bash resolves a broken `node` shim here.

## 7. Routing, and what to watch

A request falls through four tiers, cheapest first. Only the last one costs anything:

| Tier | Where | Speed |
|---|---|---|
| Strict intent (regex) | `assistant/intents.py` | microseconds |
| Saved automation (TOML) | `automations/` | microseconds |
| Loose match — filler, synonyms, word order | `assistant/matching.py` | microseconds |
| Free-model router, grounded in the script list | `assistant/classify.py` | ~1s |
| Claude | `assistant/agents/` | seconds |

`python main.py stats --routes` prints the share that never reached a model, one bar per
week. **That chart is the design's only real scoreboard.** If the bar does not grow as
you use it, the automation-promotion loop is not working, and `docs/overview.md` says so
rather than pretending otherwise.

Turn the two middle tiers off with `[assistant] loose_matching = false` and
`loose_classifier = false` in `config.toml`.

## 8. The phone bridge (optional)

Full instructions in `phone/README.md` and `docs/phone.md`. The short version:

1. Install **Termux** and **Termux:API** from **F-Droid** — both, and from the same
   source, or the signatures will not match and the API calls fail silently.
2. `pkg install python termux-api`
3. Install Tailscale on the PC and the phone; both must show in `tailscale status`.
4. Fetch the listener from Nova itself (no cable, no scp):
   ```bash
   python - <<'PY'
   import urllib.request
   r = urllib.request.Request("http://<pc-tailscale-ip>:8765/api/phone/bridge",
                              headers={"X-Nova-Token": "<PC token from data/nova_token>"})
   open("nova_bridge.py", "w").write(urllib.request.urlopen(r).read().decode())
   PY
   ```
5. `termux-wake-lock && python nova_bridge.py`
6. Put the token it prints into `.env` as `NOVA_PHONE_TOKEN`, and the phone's Tailscale
   IP into `[phone] host` in `config.toml`.

**Two Android settings decide whether half of it works**, and neither is obvious:

- **Battery → Unrestricted** for Termux, plus `termux-wake-lock`, or Android freezes the
  listener and every request fails with a connection error while `tailscale ping` still
  answers.
- **Display over other apps** (on HyperOS/MIUI: *Other permissions → Display pop-up
  windows while running in background*). Without it, Android silently drops any action
  that starts an activity — opening an app, placing a call — while the screen is locked.
  The command still exits 0. This cost an afternoon to find.

Grant Termux:API its Contacts, SMS and Phone permissions too, or those actions return
empty results rather than errors.

### Verifying the phone

```powershell
python -c "from assistant.config import load_settings; from assistant.phone import PhoneBridge; print(PhoneBridge(load_settings().phone).call('battery'))"
```

Then, with the phone **locked**, say "find my phone" — it should speak, not just buzz.
Working locked: torch, battery, vibrate, notify, clipboard, volume, location, contacts,
SMS, calls, find-my-phone.

## 9. Things that will bite

| Symptom | Cause |
|---|---|
| Phone requests fail, but `tailscale ping` answers | Android froze Termux. `termux-wake-lock`, battery unrestricted. |
| A call or app-open reports success and nothing happens | Background activity starts are blocked. See the permission above. |
| "find my phone" buzzes but does not speak | An older bridge on the phone. Re-fetch `nova_bridge.py`. |
| 401 from the phone | `NOVA_PHONE_TOKEN` in `.env` does not match the phone's `~/.nova_bridge_token`. |
| 401 opening the canvas | Open it with `?token=` once; it leaves a cookie. Token is in `data/nova_token`. |
| Canvas edits do nothing | `npm run build` not run, or run from Git Bash. |
| An automation fails on one step | Expected: Claude finishes the request, then patches the automation. See AGENTS.md §12. |

## 10. Where to read next

| File | What it covers |
|---|---|
| `docs/overview.md` | the whole system: architecture, abilities, security, honest limits |
| `AGENTS.md` | the rules any coding agent follows in this repo |
| `automations/README.md` | the TOML automation format |
| `docs/phone.md`, `phone/README.md` | the phone bridge end to end |
| `docs/personalization.md`, `docs/history.md` | profile and work history |
| `docs/document_standards.md` | how written documents and diagrams are produced |
| `docs/business_pipeline.md` | a designed-but-unbuilt outreach pipeline |
