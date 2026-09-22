# Nova as a standing digital-work agent

The goal behind this track: an assistant with a continuous identity that does the digital
chores end to end — capture, edit, publish, and keep the public profile current — rather than
answering one request at a time and forgetting.

Two honest framings, because they decide what gets built:

- **"Human-like" here means continuity, not consciousness.** What makes an assistant feel
  like someone rather than something is a consistent voice, a memory of what it did, and its
  own standing intentions. All three are engineering. None of them require pretending to
  feelings, and Nova doesn't (`assistant/persona/character.md`).
- **The brain is not the bottleneck; the platforms are.** Posting is gated by account types,
  OAuth scopes and rate limits, and the shortcut — scripting the websites in a browser — is
  how accounts get restricted. Everything here goes through official APIs.

## Where it stands

| # | Step | Status |
|---|---|---|
| 1 | **Autobiographical memory** — a daily journal Nova writes itself, fed into every agent's instructions | **Done.** `assistant/journal/`, `docs/journal.md` |
| 2 | **Standing goals** — a queue of Nova's own intentions and a loop that acts on them unprompted | **Done.** `assistant/goals/`, below |
| 3 | **The GitHub arm** — commits, releases, profile README through the `gh` CLI | **Written, blocked.** `assistant/publish/github.py`; `gh` is not installed on this machine |
| 4 | **Capture** — screen recording here, camera on the phone, both landing in an inbox | **Screen done and verified.** Phone camera written, needs the bridge redeployed |
| 5 | **Edit** — local ffmpeg: cut, caption, reframe, thumbnail | **Done and verified** on real footage. `assistant/capture/edit.py` |
| 6 | **Publish** — LinkedIn and Instagram through their official APIs | **Written, unverified.** No credentials on this machine yet |
| 7 | **One approval gate** in front of everything outward-facing | **Done.** `assistant/publish/gate.py` |
| 8 | **Drift check** — is this still the same voice? | **Done and run.** `assistant/persona/drift.py`, scored 5 of 5 |

356 tests pass (`python -m unittest discover -s tests`). Five new test files cover the
journal, goals, capture, publishing and drift.

## What each step actually is

### 1. The journal (`assistant/journal/`)

One short entry per finished day, built from the work history and the activity record,
appended to every agent's standing instructions. Full description in `docs/journal.md`.
The rule it establishes, which every later step inherits: **the facts come from the stores,
a model only ever phrases them.**

### 2. Standing goals (`assistant/goals/`)

The one place Nova starts something itself. A goal is a *request in Nova's own words*,
submitted through `app.submit` exactly as if you had said it — so every confirmation that
guards a text, a call or a post still guards a goal. Three restraints:

- **It waits its turn.** Nothing fires while you are mid-conversation or an agent task is
  running. Interrupting you is worse than being an hour late.
- **It is quiet at night.** `quiet_from` / `quiet_to` in `[goals]`.
- **A missed week fires once, not seven times.** The next due time is measured from the run,
  not from the schedule.

By voice: *"every morning, tell me what changed in my repos"* sets one; *"what are your
standing goals"* reads them back. "Every morning" first fires tomorrow at 8, not instantly.

### 3. The GitHub arm (`assistant/publish/github.py`)

Written against the `gh` CLI so there is no token of ours to store or refresh — `gh` carries
its own login. **Blocked on this machine**: `gh` is not installed, and installing software is
your call, not mine. One command unblocks it:

```powershell
winget install --id GitHub.cli -e
gh auth login
```

Until then every entry point returns that sentence rather than failing obscurely. Reads
(`repos`, `recent_activity`) are free; writes (`release`, `commit_and_push`,
`update_profile_readme`) go through the gate.

### 4. Capture (`assistant/capture/`)

- **Screen** (`screen.py`) — ffmpeg's `gdigrab`, already installed. Verified: it records,
  stops cleanly and produces a playable file. `monitor = 1` records just the laptop screen;
  `0` records the whole desktop, which on this two-screen setup is 4800×1800 and unusable
  for anything social. Stopping sends `q` on stdin — ffmpeg's own clean finish — because
  killing the process leaves a file that looks fine in the folder and won't play.
- **Phone camera** (`phone.py`) — a new `camera_photo` action in `phone/nova_bridge.py`.
  The photo comes back inside the JSON reply as base64 (there is no file server on the
  phone) and is written into the inbox. **It is a sensor**, so it is gated by the Phone
  section of the profile and off by default, and Nova says it took one every time.
  **Not yet run against the phone** — the listener has to be copied over and restarted first.
- **Inbox** (`inbox.py`) — one flat folder, timestamped names, nothing deleted automatically.
  Footage is the one thing that cannot be re-shot.

Nova can start and stop the phone's camera. It cannot hold one, frame a shot or judge a take.

### 5. Edit (`assistant/capture/edit.py`)

`trim`, `caption`, `reframe`, `thumbnail`, `probe` — all verified against real recordings.
No GPU here, so everything encodes on CPU with libx264. Two consequences baked into the design:
a cut that does not need re-encoding uses a stream copy (`precise=False`), and a long render
is minutes, not seconds. `reframe` fits the picture into 1080×1920 / 1080×1080 / 1920×1080 and
fills the gap with a blurred copy of the frame rather than cropping the middle out of a screen
recording — which is where the text people need to read usually is.

One thing worth knowing, because it cost a render to find: inside an ffmpeg filter graph the
colon in `C:/Windows/Fonts/...` has to be escaped or ffmpeg just says *Invalid argument*.
That is `_filter_path`, and there is a test on it.

### 6. Publish (`assistant/publish/linkedin.py`, `instagram.py`)

Written to the current APIs, **not yet run against a live account** — there are no credentials
on this machine. See `docs/publishing.md` for exactly what each platform needs.

### 7. The gate (`assistant/publish/gate.py`)

Everything outward-facing is staged as a draft, described back in one judgeable line, and
sent only when you approve **that draft**. Drafts are SQLite rows, so they survive a restart.
There is no "approve everything" mode, deliberately. A platform refusal is recorded as a
failure with what it said — a draft is never reported as sent because the request was made.

By voice: *"what's waiting to go out"*, *"post it"*, *"drop it"*.

### 8. The drift check (`assistant/persona/drift.py`)

`python main.py voice-check`. Five fixed probes go to the current backend; the replies are
scored against what the character actually promises — first person, short, no flattery, no
naming the plumbing, no borrowed identity, and crucially **no denying what it runs on**, since
that is the one failure that turns branding into lying. Scores land in `data/drift.json` with
a date, so "the voice has slipped" becomes a number rather than a feeling. Scoring is regex,
not a model: a judge is one more thing that can drift.

Last run: 5 of 5 clean.

## What is left

1. Install `gh` and authenticate (step 3) — one command, yours to run.
2. Copy the updated `phone/nova_bridge.py` to the phone and restart it, then take one photo
   to prove the path (step 4).
3. LinkedIn: create the app, add **Share on LinkedIn**, put the token in `.env` (step 6).
4. Instagram: switch the account to professional, add it as an Instagram Tester, put the
   token and user id in `.env` — and decide where media gets hosted, because Instagram
   fetches media by URL and will not take a local file (step 6).
5. The canvas has no screen for any of this yet: goals, drafts and the inbox are reachable
   by voice and by API, not by eye.
