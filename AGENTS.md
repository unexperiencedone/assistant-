# Agent rules: Nova voice assistant (C:\Assisstant)

These rules are for any coding agent working on this machine (Claude Code, Antigravity, Codex).
Goal: get the answer with the fewest, cheapest commands. Every command and every line of output costs tokens.

<!-- quickref:start -->
## Local quick reference (read before running any search or launch command)

- Machine: Windows 11. Shells: PowerShell 5.1 and Git Bash. Python 3.11 is `python`.
- Desktop and Documents are redirected to OneDrive: `C:\Users\kumar\OneDrive\Desktop` and `...\OneDrive\Documents`. Downloads is `C:\Users\kumar\Downloads`. Never guess `$HOME\Desktop`.
- Not installed: `rg`, `fd`, `es` (Everything), `fzf`. Don't try them.
- A SQLite index of apps, files and folders, ranked by how often and how recently they're used, answers in about 0.5 s. Use it first:
  - `python C:\Assisstant\sysindex.py find "<name>" [--kind app|file|folder] [--ext pdf] [--under <dir>] [--limit 5]`
  - `python C:\Assisstant\sysindex.py open "<name>" [--kind app|file|folder]` (opens the best match and remembers it)
  - `python C:\Assisstant\sysindex.py dupes "<folder>" [--min-size 1MB]` (duplicate report, never deletes)
  - `python C:\Assisstant\sysindex.py reveal "<path>"` · `used "<path>"` · `top --kind app` · `stats` · `index`
  - Output is one `kind<TAB>path` per line. Exit code 1 means not found; fall back to the recipes in AGENTS.md.
- Clicking inside apps: desktop apps via `python C:\Assisstant\main.py ui ...` (UI Automation), websites via the Playwright MCP tools, repeatable tasks via `python C:\Assisstant\main.py macro run "<phrase>"`. Never click by pixel coordinates.
- Never delete, move or overwrite user files without explicit confirmation. Use the Recycle Bin, not permanent delete.
- Creating written documents (articles, reports, blogs, Word docs): follow the formatting and diagram placement rules in section 11 and `docs/document_standards.md`. Always introduce visuals in text, format captions with takeaways, and pace diagrams every 300-500 words.
<!-- quickref:end -->

## 1. Decision order for "find / open / where is"

1. **Index**: `sysindex.py find` or `sysindex.py open`. Covers Start Menu apps (including Store apps), Desktop, Documents, Downloads, Pictures, Music, Videos and OneDrive.
2. **Known location**: if the user named a folder, search only that folder with a bounded command (section 3).
3. **Widen one level at a time**: Downloads, then OneDrive, then other user folders. Never the whole drive unless the user asks.
4. If a lookup fails twice, stop and ask the user for a hint. Don't loop through variations.

After opening something by any other route, run `sysindex.py used "<path>"` so its ranking learns.

## 2. Opening things

| Target | Command |
|---|---|
| App by name | `python C:\Assisstant\sysindex.py open "spotify" --kind app` |
| App not in index | `Get-StartApps \| Where-Object Name -like '*spot*' \| Select-Object -First 5` then `explorer.exe "shell:AppsFolder\<AppID>"` |
| Executable on PATH | `where.exe code` then `Start-Process code` |
| File (default app) | `Start-Process "C:\path\file.pdf"` or `Invoke-Item "C:\path\file.pdf"` |
| Folder | `explorer.exe "C:\path\folder"` |
| Select a file in Explorer | `explorer.exe /select,"C:\path\file.txt"` (no space after the comma) |
| Settings page | `Start-Process ms-settings:display` (or `bluetooth`, `sound`, `network-wifi`, `windowsupdate`, `startupapps`) |
| URL | `Start-Process "https://example.com"` |
| Project in VS Code | `code "C:\path\project"` |

Don't search `Program Files` for .exe files. `Get-StartApps` already knows every installed app.

## 3. Finding files and folders (when the index misses)

PowerShell 5.1, fastest first:

```powershell
# By name in one folder tree. -Filter runs in the file system provider: much faster than -Include or Where-Object.
Get-ChildItem -Path "C:\Users\kumar\Downloads" -Recurse -File -Filter '*invoice*' -ErrorAction SilentlyContinue |
  Select-Object -First 20 -ExpandProperty FullName

# Fastest raw name listing for big trees (cmd's dir, names only)
cmd /c 'dir /s /b /a-d "C:\Users\kumar\OneDrive\*invoice*.pdf" 2>nul' | Select-Object -First 20

# Folders, depth-limited, without dependency noise
Get-ChildItem -Path "C:\Users\kumar\OneDrive" -Recurse -Directory -Depth 4 -Filter '*python*' -ErrorAction SilentlyContinue |
  Where-Object FullName -notmatch '\\(node_modules|\.git|\.venv|__pycache__)\\' |
  Select-Object -First 20 -ExpandProperty FullName

# Recently changed / largest files (swap the Sort-Object key)
Get-ChildItem "C:\Users\kumar\Downloads" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name, LastWriteTime
```

Git Bash equivalent: `find /c/Users/kumar/Downloads -iname '*invoice*' -not -path '*/node_modules/*' 2>/dev/null | head -20`

Searching inside files:
- Claude Code: use the built-in Grep and Glob tools, not shell grep.
- PowerShell: `Select-String -Path "C:\proj\src\*.py" -Pattern 'def main' -List | Select-Object -First 20 Path, LineNumber`
- Git repos: `git grep -n "pattern" -- '*.py' | head -20` is fast and respects .gitignore.

## 4. Duplicate files

1. `python C:\Assisstant\sysindex.py dupes "C:\Users\kumar\Downloads" --min-size 100KB`. It groups by size, compares the first and last 64 KB, then hashes (BLAKE2b, cached in SQLite), so repeat runs are nearly instant. Don't hand-roll a duplicate scan.
2. Report first. Delete only the copies the user confirms, and send them to the Recycle Bin:
```powershell
Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("<path>", 'OnlyErrorDialogs', 'SendToRecycleBin')
```

## 5. Other common system questions

| Question | Command |
|---|---|
| Known folder path | `[Environment]::GetFolderPath('Desktop')` (also `MyDocuments`, `MyPictures`, `MyMusic`, `MyVideos`) |
| Folder size | `"{0:N1} MB" -f ((Get-ChildItem "<dir>" -Recurse -File -EA SilentlyContinue \| Measure-Object Length -Sum).Sum / 1MB)` |
| What's on a port | `Get-NetTCPConnection -LocalPort 8000 -State Listen \| Select-Object OwningProcess` then `Get-Process -Id <pid>` |
| Stop a process | `Stop-Process -Id <pid>` (ask before killing anything you didn't start) |
| Install an app | `winget search <name>` then `winget install --id <Id> -e` (ask first) |

## 6. Clicking buttons in apps and web pages

1. **Existing automation?** `python C:\Assisstant\main.py macro list`, then `macro run "<phrase>"`. Steps live in `automations/*.toml` (format: `automations/README.md`).
2. **Websites:** Playwright MCP tools (navigate, snapshot, click by ref). Don't write Selenium or Playwright scripts for one-off tasks.
3. **Desktop apps** (Calculator, Spotify, Notepad, Settings): UI Automation by control name.
   - `main.py ui controls "<window title>" --filter <word>` prints `Type<TAB>Name<TAB>AutomationId`
   - `main.py ui click "<window>" "<Name>"` (or `--auto-id <id>`) · `ui type "<window>" "<text>" [--into "<field>"]` · `ui keys "<window>" "^l"` · `ui read "<window>" --auto-id <id>`
   - List once with `--filter`, then act by exact name. No pixel clicks or blind keystrokes.
4. **Word documents:** write the content as Markdown to a .md file, then `main.py word write --file x.md --title "..."` (Word's COM API: opens the doc in Word, saves to Documents). Never type an article through Word's UI.
5. **Repeated task:** save it as `automations/<name>.toml` so it runs next time without a model. This is now mostly automatic -- see section 12: once a kind of request repeats enough, or the user says "remember that", you'll be asked to do this yourself.
6. Ask before anything that buys, sends messages or submits forms for the user.

## 7. Token-saving rules

- **Cap every listing.** Use `Select-Object -First N`, `| head -N`, `--limit N`, or `-List` on Select-String. Ask for names only (`-ExpandProperty FullName`, `dir /b`), not full tables.
- **Scope before recursing.** Narrow the folder and add `-Depth`, `-File` or `-Directory`, and `-Filter`. `-ErrorAction SilentlyContinue` hides access-denied noise.
- **Don't re-run to re-read.** Store results in a variable (`$r = ...`) in the same command, or write them to a file and read that.
- **One attempt per approach.** If a command errors, read the message and fix the cause. Don't retry it unchanged, and don't cycle through shells.
- **Check a tool exists** (`where.exe x`) before relying on it.
- **Don't read what you don't need.** Check file size first, never print binaries, and use `-TotalCount` or `-Tail` for logs.
- **Batch independent reads** into one command instead of many round trips.

## 8. Shell pitfalls on this machine

- PowerShell 5.1 has no `&&`, `||`, ternary or `??`. Chain with `A; if ($?) { B }`.
- Quote paths with spaces. PowerShell uses `C:\...`, Git Bash uses `/c/...`. Don't mix them in one command.
- In PowerShell, `curl` and `wget` are aliases for Invoke-WebRequest. Use `curl.exe` for real curl.
- Native tools called from PowerShell 5.1 can mangle embedded double quotes in arguments. Pass JSON through a file or call from Python.
- `Set-Content` defaults to ANSI. Use `-Encoding utf8` when writing files other tools will read.
- `explorer.exe` returns exit code 1 even on success. That's normal.

## 9. Safety

- Ask before deleting, moving, renaming or bulk-editing user files, before killing processes you didn't start, and before installing software.
- Never modify `C:\Windows`, `C:\Program Files*` or registry keys unless explicitly asked.
- Don't open or upload files containing secrets (`.env`, key files, password exports). Mention them without printing their contents.

## 10. Working on this repository

Python 3.11 voice assistant. Entry point `main.py`, settings in `config.toml` → `assistant/config.py`.

| Area | Where (under `assistant/`) |
|---|---|
| Event bus, UI state (versioned slices, patched to the canvas) | `events.py`, `state.py` |
| Mic, speech-to-text, TTS, noise gate | `audio/` (`gate.py` drops garbage transcripts) |
| Instant commands (regex) | `intents.py`, handled in `controller.py` (`_intent_<name>`) |
| Agents: live `claude -p`, `agy -p`, Groq + OpenRouter (`chat_api.py` + `tools.py`) | `agents/` |
| Task pool (parallel tasks, errand/big-job triage), plan DAG, live graph (per-task lanes) | `agent_runner.py`, `triage.py`, `planning/` |
| Raw per-turn agent logs, restart persistence | `sessions.py`, `store.py` |
| Work history: timed sessions (idle close, never mid-task), search, recap (docs/history.md) | `history/`; screen `canvas/src/History.jsx` |
| User profile: schema, visibility per agent, prompt context, speech hints (docs/personalization.md) | `profile/`; editor `canvas/src/Profile.jsx` |
| Briefing panel: the on-ask weather/markets/headlines cache (`data/ambient.json`) | `ambient/service.py`; headlines alone are instant and agent-free via `ambient/news.py` ("news briefing") |
| Standing goals: the queue Nova acts on unprompted, quiet hours, one-at-a-time firing (docs/standing_agent.md) | `goals/` (`store.py`, `service.py`) |
| Capture and edit: screen recording (ffmpeg gdigrab), phone camera, local cut/caption/reframe | `capture/` (`screen.py`, `phone.py`, `edit.py`, `inbox.py`) |
| Publishing: GitHub (gh CLI), LinkedIn, Instagram, and the approval gate in front of all three (docs/publishing.md) | `publish/` (`gate.py` stages and sends, `service.py` wires the arms) |
| Voice drift check: fixed probes scored against the character, `python main.py voice-check` | `persona/drift.py`, `persona/__main__.py` |
| Journal: one entry per finished day, written from history + awareness, fed back as continuity (docs/journal.md) | `journal/` (`writer.py` builds the facts, `service.py` writes and serves `context()`) |
| Activity awareness: window-title probe, folding into sessions, retention, questions | `awareness/` (`collector.py`, `collapse.py`, `store.py`); screen `canvas/src/Awareness.jsx` |
| Phone control: torch, battery, open, notify, calls and SMS (both confirmed first) over Tailscale (phone/README.md) | `phone/bridge.py`; the listener is `phone/nova_bridge.py`, run on the phone; automations reach it with `do = "phone"`; reading contacts/SMS/call log is gated by the Phone section of the profile |
| Web reach for the chat models: keyless search and a page reader, so a lookup never needs Claude | `agents/web.py`; tools `web_search` / `read_page` in `agents/tools.py` |
| Agent skills: the same `SKILL.md` folders Claude Code reads, indexed by name and loaded on demand (`[skills]` paths) | `agents/skills.py`; tools `list_skills` / `read_skill`; `suggest()` names the fitting skill because models won't notice one themselves |
| Recipes: how a job went when it went well, fed back on the next similar request (docs/recipes.md) | `recipes/` (`store.py`, `service.py`); recorded in `controller.on_agent_finished`, injected in `controller.dispatch` |
| Local index (SQLite, per-kind frecency, corrections, command chips) | `system/` (`ranking.py`, `commands.py`) |
| UI automation + TOML automations | `automation/` (`desktop.py`, `browser.py`, `macros.py`); files in `automations/` |
| Canvas server / frontend | `ui/server.py`; React source in `canvas/` (`npm run build`) |
| Reaching Nova from a phone: shared-secret token on every request, bind address, Tailscale (docs/phone.md) | `ui/auth.py`; `[ui] host` / `require_token` / `token_file` |
| Window, tray, hotkey | `ui/shell.py`, `ui/win32.py` |
| Paths, packaging, autostart | `paths.py`, `autostart.py`, `packaging/` |

Conventions: small modules with one responsibility each; modules talk through `EventBus` topics, never through each other's UI; type hints; no new dependencies without a reason.
Never add Anthropic API calls: the user has no API billing. Claude work goes through the `claude` CLI session; the other brains are Groq and OpenRouter (free models, keys in `.env`, never in `config.toml`).
Speech is local (SAPI) on purpose: free cloud voices rate-limit mid-reply. Don't reintroduce a cloud TTS.
Adding a voice command: add a rule to `_RULES` in `intents.py` (specific patterns before general ones), then add `_intent_<name>` to `controller.py`, or add it to `LOCAL_INTENTS` with a handler in `system/voice.py`.
Canvas changes: edit `canvas/src/`, then `npm run build` in `canvas/` (use PowerShell: Git Bash resolves a broken `node` shim here).
The canvas follows one design direction, "Living Ink": ink-black ground, Fraunces for anything Nova says or is, IBM Plex Mono for anything a machine measured (paths, figures, timestamps), coral `#ff8a65` and violet `#7c5cff`. Tokens live at the top of `canvas/src/styles.css`; the fonts are self-hosted in `canvas/public/fonts` so it looks the same offline. The blob in `NovaEntity.jsx` is driven by the real status slice (idle/listening/transcribing/thinking/speaking, plus working from `agent_running`) — never a decorative loop.
New UI state: add a slice in `AppState.__init__` and set it in `_on_event`; the server sends only changed slices (and only new rows for `LIST_LIMITS` lists), so never rebuild or resend the whole snapshot.
Personal details belong in the profile (`data/profile.json`, git-ignored), never in `config.toml`, AGENTS.md or code; respect each section's visibility (free cloud models only see "all").
Speech must start with the wake word (`[assistant] wake_word`); typed input never needs it. Record a command for the quick actions only after it succeeded.
Bus events for agent work carry `task=<id>`; the graph keeps one lane per task, so anything new that reports agent progress must pass the task id through or it will land in the wrong lane.
Checks: `python -m unittest discover -s tests`, `python -m compileall -q assistant`, and `python main.py --text --no-browser` for a manual run.

## 11. Document writing, diagramming and formatting standards

Full specification: `docs/document_standards.md`. When asked to produce articles, reports, whitepapers, blogs or Word documents:

1. **Structure by archetype:**
   - **Technical Articles:** Abstract/Hook -> Problem Statement -> System Architecture (block diagram) -> Component deep dive -> Data flow / sequence -> Trade-offs & performance -> Conclusion.
   - **Formal Reports:** Metadata block -> Executive Summary (callout) -> Scope & Methodology -> Numbered Findings with referenced figures -> Risk matrix -> Action plan.
   - **Blog Posts:** Hook -> TL;DR box -> Hero conceptual diagram (within first 2-3 paragraphs) -> Walkthrough with code -> Comparison table -> Actionable CTA.
   - **Executive Briefs:** Strategic challenge -> Solution -> 1 high-impact diagram / ROI table -> Decision roadmap.
2. **Diagram placement (The Golden Rules):**
   - **Proximity:** Always introduce the visual in the text immediately before it appears (`As shown in Figure 1 below, ...`). Never leave an orphaned diagram.
   - **Standalone comprehension:** A reader must understand the visual and its caption without reading the body text.
   - **Captions with takeaways:** Format as `Figure [N]: [Title] — [1-2 sentence takeaway]`. Never use a bare title.
   - **Visual rhythm:** Provide a visual cognitive break (diagram, table, or callout) every 300 to 500 words.
3. **Diagram types:**
   - Architecture / Flow: Mermaid `flowchart TD` or `flowchart LR`.
   - Protocols / Interactions: Mermaid `sequenceDiagram`.
   - State transitions: Mermaid `stateDiagram-v2`.
   - Data schemas: Mermaid `erDiagram`.
   - Comparisons: Formatted Markdown tables.
   - Terminal / CLI: Clean Unicode box-drawing characters (`┌─┐│└─┘`).
4. **Word Documents (`.docx`):**
   - Write content to `.md` first, then run `python C:\Assisstant\main.py word write --file doc.md --title "..."`.
   - Embed diagrams using `![Figure N: Title — Caption](path/to/image.png)`. The Word engine embeds the picture inline and centers the caption below.

## 12. Automation promotion: write yourself out of the job

The goal over time is to spend pennies on tokens, not dollars: the default should shift from "ask Claude" to "run a saved automation" for anything general enough to repeat. Ask Claude directly only when there's no automation yet, the existing one just failed, or the request has genuinely changed.

- **Tag your own replies.** When a request is a general, repeatable *kind* of task (not one-off, not personal small talk), end your final reply with a hidden `[[TASK: task_type]]` line, e.g. `[[TASK: play_song]]` or `[[TASK: search_wikipedia]]`. Use a short snake_case name for the *shape* of the request, not its specific words. Leave the tag off anything you can't picture doing the same way again. This is instructed to you automatically on every voice turn (`assistant/agents/rules.py`); it's stripped before anything is spoken or shown, so it costs a few tokens and nothing else.
- **Nova tracks the tag, not you.** `assistant/automation/promotion.py` counts how often each `task_type` goes through you instead of an existing automation (`data/task_frequency.json`, `[promotion]` in `config.toml`). What happens next depends on how often and how expensive it's been:
  - **Frequent and general** (`auto_after` repeats, default 3): Nova asks you directly, in a background turn, to save the steps as `automations/<task_type>.toml` -- see section 6 and `automations/README.md` for the format. Base the steps on how you actually did it earlier in the same conversation. Write the file yourself; don't just describe it.
  - **Expensive but not frequent yet** (a turn used several tool calls or real cost, below the auto threshold): Nova asks the user once by voice before saving anything. Only write the automation if a fresh request comes back asking you to.
  - **The user says "remember that" / "teach yourself that" / "save that as an automation"**: write it immediately, regardless of count.
- **When a saved automation fails** a step for some case it didn't expect, Nova runs the request through you instead so the user still gets an answer, then fires a second background turn telling you what failed, where, and the fact that you just did the same request successfully. Open the automation file and patch the one thing that broke (a selector, a control name, a phrase pattern); keep the change minimal and don't break its other phrases.
- **Never write an automation for a step you haven't actually verified once.** If you're not sure a selector or control name is right, do the request live first (Playwright tools / `main.py ui`), then write the file from what really worked -- a wrong automation is worse than none: it fails silently for the user until it's tried.
- This loop is how the assistant is meant to get cheaper and faster over its lifetime: the first time something is asked, it goes through you; by the fifth time, it shouldn't.

