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
- Always cap output (`-First 20`, `| head -20`, `--limit`). Never recurse from `C:\` or `C:\Users\kumar`. Skip `AppData`, `node_modules`, `.git`.
- Never delete, move or overwrite user files without explicit confirmation. Use the Recycle Bin, not permanent delete.
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

# Recently changed files
Get-ChildItem "C:\Users\kumar\Downloads" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name, LastWriteTime

# Largest files in a folder
Get-ChildItem "C:\Users\kumar\Downloads" -Recurse -File -ErrorAction SilentlyContinue |
  Sort-Object Length -Descending | Select-Object -First 10 @{n='MB';e={[math]::Round($_.Length/1MB,1)}}, FullName
```

Git Bash equivalent: `find /c/Users/kumar/Downloads -iname '*invoice*' -not -path '*/node_modules/*' 2>/dev/null | head -20`

Searching inside files:
- Claude Code: use the built-in Grep and Glob tools, not shell grep.
- PowerShell: `Select-String -Path "C:\proj\src\*.py" -Pattern 'def main' -List | Select-Object -First 20 Path, LineNumber`
- Git repos: `git grep -n "pattern" -- '*.py' | head -20` is fast and respects .gitignore.

## 4. Duplicate files

1. `python C:\Assisstant\sysindex.py dupes "C:\Users\kumar\Downloads" --min-size 100KB`. It groups by size, then compares the first and last 64 KB, then does a full BLAKE2b hash, with hashes cached in SQLite, so repeat runs are nearly instant.
2. Fallback in pure PowerShell (hashes only files that share a size):
```powershell
Get-ChildItem -Path "<folder>" -Recurse -File -ErrorAction SilentlyContinue | Where-Object Length -gt 0 |
  Group-Object Length | Where-Object Count -gt 1 | ForEach-Object { $_.Group } |
  Get-FileHash -Algorithm SHA256 | Group-Object Hash | Where-Object Count -gt 1 |
  ForEach-Object { $_.Group.Path -join ' | ' } | Select-Object -First 30
```
3. Report first. Delete only the copies the user confirms, and send them to the Recycle Bin:
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
4. **Repeated task:** save it as `automations/<name>.toml` so it runs next time without a model.
5. Ask before anything that buys, sends messages or submits forms for the user.

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
| Event bus, UI state | `events.py`, `state.py` |
| Mic, speech-to-text, TTS | `audio/` |
| Instant commands (regex) | `intents.py`, handled in `controller.py` (`_intent_<name>`) |
| Agent sessions (live `claude -p`, `agy -p`, voice rules) | `agents/` |
| Turn runner, plan DAG, live graph | `agent_runner.py`, `planning/plan.py`, `planning/graph.py` |
| Local index (SQLite, frecency, dupes) | `system/` |
| UI automation + TOML automations | `automation/` (`desktop.py`, `browser.py`, `macros.py`); files in `automations/` |
| Canvas server / frontend | `ui/server.py`; React source in `canvas/` (`npm run build`) |
| Window, tray, hotkey | `ui/shell.py`, `ui/win32.py` |
| Paths, packaging, autostart | `paths.py`, `autostart.py`, `packaging/` |

Conventions: small modules with one responsibility each; modules talk through `EventBus` topics, never through each other's UI; type hints; no new dependencies without a reason.
Never add Anthropic API calls: the user has no API billing. All model work goes through the `claude` CLI session.
Adding a voice command: add a rule to `_RULES` in `intents.py` (specific patterns before general ones), then add `_intent_<name>` to `controller.py`, or add it to `LOCAL_INTENTS` with a handler in `system/voice.py`.
Canvas changes: edit `canvas/src/`, then `npm run build` in `canvas/` (use PowerShell: Git Bash resolves a broken `node` shim here).
Checks: `python -m compileall -q assistant` and `python main.py --text --no-browser` for a manual run.
