# Usage: tokens spent and saved

Nova records what every brain spends and estimates what its cheaper routes save. You can
see it in two places: the **Usage** screen on the canvas, and in a terminal with
`python main.py usage [--days 7 | --all] [--json]`.

## Spent is measured

Every number on the spent side is the provider's own count. Nothing is estimated.

| Brain | What it reports | Money |
|---|---|---|
| Claude Code | fresh input, cache reads, cache writes, output, thinking, model | list-price cost of the turn |
| Antigravity | input, output, thinking, cache reads | none: it runs on its plan's quota |
| Groq / OpenRouter | prompt and completion tokens, summed over the turn's rounds | free |

The Claude dollar figure is the **list price** of the tokens. Your Claude plan is a flat subscription,
so this figure isn't a bill. It shows how much of the plan's limits a turn used, in the one unit that compares across turns.

Thinking tokens are part of output and are never added on top of it.

### A correction to earlier figures

In a long-lived Claude session, the `total_cost_usd` field is a **running total for the
process** (0.30, 3.12, 3.27, …), not the cost of the turn. Nova used to add these up as if each were per-turn,
so its cost counter, work-history totals and the promotion threshold were overstated by
roughly 4–5×. Over the history available on 2026-10-07, that was $47.78 claimed against $10.13 actually used. `claude_cli.py` now
reports each turn's own cost, the step up from the previous running total. Costs already stored in
`history.db` and `state.json` before this fix still carry the old, inflated figures.

## Saved is estimated, and the method is stated

Savings are valued from what turns actually cost on this machine, never from a price table:

- **A free-model or Antigravity turn** stood in for a Claude turn. It is valued at the median
  Claude turn of the same task type, or at the median of all Claude turns when the type is unknown.
  "At least" uses the 25th percentile instead.
- **A scripted answer** (an automation or an instant command) only counts as a Claude turn if
  Claude has actually handled that kind of request here. Otherwise it stood in for a free-model
  turn, because that's where an unscripted request goes first: $0, plus the median free turn's tokens.
- **Failed turns save nothing**, and neither do free-model turns that handed the job to Claude.
- **The browser extension** saving is in tokens only: the page's full text minus what the
  extension actually sent, divided by four.

If Claude turns get cheaper, the savings shrink with them. That's the honest direction for the estimate to move.

## What it records

Everything is stored in `data/usage.db` (git-ignored) and kept for good. The rows are small.

| Table | One row per | From |
|---|---|---|
| `turns` | agent turn | the runner's `agent` result events, plus `usage` events for delegated turns |
| `free` | request answered by no model | `route` events (intent, automation, loose, classifier, local) |
| `browser` | Chrome extension call | `extension.BRIDGE.on_measure` |

**Delegated turns used to be invisible.** When a free model called `delegate_to_claude` or
`delegate_to_agy`, or escalated a job, that turn never passed through the runner, so its cost appeared nowhere.
`app._delegate` now publishes a `usage` event for it. Escalating free-model turns also keep their own token counts now.

**Backfill.** On start, turns are imported from `data/sessions/` (kept 14 days) and the
weekly route counts are imported from `data/routes.json`. Each log is read once. Turns after
live recording began are left to the live rows, so nothing is counted twice. The old
weekly counts don't say *which* automation ran, so those scripted answers show $0. From now on each
run is named, and automations that replaced Claude work are valued accordingly.

## What to do with it

- **Where to save next** lists task types Claude handled at least twice that have no automation yet.
  Say "save that as an automation" after the next one (AGENTS.md section 12).
- **Spent on failed turns** is the clearest waste figure: a job costs the same whether it works or not.
- **Cache hit rate** shows how much of Claude's input came from its cache. A drop usually means the
  session restarted, or its instructions changed, and the context had to be rebuilt.

## Code

| Piece | Where |
|---|---|
| Every brain's own report, in one shape | `assistant/usage/normalize.py` |
| The ledger and its bus subscriptions | `assistant/usage/ledger.py` |
| Import from session logs and `routes.json` | `assistant/usage/backfill.py` |
| Spent, saved, method, where to save next | `assistant/usage/report.py` |
| `GET /api/usage?days=30` | `assistant/ui/server.py` |
| The screen | `canvas/src/Usage.jsx` |
| Tests | `tests/test_usage.py` |
