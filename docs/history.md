# Work history: timed sessions

Everything you say and everything the agents do is recorded into **sessions**. Open them from
**History** in the canvas (top right, or `/#history`). A green dot on the button means a
session is in progress.

## What a session is

Projects can't be detected reliably, so sessions are split by time instead:

- A session **opens** with your first request: something you say or type, or an agent or
  automation starting.
- Everything that happens while it is open **extends** it.
- It **closes** after `idle_minutes` (default 30) with no activity, but **never while a task
  is still running**. A long, quiet agent turn is still work. The session ends at its last
  activity, not at the moment the idle check noticed.
- If Nova **restarts** within that idle window, the same session continues. After a crash,
  sessions left open are closed at their last activity.
- If the PC sleeps past the idle time, the next request starts a new session even though the
  idle check never ran.

Projects from your profile (`docs/personalization.md`) that you mention become **tags** on
the session, so you can still see which work a session was about.

## What is recorded

| Kind | From | Stored |
|---|---|---|
| `you` | what you said or typed | full text; counts as one request |
| `nova` | Nova's spoken replies | full text |
| `request` | the route a request took | utterance, route, task id, agent |
| `start`, `tool` | the agent starting, and each action it takes | tool name and full argument (command, path, URL) |
| `result`, `error` | the end of a turn | reply, cost, and data such as denied tools |
| `plan` | each new plan draft (not step status changes) | title and steps |
| `log` | warnings and errors | text |
| runs | each finished request's graph (`run_finished` bus event) | the full graph, replayable in the canvas |

Per session, the totals are kept up to date: requests, results, failures, actions, cost, the
agents used, and project tags. The files agents wrote or edited are derived from the tool
events.

Nothing is recorded before your first request, so the start-up greeting alone doesn't create
a session.

## Using it

- **Browse:** sessions are grouped by day. Each shows its time range, duration, request count,
  failures, cost and project tags.
- **Timeline:** your messages, then one card per request with its agent, duration, status,
  actions (expand to see every command and path) and result. Plan drafts are collapsible.
- **Graphs:** replay any finished request's graph. Click a node for the full detail in the
  inspector.
- **Search** (press `/`): matches anything said or done, including tool arguments like file
  paths, using a trigram full-text index.
- **Delete** a session from its header (it asks you to confirm first).
- **By voice:** "what did we do this session", "recap the last session", "what did we do
  today", "give me a recap of yesterday".

## Files

| File | What it does |
|---|---|
| `assistant/history/store.py` | SQLite schema (`sessions`, `events`, `runs`, `events_fts`), queries, files-changed detection |
| `assistant/history/recorder.py` | Bus events to sessions: open, extend, idle close, restart continuity. Writes happen on one background thread. |
| `assistant/history/recap.py` | The spoken recap, built from stored counts and titles (no model call) |
| `assistant/planning/graph.py` | Publishes `run_finished` with the full graph when a request ends |
| `canvas/src/History.jsx` | The History screen |
| `tests/test_history.py` | Session boundaries, counting, runs, plans, search, delete, recap |

Data is stored in `data/history.db` (git-ignored). This layer is separate from `data/sessions/`,
which holds each agent turn's raw stream for 14 days.

## Settings (`config.toml`)

```toml
[history]
enabled = true
db_path = "data/history.db"
idle_minutes = 30   # how long a pause ends a session
keep_days = 365     # older, finished sessions are deleted at start-up
```

## API

- `GET /api/history/sessions?q=&before=&limit=`: `{sessions, more, enabled}`, newest first.
  For paging, pass `before` = the last session's `started_at`.
- `GET /api/history/sessions/<id>`: `{session, events, truncated, runs, files}` (up to 5000
  events).
- `DELETE /api/history/sessions/<id>`
- `GET /api/history/runs/<id>`: a stored request's full graph, in the same shape as `/api/run`.
- The `history` slice of the WebSocket state is `{revision, current}`. `current` is the open
  session's summary, or null.
