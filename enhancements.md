# Nova — codebase review, upgrades and fixes

> **Status, 2026-09-16.** Every item in this document is implemented and tested
> (`python -m unittest discover -s tests`: 26 tests, plus live runs against Claude Code,
> OpenRouter and automations).
>
> The three items that were outstanding are now done:
>
> - **Multi-task agents.** `AgentRunner` is a pool (`[agents] max_parallel`, default 2). Each
>   task has its own id, its own agent session via `AgentBackend.spawn()` (a separate
>   `claude -p --resume` process / conversation id / OpenRouter history), its own graph lane
>   and canvas tab. Only the focused task narrates; others announce themselves when they
>   finish. "Cancel the task" stops the focused one, "cancel all tasks" stops everything.
>   Verified live: a quick question answered at +6.0 s while a long one was still running and
>   finished at +12.9 s, which is impossible when serialized.
>   Parallelism is not unconditional: `triage.py` sorts each request into an errand
>   (open, play, search, find, send, a short question) or a big job (write, tidy,
>   refactor, research, install, or running a plan). Errands start alongside anything;
>   a second big job is queued with "that's a big one, so I'll start it once X is done",
>   because two big jobs fight over the same windows, files and attention.
> - **`task_id` on bus events.** `task=<id>` now rides on route/agent events and keys the
>   graph lanes.
> - **Drag-to-reorder plan steps.** Drag a step on the canvas, use Move up/down in the
>   inspector, or say "move step 2 down" / "move step 1 to 3". `Plan.move_step()` remaps
>   dependencies and drops any that would point forwards.
>
> Details of each fix are in the sections below, marked ✅.

Reviewed: every module under `assistant/` (app, controller, agent_runner, agents/*, planning/*, ui/*, system/*, audio/*, automation/*), `canvas/src/*`, `main.py`, `sysindex.py`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `config.toml`. Updated 2026-09-16.

## Why the graph hides detail (and what to do about it)

This is worth calling out on its own because it isn't just a rendering choice — real data is being discarded, not just summarized:

- `assistant/agents/claude_cli.py`, `_describe_tool()`: every tool argument (a bash command, a file path, a grep pattern, a URL) is cut to 80 characters with `short(value, 80)` **before** it becomes an `AgentEvent`. The full value is never captured anywhere.
- `assistant/planning/graph.py`, `_add_action()`: each action's text is capped at 120 characters, a node's `detail` at 140, a result at 300.
- Worse: `del anchor.actions[:-MAX_ACTIONS_SHOWN]` in the same function **permanently deletes** every action but the most recent 4 per step from the app's own in-memory state — not just from the screen. The `action_count` / "+N earlier" chip on `StepNode.jsx` counts how many were lost; it can't show them, because nothing kept them.
- There is no raw log of the `claude -p` stream-json session written to disk anywhere (`claude_cli.py`'s reader thread only ever queues lines in memory for the current turn). Once a turn ends, the only surviving record is the already-truncated graph node.

**Fix, in order of value:** ✅ all five done.
1. ✅ `_describe_tool` / `_describe` now keep the whole command, path or pattern; `agent_runner`
   publishes full text on the bus; the graph keeps up to 8000 chars per action and 300 actions
   per node, and only `to_dict()` shortens (to 300 chars) for the wire.
2. ✅ `assistant/sessions.py` writes every turn's raw stream to
   `data/sessions/<date>/<backend>/turn-NN.jsonl` (verified: 5719-char lines preserved).
3. ✅ Inspector panel: click any node for the full text, per-action, with copy buttons,
   served untruncated from `GET /api/run[/<n>]`.
4. ✅ `MAX_ACTIONS_SHOWN` is display-only now; `MAX_ACTIONS_KEPT` (300) is the storage cap.
5. ✅ Activity feed has a text filter and a short/full toggle.

Original notes follow.
1. Stop truncating at the source. Keep the full text in the `AgentEvent`/bus payload; only truncate at the point something is actually rendered (and even then, keep the full string alongside the shortened one).
2. Persist every turn's raw stream-json to `data/sessions/<session_id>/<turn>.jsonl` (you already have the SQLite pattern in `system/db.py` for exactly this kind of "small structured log" need — a flat file is even simpler here). This alone gives you an after-the-fact "what actually happened" that survives restarts, independent of any UI.
3. Add a detail panel/inspector to the canvas: clicking a node or an action opens a side panel with the full untruncated text (full command, full file diff, full tool output) instead of only the 120/140/300-char summary. This is additive — the DAG stays the at-a-glance view, the panel is where you go for detail, same relationship a stack trace has to a one-line error banner.
4. Raise or remove `MAX_ACTIONS_SHOWN` as a *storage* limit — it's fine as a *display* limit (show the last 4 inline, "N earlier" expandable to the rest), but right now it's actually deleting data, which it shouldn't.
5. Give the Sidebar's activity feed a raw/full-text mode and a text filter — once a task produces 40+ rows, "when did it touch config.py" currently means scanning by eye through already-shortened lines.

## Bugs and correctness fixes

✅ All fixed: OpenRouter cancels mid-request in ~1 s (request runs on a worker thread);
`desktop.py`/`browser.py` log through `nova.*` loggers into the activity feed instead of
`except: pass`; the window cache is an LRU (32) and the type-name map is built once;
`MAX_WALK` truncation is reported in the "no control" message; the Playwright connection is
reused across automation runs (8.5 s → 2.6 s); agent failures now say *why* (denied tool,
stderr tail, exit code); `GraphTracker` keeps the last 20 runs; `Plan.infer_from_action` has
tests, including the overlapping-filename case. Also found and fixed while testing: a
fall-through local command created a duplicate, empty history entry.

- **`OpenRouterAgent._chat()` (now `agents/chat_api.py`) can't actually cancel mid-request.** `cancel.is_set()` is only checked between tool-call rounds and between individual tool calls — the blocking `urllib.request.urlopen(..., timeout=self.settings.timeout)` (up to 120s by default) has no cancellation path. "Cancel the task" while OpenRouter is mid-HTTP-call will silently wait out the request. Compare to `ClaudeCliAgent.close()`, which kills the process tree immediately — this asymmetry should be fixed (e.g. run the request in a thread and only wait on `min(timeout, poll interval)` chunks, checking `cancel` between them, or pass a socket-level timeout and race it against the cancel event).
- **Bare `except Exception: pass` in `automation/desktop.py` (`focus()`, several call sites) and `automation/browser.py` swallow real failures with zero logging.** A UI-Automation click or focus failure disappears silently instead of reaching `bus.log`, which makes "why didn't that work" hard to diagnose days later from `nova.log`. At minimum, log at `debug`/`warn` level instead of bare `pass`.
- **`desktop.py`'s `_window_cache` and `_TYPE_NAMES` are unbounded global dicts** with no eviction. Nova is designed to run for days via the tray/autostart; a window-title cache that only grows is a slow leak. Add an LRU cap or clear entries for windows that no longer exist (you already have `is_visible()` checks that could double as eviction).
- **`desktop._snapshot()` caps a UI walk at `MAX_WALK = 4000` elements with no signal back to the caller.** If a wanted control lives past element #4000 in a huge app (a big Electron app, a heavy web page hosted in an app shell), `find_control` reports "no control" as if it simply doesn't exist. Surface "search may be incomplete (truncated at 4000 elements)" in the error message so it doesn't read as a false negative.
- **`browser.py` tears down and restarts the whole Playwright connection (`sync_playwright().start()`) on every single macro run** (`MacroRunner.run()`'s `finally: browser.detach()`), even though the underlying Edge process and profile stay open. Since `sync_playwright().start()` spawns a driver process, this adds avoidable latency to every web automation call. Pool/reuse a `Browser` instance across runs instead of creating and killing one per macro invocation.
- **`Controller.on_agent_finished()`'s fallback message ("ran into a problem, check the canvas") is much vaguer than `MacroRunner`'s ("stopped at step N: <reason>").** `AgentResult`/`claude_cli.py`'s `parse()` usually does carry more (stderr tail, exit code, the specific denied tool's name from `permission_denials`) — route more of that into the spoken/topbar message so a voice-only user (no screen in front of them) gets a specific reason, not just "check the canvas."
- **`GraphTracker._reset()` wipes the entire previous graph on every new `route` event.** Even without building full parallelism, this means there is zero way to look back at what the *previous* request's graph looked like once you say anything new — worth fixing on its own even before tackling concurrency.
- **`Plan.infer_from_action()`'s heuristic can mis-attribute actions** when two steps mention overlapping filenames (e.g. "Write tests for login.py" and "Fix login.py") — the scoring gives a slight edge to tokens near the start of the step text, but this is a heuristic with real failure modes worth a unit test or two given how central it is to making the live graph feel accurate.

## Parallel use for two tasks at once

Currently strictly serial: `Controller.dispatch()` queues a second request into `self.waiting` while `AgentRunner.running` is true; `AgentRunner` holds exactly one thread and one `Plan`; `ClaudeCliAgent` holds one long-lived `claude -p --resume` process; `GraphTracker` resets to a single graph per request. "Type ahead while it's working" exists; "watch two independent tasks run side by side" does not.

- [ ] **Quick win, low risk:** let TOML automations (`MacroRunner`) and the agent (`AgentRunner`) run concurrently — they're already separate threads with separate busy-flags (`MacroRunner._busy` vs `AgentRunner._thread`); `Controller` just needs to stop treating "an automation is running" and "the agent is running" as one shared kind of busy.
- [ ] **Medium:** turn `Plan`/`GraphTracker` from app-wide singletons into per-task objects keyed by a `task_id`, carried on the `route`/`agent`/`plan` bus events. This alone gets you request history (see below) and is most of the plumbing multi-task support needs.
- [ ] **Large:** turn `AgentRunner` into a small pool keyed by `task_id` instead of one `self._thread`/`self.backend`.
- [ ] **Large, Claude-Code-specific:** support more than one live `claude -p --resume` process at once (one per concurrent task), each with its own `session_id`; `agents.continue_session` needs to become "continue *this named* session" rather than "continue the one session."
- [ ] Gate spoken narration to one "focused" task at a time (voice/TTS can't be parallel) — background tasks post to the canvas silently and get a spoken ping only on completion; `Speaker.is_busy` already exists and can gate this.
- [ ] Canvas becomes tabbed (one tab per active task) instead of a single graph that gets replaced.
- [ ] Tray menu's "Cancel task" (currently `enabled=lambda item: app.runner.running`, `ui/shell.py`) needs to become "cancel *which* task" once more than one can run.

## UI/UX and widgets

✅ Done: inspector panel, request history rail, minimap (appears past 6 nodes), cost/session
meter, live mic level (via `recognizer.listen(stream=True)`), permission-denial banner,
frecency-ranked quick action chips (`commands` table in the index db), activity feed
filter + full-text mode, click-a-step editing (remove / run / read back), theme toggle
(system/light/dark), truncation-aware messages for UI walks and duplicate scans.
Not done: drag-to-reorder steps, tabbed canvas for multiple *agent* tasks.

- [ ] **Detail/inspector panel** for nodes and actions (see graph section above) — the single highest-value addition.
- [ ] **Request/session history** — a rail of past requests (even just the last 10-20, each collapsed to one line, clickable to restore its final graph) since `GraphTracker` currently discards the previous graph outright.
- [ ] **Minimap** on the canvas (`<MiniMap />` ships with React Flow for free) once a plan exceeds ~6-8 steps and stops fitting one screen.
- [ ] **Cost/session meter.** `claude_cli.py`'s `parse()` already receives `total_cost_usd` from the CLI's `result` message and currently discards everything but `is_error` — surface cumulative cost as a small topbar widget; the data is already flowing through.
- [ ] **Live mic level/waveform** during "listening" status — `MicrophoneListener` already has the raw audio; right now the only feedback is a text label, which leaves "is it actually hearing me" as a real, common source of voice-UI anxiety.
- [ ] **Distinct visual callout for permission denials**, not just another gray activity row. `claude_cli.py`'s `parse()` already yields `tool="denied"` events for `permission_denials`; since `CLAUDE.md` states "nobody can approve permission prompts mid-turn," a missed denial silently stalls the whole task until the user happens to notice.
- [ ] **Dynamic quick-action chips** in `Sidebar.jsx` instead of the hardcoded 6-item `QUICK` list — surface the user's loaded automations or most-used spoken commands (frecency scoring already exists in `system/ranking.py` for files/apps; extending it to spoken intents would let the sidebar adapt to how someone actually uses it).
- [ ] **Search/filter over the activity feed** — needed regardless of the detail-panel fix, for "find the row about config.py" in a long feed.
- [ ] **Click-to-edit / drag-to-reorder plan steps** in the canvas — today, editing a plan is voice/text-only ("remove step 2") even though `FlowCanvas.jsx` explicitly disables `nodesConnectable`/`elementsSelectable`. A natural v2 given the graph is already visually plan-shaped.
- [ ] **Live theme switching.** `system_prefers_dark()` (`ui/win32.py`) is read once at window creation; no in-app light/dark toggle and no reaction to the user changing Windows theme mid-session.
- [ ] **Truncation-aware error messages** in `desktop.py`/duplicate scans, so a "not found" doesn't look identical to "found nothing" (see MAX_WALK note above, and duplicate scans that silently skip unreadable files via bare `except OSError: continue` in `duplicates.py`'s `_files()`).

## Performance and reliability

✅ Done: conversation + plan persist across restarts (`store.py`, `data/state.json`, atomic
writes, verified by restarting); Playwright/Edge connection reused; desktop caches capped;
OpenRouter's `max_tool_calls` exhaustion now returns a clear message naming the limit;
the indexer walks breadth-first so top-level results appear first during a cold index.

- [ ] Persist plans/transcripts/activity across restarts — everything in `AppState` is a bounded in-memory `deque` (`maxlen=40`/`60`) and `Plan` is cleared with the process; for a tool meant to run for days via tray/autostart, "what did I ask it yesterday" currently has no answer at all. A lightweight SQLite table (same pattern as `system/db.py`) would fix this cheaply.
- [ ] Reuse the Playwright/Edge connection across automation runs instead of reconnecting every time (see bug above) — real latency win for any web automation.
- [ ] Cap or evict `desktop.py`'s global window/type caches (see leak above).
- [ ] Double-check `OpenRouterAgent`'s behavior when a legitimate task needs more than `max_tool_calls` (6 by default) — confirm it degrades with the existing "I tried several steps but didn't finish" message rather than silently truncating output mid-tool-call, and consider making the cap configurable per-task rather than fixed at startup.
- [ ] `Indexer.index_files()`'s directory walk uses a plain `list.pop()` stack (LIFO/DFS) with no depth-based priority — fine functionally, but on very large trees a breadth-first pass would surface top-level results to `sysindex find` sooner during the first cold index.

## Security and safety

✅ Done: every model-initiated tool call is logged, and the ones that change something
(`open_item`, `click_control`, `type_in_app`, `press_keys`, `run_automation`, `write_word`)
are logged at warning level so they appear in the activity feed and `nova.log`, distinct from
a user's own voice command; `browser.py` carries a trusted-user-only note; `build.ps1` fails
the build if a `.env` ever reaches `dist\Nova`.

- [ ] `_read_control`/`click_control`/`type_in_app` etc. in `agents/tools.py` give a chat model (OpenRouter, potentially a much weaker free model per `README.md`) direct UI-automation control over arbitrary windows with no allowlist — worth at least logging every automation action a *model* (as opposed to a human via voice) initiates distinctly from user-issued ones, since `AGENTS.md` already asks agents to "ask before anything that buys, sends messages or submits forms," but that's a convention in a prompt, not an enforced boundary.
- [ ] `automation/browser.py` launches Edge with a dedicated profile and no sandboxing beyond that — reasonable for a personal tool, but worth a comment noting it's trusted-user-only, not something to expose to any multi-user or remote-access scenario.
- [ ] `.env`/API keys: `config.load_env_file()` correctly keeps secrets out of `config.toml`, and `.gitignore` presumably covers `.env` — worth double-checking the packaged `dist\Nova\` build doesn't accidentally bundle a developer's `.env` if `packaging/build.ps1` ever does a blanket folder copy.

## Smaller housekeeping

- [ ] `Plan.infer_from_action`/`GraphTracker` combo would benefit from a small test suite given how much live-UI correctness rides on the heuristics (step inference, plan diffing) rather than on deterministic markers.
- [ ] `AGENTS.md`/`CLAUDE.md` are excellent — keep them in sync as the multi-task and detail-panel work lands, since they're what keeps a coding agent from re-discovering the same constraints every session.

## Suggested priority order

1. **Detail-loss fixes** (stop truncating/deleting at the source, add the inspector panel, persist raw session logs) — directly answers "graphs hide details," cheap relative to its value, no architecture change required.
2. **Bug fixes**: OpenRouter mid-call cancellation, silent exception swallowing in `desktop.py`/`browser.py`, vague agent-failure messages, unbounded caches.
3. **Cheap high-value widgets**: cost meter, permission-denial callout, mic level indicator, minimap, dynamic quick actions.
4. **Concurrency, cheap end**: automations running alongside the agent (separate busy-gates).
5. **Concurrency, larger end**: per-task `Plan`/`GraphTracker` (also buys you request history), then a tabbed canvas, then true multi-session Claude Code.
