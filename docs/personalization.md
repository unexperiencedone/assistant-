# Personalization: the profile

Nova keeps a profile of you (who you are, your goals, skills, machines, projects, people,
past work and how you like to work) and uses it to tailor every agent, speech recognition and
local commands. Edit it in the canvas: **Profile** button, top right (or open `/#profile`).

It is stored in `data/profile.json`. `data/` is git-ignored, so the profile never reaches a
commit.

## Files

| File | What it does |
|---|---|
| `assistant/profile/schema.py` | The profile's shape, declared once. Validates saves and drives the canvas form. |
| `assistant/profile/render.py` | Turns the profile into prompt text (standing and relevant context) and speech hints. |
| `assistant/profile/service.py` | Loads and saves `data/profile.json`, publishes the `profile` bus event, notifies consumers. |
| `canvas/src/Profile.jsx` | The editor. Draws its form from the schema, previews what each agent sees. |
| `tests/test_profile.py` | Schema coercion, visibility, context matching, agent wiring. |

## Sections and schema

| Section | Default visibility | Fields |
|---|---|---|
| About you (`identity`) | Claude & Antigravity | name, preferred name, what you do, education, roles and organizations, location, time zone, languages |
| Goals | All agents | career goals, research interests, currently learning, looking for |
| Skills | All agents | skills (name, level: learning / rusty / comfortable / strong / advanced, notes), topics to steer away from |
| How to work with you (`work_style`) | All agents | code delivery, documentation, spoken reply length (brief / normal / detailed), other rules |
| Computers (`devices`) | All agents | machines (name, specs, GPU, yours or not, notes), compute constraints |
| Projects | Claude & Antigravity | name, aliases, status (active / paused / done / idea), event or client, your role, team, summary, stack, folder, links, deadline |
| People | Claude & Antigravity | name, aliases, relation, projects together, notes |
| Past work (`portfolio`) | Claude & Antigravity | name, aliases, summary, stack |
| Speech words (`vocabulary`) | This PC only | words |
| Privacy | (fixed) | personalize agents at all, shared Claude account |

Field types are `text`, `textarea`, `select`, `tags`, `bool` and `list`. Saving runs
`schema.normalize()`, which drops unknown keys, replaces invalid values with defaults, caps
lengths, and discards list items without a name.

### Visibility

Each section chooses who may read it in a prompt:

- **All agents**: Claude Code, Antigravity and the free cloud models (Groq, OpenRouter).
- **Claude & Antigravity**: only the CLI agents running under your own accounts.
- **This PC only**: never put in any prompt. Used only for speech hints and opening folders.

`profile.audience_for(backend)` maps `claude` and `antigravity` to "trusted", and everything
else to "all".

## Where the profile is used

1. **Standing context.** A compact summary (at most 2400 characters) is added to every
   session's instructions, filtered by visibility:
   - Claude Code: `--append-system-prompt`. A change restarts the session before the next turn
     (never mid-turn), and `--resume` keeps the conversation.
   - Antigravity: sent with the first message of a conversation. A change is sent once, with
     the next message.
   - Groq and OpenRouter: part of the system prompt, which is rebuilt every turn.
2. **Relevant context.** When a request names a project, person or past-work entry (its name
   or an alias, as a whole word), that entry's full details are put in front of that one
   prompt (`ProfileService.with_context`, called in `Controller.dispatch`).
3. **Speech hints.** The wake word plus vocabulary, project, people and past-work names (up to
   three words each) are passed to faster-whisper as `hotwords`, and updated live on save.
4. **Opening projects.** Set a project's folder, then say "open drishtikon" (or an alias, or
   "... project" / "... folder"). The folder opens and counts toward frecency.
5. **Greeting and reply length.** Nova greets you by your preferred name. The spoken reply
   length setting caps how many sentences are read aloud (brief 2, normal 3, detailed 5).

## Adding a field

Add it to `SECTIONS` in `schema.py`. The form picks it up automatically. If agents should
see it, render it in `render.standing_context` or in the matching `_*_detail` function. Then
add a case to `tests/test_profile.py`.

## API

- `GET /api/profile`: `{profile, schema, revision, preview: {trusted, all, speech}}`
- `PUT /api/profile`: body `{profile}`. Validates, saves and applies. Returns the same shape.
- `POST /api/profile/preview`: body `{profile}`. What agents would see for unsaved changes.
- The `profile` slice of the WebSocket state is `{revision, name}`, so open editors notice
  saves made elsewhere.
