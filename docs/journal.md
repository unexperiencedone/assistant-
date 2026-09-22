# The journal: Nova's own record of its days

A persona in a system prompt gives the same *manner* every session and no *continuity*:
the character is re-read from scratch each time, so yesterday is gone. The journal is the
other half. One short entry per finished day, written from the work that actually happened,
put back in front of every agent — so a new session starts knowing what we were in the
middle of instead of meeting you for the first time.

Stored at `data/journal.db`. Local, like everything else here.

## What an entry is made of

`writer.facts_for(day)` reads the day out of the stores that already exist and turns it into
numbers and titles:

| From | What it takes |
|---|---|
| `history.db` (`docs/history.md`) | sessions in that day, minutes, requests, unfinished requests, project tags, the request titles, how many files were changed, cost |
| `awareness.db` (`assistant/awareness/`, when watching is on) | which apps were actually on screen, and for how long |

That part is arithmetic — it cannot be wrong. `writer.plain(facts)` then states those facts in
Nova's voice with no model involved at all:

> Saturday 19 September: 5 sessions with you, 3 h 02 m, 60 requests, 3 of which I did not
> finish, on AssisstantOS. You asked me for: pull off the news brief for today; … I changed
> 3 files.

A model is only ever asked to phrase **those same facts** more naturally (`narrate = true`),
and it is shown nothing else — no transcripts, no screenshots, no guessing. So the worst a
narrated entry can be is clumsy; it cannot be a day that never happened. If the model errors,
returns nothing, or rambles past `MAX_NARRATED` characters, the plain entry stands and the
entry is marked `source = "plain"`.

## When it writes

- Only about a day **that is over**. Today is never written; it isn't finished being lived.
- A day is written **once**. The date is the primary key, so a catch-up can run as often as
  it likes and the record cannot grow a second version of the same day.
- A day with **nothing in it gets no entry**. Silence is more honest than a filler line.
- After the laptop has been off, `backfill_days` (default 3) is how far back it will still
  fill in. The writer thread wakes every `check_minutes` and catches up.

## Where the entries go

`JournalService.context()` renders the last `context_days` entries into a short block, which
`AgentRegistry.apply_profile` appends to the standing profile context every agent already
gets. Both are standing context: true at process start, rarely changing, needed before the
first word of a turn. The block ends by saying what it is for — continuity, not a script to
recite — because otherwise a model will read its own diary at you unprompted.

A new entry publishes the `journal` bus topic, and `app.py` re-applies the instructions then.
That happens about once a day, so it is free.

## Settings (`[journal]` in config.toml)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | write entries at all |
| `db_path` | `data/journal.db` | where they live |
| `check_minutes` | `30` | how often the writer looks for a finished day with no entry |
| `backfill_days` | `3` | how far back it will still write |
| `context_days` | `3` | how many entries the agents are shown |
| `keep_days` | `730` | entries older than this are pruned at startup |
| `narrate` | `true` | let a free chat model phrase the entry; the facts still come from the rows |

`JournalService.forget()` deletes every entry, the same way the Activity screen's
**Forget everything** does.

## Files

| File | Does |
|---|---|
| `assistant/journal/store.py` | SQLite: one row per day, the text and the facts it came from |
| `assistant/journal/writer.py` | a day → facts → the plain entry, and the prompt a narrator sees |
| `assistant/journal/service.py` | the writer thread, catch-up, `context()` for the agents |
| `tests/test_journal.py` | what it writes, and what it refuses to write |
