# Recipes: what worked, so it doesn't have to be worked out twice

Nova has two tiers that cost very different amounts. Groq answers in about a second and
is free. Claude Code is slow and is the expensive half. The cascade in AGENTS.md exists
to keep work off the expensive tier, but until now it only got cheaper in one way: when
a task became rigid enough to write as an `automations/*.toml`. Most work never gets
that rigid, so it kept going to Claude forever.

A recipe is the softer half of that. When an agent turn succeeds, Nova records **how**:
which tools, in what order, how long, which brain, and what failed on the way. The next
time a similar request arrives, that account goes in front of the model.

The point is not replay. It is that the expensive tier solves something **once**, and
after that the cheap tier can read how it was done and do it itself. Work migrates down
the cascade instead of sitting at the top, which is the only way the running cost falls
over the life of the assistant rather than staying flat.

## Recipes versus automations

Both come from the same instinct and they are not competing.

| | `automations/*.toml` | a recipe |
|---|---|---|
| What it is | exact steps | an account of what worked |
| Who runs it | Nova, with no model | a model, which adapts it |
| Fits | deterministic tasks, same every time | fuzzy tasks phrased differently each time |
| Fails by | breaking loudly when a selector moves | being ignored, which is harmless |
| Written after | 3 repeats, or on request (AGENTS.md §12) | every successful agent turn |

A recipe is a lead. An automation is a script. A task that hardens over time can
graduate from one to the other.

## What is stored

One row per `task_type` — the same `[[TASK: type]]` tag the promotion loop already
uses, so nothing new had to be instrumented.

- **utterance** — how it was asked, which is what a new request is matched against.
- **tools** — the tool names in the order they were called, collected off the event bus.
- **backend, seconds** — who did it and how long it took.
- **skill** — the skill that applied, when one did.
- **pitfalls** — what failed, newest last, accumulated rather than overwritten.

Successes overwrite: a better route found later is the one worth keeping. Failures
accumulate: an old dead end is still a dead end.

### The pitfalls column is the valuable half

A successful transcript shows the path that worked. It cannot show the three that
didn't, and those are exactly what the next model will try. "The window is called
Spotify Premium, not Spotify" is one line that saves a model two wasted tool calls, and
no amount of cleverness infers it from a clean run.

## Feeding it back

`RecipeService.hint(text)` returns the block put in front of the agent, and returns
`""` far more often than not. That is deliberate:

> A hint about the wrong task is worse than no hint, because a model given a confident
> irrelevant instruction follows it anyway.

So matching is strict — two distinctive words shared with the stored request, common
words discarded — and the block is phrased as evidence rather than instruction. It ends
with "treat this as evidence, not a script: do what fits this request", because a model
told *do this* replays steps that may not fit, where a model shown *this worked* adapts.

A real one reads:

```
You have done this kind of thing before ("post an update about the release to linkedin").
What worked, in order: list_windows, click_control, type_in_app.
It took Claude Code about 52 seconds.
Known dead ends, don't repeat them: the LinkedIn compose box needs a click before typing
Treat this as evidence, not a script: do what fits this request.
```

## Where it lives

| Thing | Where |
|---|---|
| Rows, SQLite | `assistant/recipes/store.py` → `data/recipes.db` |
| Collecting and matching | `assistant/recipes/service.py` |
| Recorded after a turn | `assistant/controller.py`, in `on_agent_finished` |
| Fed back before a turn | `assistant/controller.py`, in `dispatch` |
| Settings | `[recipes]` in `config.toml` |

`hint_agents = false` keeps recording but stops feeding recipes back, which is the
switch to reach for if a hint ever drags an answer off course.

## Limits worth knowing

- A recipe needs a `task_type`, which comes from the agent tagging its own reply. An
  untagged turn is not recorded — by design, since an untagged turn is one the agent
  judged too specific to repeat.
- Matching is words, not meaning. "Summarise my repo changes" and "what's new in the
  codebase" share nothing distinctive and will not match each other.
- Nothing here is ever spoken. A recipe is context for a model, not a fact about the
  user, so it never reaches the journal or the profile.
