<div align="center">

# Nova

**A voice assistant that lives on your machine — and gets cheaper the longer you use it.**

Say what you want. Nova does it, shows its working on a live canvas, and quietly learns
the things you repeat so they stop costing anything at all.

<br>

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![Vite](https://img.shields.io/badge/Vite-646CFF?style=for-the-badge&logo=vite&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)

![Claude](https://img.shields.io/badge/Claude_Code-CLI-D97757?style=for-the-badge&logo=anthropic&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-F55036?style=for-the-badge&logo=groq&logoColor=white)
![Windows](https://img.shields.io/badge/Windows_11-0078D4?style=for-the-badge&logo=windows&logoColor=white)
![Tailscale](https://img.shields.io/badge/Tailscale-242424?style=for-the-badge&logo=tailscale&logoColor=white)
![Termux](https://img.shields.io/badge/Termux-000000?style=for-the-badge&logo=android&logoColor=3DDC84)

![Tests](https://img.shields.io/badge/tests-400_passing-3fb950?style=flat-square)
![Modules](https://img.shields.io/badge/python-110_modules-3776AB?style=flat-square)
![API billing](https://img.shields.io/badge/API_billing-none-8957e5?style=flat-square)
![Speech](https://img.shields.io/badge/speech-100%25_local-0969da?style=flat-square)

</div>

---

## The idea

Most assistants cost the same on day 300 as they did on day 1, because every request
goes to a model. **Nova treats reaching for a model as a failure to have learned
something.** A request falls through six tiers, cheapest first, and only the last one
costs anything.

```mermaid
flowchart LR
    A([You speak]) --> B{Instant<br/>intent?}
    B -->|match| Z([Done])
    B -->|miss| C{Saved<br/>automation?}
    C -->|match| Z
    C -->|miss| D{Same thing,<br/>said differently?}
    D -->|match| Z
    D -->|unsure| E{Free model:<br/>which script?}
    E -->|a script| Z
    E -->|none| G[Groq<br/>+ 23 tools, web, skills]
    G -->|finished it| Z
    G -->|"can't finish"| F[Claude]
    F --> Z
    F -.->|recipe| G
    F -.->|automation| C

    style B fill:#0d1117,stroke:#3fb950,color:#e6edf3
    style C fill:#0d1117,stroke:#3fb950,color:#e6edf3
    style D fill:#0d1117,stroke:#3fb950,color:#e6edf3
    style E fill:#0d1117,stroke:#d29922,color:#e6edf3
    style G fill:#0d1117,stroke:#d29922,color:#e6edf3
    style F fill:#0d1117,stroke:#f85149,color:#e6edf3
    style Z fill:#0d1117,stroke:#8957e5,color:#e6edf3
```

| Tier | Where | Speed | Cost |
|:--|:--|:--|:--|
| 🟢 **Instant intent** — a regex matched | `assistant/intents.py` | microseconds | free |
| 🟢 **Saved automation** — a TOML script matched | `automations/` | microseconds | free |
| 🟢 **Loose match** — filler, synonyms, word order | `assistant/matching.py` | microseconds | free |
| 🟡 **Free-model router** — which of these scripts? | `assistant/classify.py` | ~1 s | free |
| 🟡 **Groq** *(default)* — 23 tools, web search, skills | `assistant/agents/chat_api.py` | ~1 s | free |
| 🔴 **Claude** — what Groq couldn't finish | `assistant/agents/` | seconds | your subscription |

**The two dotted arrows are the whole point.** When Claude does solve something, it
doesn't just answer — it leaves the answer behind. A [recipe](docs/recipes.md) records
*how* the job went so Groq can do it next time; three repeats and it becomes a saved
[automation](automations/README.md) that needs no model at all. Work migrates **down**
the cascade over the assistant's life instead of sitting at the top forever.

> **Watch it work:** `python main.py stats --routes` prints the share of requests that
> never reached a model, one bar per week. That chart is this design's only real
> scoreboard.

---

## User flow

What actually happens between you speaking and Nova answering.

```mermaid
flowchart TD
    W([You say<br/>“Nova, …”]) --> WAKE{Wake word?}
    WAKE -->|no| DROP([Ignored])
    WAKE -->|yes| GATE[Noise gate<br/>drops garbage transcripts]
    GATE --> STT[Whisper, on this machine]
    STT --> CASCADE{Routing cascade}

    CASCADE -->|regex / automation / loose| LOCAL[Answered locally<br/>no model, no network]
    CASCADE -->|needs a brain| PRE[Nova adds context<br/>profile · journal · recipe · skill]

    PRE --> GROQ[Groq, ~1s<br/>23 tools]
    GROQ --> ASK{Finished?}
    ASK -->|yes| GUARD
    ASK -->|out of steps,<br/>tool failed,<br/>or too complex| CLA[Claude Code<br/>told what Groq already tried]
    CLA --> GUARD[Persona guard<br/>strips borrowed identity]

    LOCAL --> SPEAK
    GUARD --> SPEAK([Spoken aloud<br/>+ drawn on the canvas])
    SPEAK --> LEARN{Worth keeping?}
    LEARN -->|yes| REC[Recipe written<br/>3 repeats → automation]
    LEARN -->|no| END([Idle])
    REC --> END

    style WAKE fill:#0d1117,stroke:#8957e5,color:#e6edf3
    style CASCADE fill:#0d1117,stroke:#d29922,color:#e6edf3
    style LOCAL fill:#0d1117,stroke:#3fb950,color:#e6edf3
    style GROQ fill:#0d1117,stroke:#d29922,color:#e6edf3
    style CLA fill:#0d1117,stroke:#f85149,color:#e6edf3
    style REC fill:#0d1117,stroke:#3fb950,color:#e6edf3
    style SPEAK fill:#0d1117,stroke:#8957e5,color:#e6edf3
```

### The same flow, in words

| Step | What happens | Why it's built that way |
|:--|:--|:--|
| **1 · Wake word** | Speech must start with "Nova". Typed input never needs it. | The privacy boundary. Nothing leaves the mic without it. |
| **2 · Noise gate** | Whisper's own confidence score throws away the TV, mumbling and half-words. | A wrong transcript acted on is worse than one ignored. |
| **3 · Cascade** | Six tiers, cheapest first. Most days most requests never reach step 4. | Reaching for a model is treated as a failure to have learned. |
| **4 · Context** | Before any model runs, Nova adds your profile, the last few journal entries, a matching recipe, and names a fitting skill. | A model that starts knowing yesterday and knowing what worked last time needs fewer turns. |
| **5 · Groq** | Answers in about a second with 23 tools: the local index, app clicking, web search, its own status. | Free and fast. This tier is meant to *finish* jobs, not triage them. |
| **6 · Escalation** | Out of steps, a tool failed, or genuinely complex → the whole job goes to Claude, **told what Groq already tried.** | You should never have to do the routing by hand. |
| **7 · Guard** | A regex net catches a backend introducing itself or blaming "the API". | Whatever runs underneath is Nova's business, not yours. |
| **8 · Learning** | A success is recorded as a recipe; three repeats writes an automation. | This is the step that makes tomorrow cheaper than today. |

### A worked example

> **You:** *"Nova, post something on LinkedIn about the release."*

1. No regex matches, no automation matches → the cascade falls through to a brain.
2. Groq gets your profile and the last three journal entries, so it knows what "the
   release" refers to without asking.
3. It calls `draft_post` — which **stages a draft and sends nothing**.
4. Nova reads the draft back to you: *"Here's what I'd post…"*
5. Nothing goes out until you say so. There is no tool for approving a draft, by
   design — a model cannot decide to publish ([why](docs/publishing.md)).
6. A recipe records that this worked, so the next one is quicker.

---

## Quick start

```powershell
pip install -r requirements.txt

python main.py                        # microphone + canvas + terminal dashboard
python main.py --text --no-browser    # type instead of talking, no canvas
python main.py --mode window          # native window with a tray icon
python main.py --tray                 # start hidden (Ctrl+Alt+N shows it)
python main.py demo                   # scripted canvas demo, no mic, no usage
```

> First voice run downloads the Whisper `base.en` model (~150 MB), once.
> Make sure `claude` works in a terminal first.

<details>
<summary><b>Other sub-commands</b></summary>

```powershell
python main.py stats                  # module and line counts
python main.py stats --routes         # which tier answered, by week
python main.py models                 # what your Groq key can actually run
python main.py voices                 # installed SAPI voices
python main.py voice-check            # is this still the same Nova? (scores its own replies)
python main.py sysindex find "invoice" --kind file
python main.py ui controls "Calculator" --filter plus
python main.py macro run "search wikipedia for ada lovelace"
python main.py autostart install      # start in the tray at logon
```

</details>

---

## Contents

| | | |
|:--|:--|:--|
| [User flow](#user-flow) | [Things to say](#things-to-say) | [The four brains](#the-four-brains) |
| [Getting cheaper](#getting-cheaper-over-time) | [Beyond answering](#beyond-answering) | [The canvas](#the-canvas) |
| [Voice](#voice) | [Clicking things](#clicking-things) | [Your phone](#your-phone) |
| [Who Nova is](#who-nova-is) | [Architecture](#architecture) | [Reproducing it](#reproducing-it-from-scratch) |
| [Troubleshooting](#when-things-go-wrong) | [Packaging](#packaging-and-autostart) | [Further reading](#further-reading) |

---

## Things to say

| You say | What happens |
|:--|:--|
| "Create a notes file with a shopping list" / "fix the failing test" | Straight to Claude Code, which does it; the reply is spoken |
| *(while it's working)* "also check the python version" | Queued, sent when the current turn ends |
| "Let's plan a notes app with login" | Planning mode: a plan is drafted, nothing changes, the canvas draws it as a graph |
| "Remove step 2" / "read the plan" / "go ahead" | Edit or read the plan locally, then run it |
| "Move step 2 down" / "move step 1 to 3" | Rearrange the plan, or drag the step on the canvas |
| "Status" / "cancel the task" / "cancel all tasks" / "stop" | Progress on every task, kill one or all, stop talking |
| "Use Antigravity" / "use Groq" / "new session" | Switch brain / start a fresh conversation |
| "Open Spotify" / "find my resume" / "open 2" | Local index, instant |
| "Find duplicates in downloads" / "my most used apps" | Local duplicate scan / frecency list |
| "Find my phone" / "torch on" / "text Priya saying running late" | Your phone, over Tailscale |
| "News briefing" / "show the canvas" / "goodbye" | Headlines with no model at all / open the canvas / quit |
| "What's the latest on the Nvidia earnings?" | Groq searches the web itself — no Claude turn |
| "What are you working on?" | Real state: running tasks, recordings, drafts, goals |
| "Record my screen" / "that's it, stop recording" | ffmpeg screen capture into the inbox |
| "Take a picture with my phone" | The phone camera, if you've switched it on in your profile |
| "Every morning, tell me what changed in my repos" | A standing goal Nova fires on its own |
| "What are your standing goals?" | Reads them back |
| "What's waiting to go out?" / "post it" / "drop that draft" | The approval gate in front of anything outward-facing |
| "What did we do today?" / "recap yesterday" | Work history, no model needed |

---

## The four brains

Say **"use claude"**, **"use groq"**, **"use openrouter"** or **"use antigravity"**. The
canvas header shows which is active.

| | What it is | Best at |
|:--|:--|:--|
| **Groq** *(default)* | Open models on Groq's chips, answering in about a second, acting through *Nova's* 23 tools. Free. | Nearly everything: questions, lookups, opening things, automations, status |
| **Claude Code** | A brain with hands already attached: terminal, file editing, web. Runs on your subscription — no API key. | Coding, file edits, and whatever Groq couldn't finish |
| **OpenRouter** | The same loop against free models. | A fallback when Groq is rate-limited |
| **Antigravity** | Google's `agy` CLI. | An alternative coding agent |

**Groq leads on purpose.** It costs nothing and replies in about a second, so the
question is not whether it's as good as Claude — it's how much it can finish by itself.
Nova supplies the loop and the hands, in five groups:

| Group | Tools |
|:--|:--|
| **This machine** | `find_items` · `open_item` · `find_duplicates` |
| **Inside apps** | `list_windows` · `list_controls` · `click_control` · `type_in_app` · `press_keys` · `read_control` |
| **The web** | `web_search` · `read_page` |
| **Nova itself** | `nova_status` · `list_automations` · `run_automation` · `write_word` · `start_recording` · `stop_recording` · `draft_post` · `add_goal` · `list_goals` |
| **Skills & handover** | `list_skills` · `read_skill` · `delegate_to_claude` |

Three of those changed how much reaches Claude at all:

- **`web_search` / `read_page`** — keyless DuckDuckGo search and a page reader, stdlib
  only. Before these, *every* question about anything current had to go to Claude,
  because a chat model with no internet can only guess or delegate.
- **`nova_status`** — Groq can see its own state: running tasks, whether a recording is
  going, drafts waiting for approval, standing goals. "What are you working on" is now
  answerable from real state rather than guessed at.
- **`delegate_to_claude`** — still the escape hatch, but now the *last* resort rather
  than the first, and it carries a `skill` argument so Groq names the right skill when
  handing over.

There is deliberately **no tool for approving a draft**. The gate in front of everything
outward-facing exists so a model can never decide to publish.

<details>
<summary><b>When Groq can't finish — the handover</b></summary>

The cascade is only honest if its bottom tier failing means the work *rises*, not that
the work stops. Three paths now escalate automatically:

| Situation | What happens |
|:--|:--|
| Used all `max_tool_calls` steps | The whole job goes to Claude |
| Groq unreachable or rate-limited | The job goes to Claude; you still get an answer |
| A tool failed so it can't finish | Hands over rather than reporting a dead end |

Claude is told **what Groq already tried**, so the expensive tier doesn't repeat the
cheap tier's dead work. A cancelled turn never escalates — saying "stop" must not
quietly start a slower, costlier turn. Set `escalate_to_claude = false` to turn it off.

There is also a guard for a specific small-model failure: replying *"I've handed that to
Claude"* **without calling the tool**. Describing the handover reads, to a small model,
like performing it. Nova detects the claim and performs the delegation for real, because
a fabricated action is the one failure you cannot detect for yourself.

</details>

<details>
<summary><b>Configuration and the tool-calling catch</b></summary>

```toml
[agents]
default = "groq"                 # groq | claude | antigravity | openrouter

[agents.groq]
model = "openai/gpt-oss-120b"    # python main.py models
api_key_env = "GROQ_API_KEY"
max_tool_calls = 6
escalate_to_claude = true        # out of steps or unreachable → hand the job up

[agents.openrouter]
model = "openrouter/free"
api_key_env = "OPENROUTER_API_KEY"
```

**Pick a model that supports tool calling**, or it can only talk. On Groq that means
`openai/gpt-oss-120b` (default), `qwen/qwen3.8-27b` or `openai/gpt-oss-20b` — the
Llama-based `groq/compound` models reject tool calls outright. `python main.py models`
lists what your key can actually run, which is the only reliable answer.

Measured on Groq: "capital of Japan" in 0.5 s, a `find_items` lookup in 2 s, two errands
in parallel both answered in about 2 s. Free tiers rate-limit; if a model garbles a tool
call, the turn is retried once without tools so you still get an answer.

</details>

---

## Getting cheaper over time

Three mechanisms, softest first. Together they're the reason work moves *down* the
cascade instead of sitting at the top.

### Recipes — what worked, so it isn't worked out twice

When an agent turn succeeds, Nova records **how**: which tools in what order, how long,
which brain, and what failed on the way. Next time a similar request arrives, that
account goes in front of the model.

```
You have done this kind of thing before ("post an update about the release to linkedin").
What worked, in order: list_windows, click_control, type_in_app.
It took Claude Code about 52 seconds.
Known dead ends, don't repeat them: the LinkedIn compose box needs a click before typing
Treat this as evidence, not a script: do what fits this request.
```

The point is **not replay**. The expensive tier solves something once, and after that
the cheap tier can read how it was done and do it itself.

> **The dead ends are the valuable half.** A successful transcript shows the path that
> worked; it cannot show the three that didn't, and those are exactly what the next
> model will try. *"The window is called Spotify Premium, not Spotify"* is one line that
> saves two wasted tool calls, and no amount of cleverness infers it from a clean run.

A hint about the *wrong* task is worse than no hint — a model given a confident
irrelevant instruction follows it anyway — so matching is strict and stays silent far
more often than not. Full detail: [`docs/recipes.md`](docs/recipes.md).

### Skills — the same documents Claude Code reads

Point `[skills] paths` at a folder of `SKILL.md` files and every brain Nova has can use
them. A skill written once teaches all of them.

| | How |
|:--|:--|
| **Always in the prompt** | Just the *names* — all 18 cost about 50 tokens |
| **Loaded on request** | `read_skill` fetches one document, truncated to a budget |
| **When names aren't enough** | `list_skills` returns the descriptions |
| **Handing over** | `delegate_to_claude` takes a `skill` argument, so Claude doesn't re-choose |

These documents run to 1,500 lines and Groq's whole reply is capped near 1,000 tokens,
so loading them wholesale would cost more than the answer. Hence the two stages.

> **Telling a model "read a skill if one fits" does not work.** Verified: asked what the
> bouncy popover effect is called, Groq answered "spring animation" from memory and never
> opened `animation-vocabulary` — whose own description quotes that exact question, with
> the answer "Pop in". Strengthening the prompt didn't help either. So Nova matches the
> request against skill descriptions *locally* and names the one skill that fits. Then it
> loads it, and answers correctly.

### Automation promotion — the end state

Three repeats of the same kind of request and Claude writes the steps as
`automations/<task_type>.toml` itself. From then on it needs no model at all. See
[AGENTS.md §12](AGENTS.md).

---

## Beyond answering

Nova also does things nobody asked for in that moment.

| | What it does | Detail |
|:--|:--|:--|
| 📔 **Journal** | One short entry per finished day, written from the work history — never invented. The last few entries go into every brain's instructions, which is where continuity between sessions comes from. | [`docs/journal.md`](docs/journal.md) |
| 🎯 **Standing goals** | *"Every morning, tell me what changed in my repos."* The one place Nova starts something itself. | [`docs/standing_agent.md`](docs/standing_agent.md) |
| 🎬 **Capture** | Screen recording via ffmpeg, phone camera photos, and local CPU cut/caption/reframe. | [`docs/standing_agent.md`](docs/standing_agent.md) |
| 📤 **Publishing** | GitHub, LinkedIn and Instagram — all three behind one approval gate. | [`docs/publishing.md`](docs/publishing.md) |
| 🎙 **Voice drift check** | `python main.py voice-check` scores Nova's replies against its own character, so "the voice has slipped" becomes a number with a date. | `assistant/persona/drift.py` |

Three properties hold across all of them, and each is a line of code someone will later
be tempted to delete:

- **A goal is a request, not a privilege.** A due goal is submitted through the same
  pipeline you speak into, so every confirmation guarding a text, a call or a post still
  guards a goal. Nothing fires mid-conversation or during quiet hours.
- **Nothing sends itself.** Everything outward-facing is staged as a draft, read back to
  you, and sent only when you approve *that specific draft*. There is no "yes to
  everything" switch, because that's the switch that eventually posts the wrong thing.
- **Footage is never deleted automatically.** It's the one thing you cannot re-shoot.

---

## The canvas

A live graph of each request as it happens — what you said, how it was routed, the plan
steps with their branches, each action taken, and the result.

> **Design direction — "Living Ink":** ink-black ground, **Fraunces** for anything Nova
> *says or is*, **IBM Plex Mono** for anything a machine *measured* (paths, figures,
> timestamps), coral `#ff8a65` and violet `#7c5cff`. Fonts are self-hosted, so it looks
> identical offline. The blob is driven by the real status slice — never a decorative
> loop.

| | |
|:--|:--|
| **Click any node** | An inspector with the full untruncated text and copy buttons |
| **Every turn logged raw** | `data/sessions/<date>/<backend>/turn-NN.jsonl`, surviving restarts |
| **History rail** | The last 20 requests; click one to see its graph again |
| **Parallel tasks** | Errands start immediately; big jobs queue. One lane and tab each, so conversations never mix |
| **Cost meter** | What the session cost and how many turns it took |
| **Mic level, minimap, theme toggle** | Plus a filterable activity feed and adaptive quick-action chips |
| **Restored after a restart** | Conversation and plan both |

Only one task talks out loud; the others report when they finish. `max_parallel` under
`[agents]` (default 2) caps the total; the errand-versus-big-job sorting lives in
`assistant/triage.py`.

---

## Voice

Nova speaks with the Windows voice (SAPI), and **only** that. Free cloud voices sound
better for about four sentences and then rate-limit, leaving the assistant silent
mid-reply. A local voice is instant, free, and never fails.

```toml
[voice]
engine = "auto"                # auto | sapi (Windows) | pyttsx3
voice_contains = "Zira"        # python main.py voices
rate = 0                       # -10..10
```

- **"Stop" still works**, cutting speech within about 0.2 s.
- **Punctuated for the ear.** Every brain is told to write commas where a person would
  pause, and Nova normalises what it says: markdown removed, line breaks become sentence
  breaks, "50%" becomes "50 percent", `notes.txt` stays one word. Speech engines take
  their rhythm from punctuation, so this is most of what makes a local voice sound human.

---

## Clicking things

| | How | Speed |
|:--|:--|:--|
| **Automations** | TOML scripts in `automations/`, triggered by their phrases. Each step lights up on the canvas; a failed step turns red and says why. | ~1–8 s, free |
| **Desktop apps** | Claude lists a window's controls and clicks them **by name** via Windows UI Automation — no pixel guessing. | ~45 s |
| **Websites** | Claude drives a browser with the Playwright MCP tools. | ~20 s+ |

```powershell
python main.py ui controls "Calculator" --filter plus
python main.py ui click "Calculator" "Five"
python main.py macro run "search wikipedia for ada lovelace"
```

See [`automations/README.md`](automations/README.md) for the step reference.

---

## Your phone

An Android phone becomes part of the assistant: a Termux listener reachable **only over
Tailscale**, speaking a fixed list of named actions. There is no "run a command"
endpoint.

```mermaid
flowchart LR
    V([You]) --> N[Nova<br/>on the PC]
    N -->|named action + token| T[Tailscale]
    T --> B[nova_bridge.py<br/>in Termux]
    B --> A[Termux:API]
    A --> P([Your phone])
    B -->|what actually happened| N

    style N fill:#0d1117,stroke:#7c5cff,color:#e6edf3
    style T fill:#0d1117,stroke:#0969da,color:#e6edf3
    style B fill:#0d1117,stroke:#3fb950,color:#e6edf3
    style A fill:#0d1117,stroke:#3fb950,color:#e6edf3
```

**All verified working with the screen locked:**

| Doing — reveals nothing about you | Reading — gated by your profile |
|:--|:--|
| Torch · vibrate · notification | Contacts |
| Open an app or URL | Text messages |
| Clipboard **set** · volume | Call log · notifications |
| Send an SMS · place a call *(confirmed first)* | Clipboard **get** · location |
| **Find my phone** — it says *"Here is your phone"* out loud | |

> **The rule this taught us.** `termux-telephony-call` exits `0` whether or not Android
> actually placed the call. Nova reported "Calling now" for calls that never happened.
> It now checks the phone's real call state and has three honest answers: *calling*,
> *dropped, and why*, or **"I can't tell"** — that third one being the important one.
> **An assistant that says it did something it didn't is worse than one that can't do it
> at all.**

---

## Who Nova is

Every backend has an identity baked in by training, so a persona prompt only argues with
it. Nova handles that in three layers, matching the cascade:

```mermaid
flowchart TD
    Q([&quot;Who are you?&quot;]) --> I{Instant intent}
    I -->|matched| L[Answer composed locally<br/>from the character]
    I -->|unusual phrasing| M[A backend answers,<br/>carrying the character]
    M --> G{Guard}
    G -->|introduced itself as a model| L
    G -->|fine| S([Spoken])
    L --> S

    style I fill:#0d1117,stroke:#3fb950,color:#e6edf3
    style G fill:#0d1117,stroke:#d29922,color:#e6edf3
    style L fill:#0d1117,stroke:#7c5cff,color:#e6edf3
```

The character is built out of **reasons, not adjectives** — what Nova cares about, in
order, with *truth about what actually happened* at the top. Failures are owned:
*"I couldn't finish that"*, never *"the provider failed"*.

> **One boundary, deliberately kept.** The guard replaces a backend introducing itself as
> a model, but leaves an honest answer about what Nova is built on completely alone.
> Answering as Nova is branding; denying what it runs on would be a lie — and not lying
> is the first thing this character is for.

Set who Nova says made it with `[assistant] maker` in `config.toml`. The character lives
in [`assistant/persona/character.md`](assistant/persona/character.md); when the voice
drifts, add a row to its examples table rather than another adjective.

---

## Architecture

```mermaid
flowchart TD
    subgraph IN [" Input "]
        MIC[Microphone] --> GATE[Noise gate]
        GATE --> STT[Whisper, local]
        TXT[Canvas / phone text]
    end

    STT --> CTL[controller.py]
    TXT --> CTL

    subgraph ROUTE [" Routing "]
        CTL --> INT[intents.py]
        CTL --> MAC[automations/]
        CTL --> LOOSE[matching.py]
        CTL --> CLS[classify.py]
        CTL --> AG[agents/]
    end

    subgraph CTX [" Context added before any model "]
        PROF[profile/] --> AG
        JRNL[journal/] --> AG
        RCP[recipes/] --> AG
        SKL[agents/skills.py] --> AG
    end

    subgraph HANDS [" What a model can reach "]
        AG --> TOOLS[agents/tools.py<br/>23 tools]
        TOOLS --> WEBT[agents/web.py]
        TOOLS --> SYS[system/]
        TOOLS --> DESK[automation/desktop.py]
        TOOLS --> CAP[capture/]
        TOOLS --> PUB[publish/ → gate]
        TOOLS --> GOAL[goals/]
    end

    AG --> RUN[agent_runner.py]
    RUN --> PLAN[planning/]
    RUN -.->|success| RCP
    GOAL -.->|fires a request| CTL

    subgraph OUT [" Output "]
        BUS[(EventBus)] --> STATE[state.py<br/>versioned slices]
        STATE --> WS[ui/server.py /ws]
        WS --> CANVAS[React canvas]
        BUS --> TTS[SAPI voice]
    end

    INT --> BUS
    MAC --> BUS
    LOOSE --> BUS
    CLS --> BUS
    PLAN --> BUS

    style CTL fill:#0d1117,stroke:#7c5cff,color:#e6edf3
    style BUS fill:#0d1117,stroke:#ff8a65,color:#e6edf3
```

Modules talk through `EventBus` topics, never through each other's UI. State reaches the
canvas as **versioned slices** — only what changed, and only new rows for lists.

<details>
<summary><b>Module map</b></summary>

```
main.py                   CLI flags and sub-commands -> Settings -> VoiceAssistant
sysindex.py               local index CLI, usable from any folder
canvas/                   React + dagre frontend (npm run build -> assistant/ui/canvas_dist)
phone/nova_bridge.py      the Termux listener that runs on the phone
automations/              saved TOML automations
packaging/                nova.spec + build.ps1 (PyInstaller)

assistant/
  app.py                  wiring: worker thread for input, main thread for window/tray
  controller.py           wake word -> the four tiers -> agent; queues, planning mode
  intents.py              regex command matching (~0 ms, no model call)
  matching.py             the same request said differently (filler, synonyms, order)
  classify.py             free-model router, grounded in the scripts that exist
  routes.py / stats.py    which tier answered, counted by week
  triage.py               errand or big job
  agent_runner.py         parallel turns, plan markers, step inference
  events.py / state.py    pub/sub bus and the slices the UIs render
  persona/                character.md, seed.md, guard.py — who Nova is
  audio/                  microphone, transcriber, tts, gate
  agents/                 claude_cli, antigravity_cli, chat_api, tools, rules, registry
  planning/               plan.py (DAG + markers), graph.py (live nodes and edges)
  automation/             macros, desktop (UI Automation), browser (Playwright),
                          office (Word), promotion (writes itself out of the job)
  system/                 SQLite index: db, indexer, apps, search, ranking, duplicates
  profile/ history/       who you are; timed work sessions, search, recap
  awareness/ ambient/     window-title activity; weather, markets, headlines
  phone/                  the PC half of the phone bridge
  ui/                     server (FastAPI + WebSocket), auth (shared secret),
                          shell (window + tray), win32 (hotkey, single instance)
```

</details>

<details>
<summary><b>Plans, and how steps light up live</b></summary>

Plans come back as `[[PLAN: title]] 1. ... 2. ... (after 1) [[/PLAN]]`. A step without
`(after ...)` depends on the previous one. While executing, the agent prints
`[[STEP n START]]` / `[[STEP n DONE]]`. Models often run several steps back to back and
print markers only at the end, so the runner also matches each action's target (such as
`style.css`) against the step texts to light up the right step live.

</details>

<details>
<summary><b>Latency, index and ranking</b></summary>

**Latency.** The Claude session starts when Nova starts, so it's ready when you first
speak. Each request is a new turn in the same process: no CLI start-up cost, and the
whole conversation is remembered. Simple questions take about 2 s, small file tasks
6–15 s. If nothing has come back after 1.5 s, Nova says "On it."

**Index.** Apps come from `Get-StartApps` and launch via `shell:AppsFolder`; files come
from your user folders with FTS5 trigram search. Ranking combines match quality with
frecency — each use adds a point, points halve every `half_life_days`. Re-indexing is
incremental. Duplicates are narrowed by size, then first/last 64 KB, then a full BLAKE2b
hash, cached.

</details>

---

## Reproducing it from scratch

### 1 · Prerequisites

| Needs | Why | Check |
|:--|:--|:--|
| Windows 10/11 | UI Automation and the tray live here | — |
| Python 3.11 | everything | `python --version` |
| `claude` CLI, logged in | the main brain; subscription, not an API key | `claude -p "say hi"` |
| Node 18+ | only to rebuild the canvas | `node --version` |
| A microphone | only for voice; `--text` works without one | — |

*Optional:* `agy` (Antigravity), Tailscale, Termux + Termux:API on an Android phone.

### 2 · Install and configure

```powershell
git clone <this repo> C:\Assisstant
cd C:\Assisstant
pip install -r requirements.txt
copy .env.example .env
```

**Nothing in `.env` is required to start Nova.** Each block switches on one capability,
and a missing key disables that feature rather than breaking startup.

| Key | Unlocks | Without it |
|:--|:--|:--|
| `GROQ_API_KEY` | the free-model router and the Groq brain | unrecognised phrasings go to Claude |
| `OPENROUTER_API_KEY` | free models as an alternative brain | — |
| `NOVA_PHONE_TOKEN` | the phone bridge | no phone features |

> ⚠️ `.env` and `data/` are git-ignored. **A file named `env` (no dot) is not** — don't
> create one. That mistake was made here, and it held a live token.

### 3 · Run and check

```powershell
python main.py --text --no-browser     # simplest first run
python -m unittest discover -s tests   # 295 tests, ~12 s
python -m compileall -q assistant
```

Say or type `status`. If it answers, the loop works.

### 4 · The canvas

The built canvas ships in `assistant/ui/canvas_dist/`, so a fresh clone needs no Node.
Only rebuild after editing `canvas/src/`:

```powershell
cd canvas
npm install
npm run build
```

> Use **PowerShell** for this, not Git Bash — Git Bash resolves a broken `node` shim here.

### 5 · The phone bridge *(optional)*

1. Install **Termux** *and* **Termux:API** from **F-Droid** — both, from the same source,
   or the signatures won't match and API calls fail silently.
2. `pkg install python termux-api`
3. Install Tailscale on both; check they see each other with `tailscale status`.
4. Fetch the listener from Nova itself — no cable, no `scp`:

   ```bash
   python - <<'PY'
   import urllib.request
   r = urllib.request.Request("http://<pc-tailscale-ip>:8765/api/phone/bridge",
                              headers={"X-Nova-Token": "<from data/nova_token>"})
   open("nova_bridge.py", "w").write(urllib.request.urlopen(r).read().decode())
   PY
   ```
5. `termux-wake-lock && python nova_bridge.py`
6. Put the token it prints into `.env` as `NOVA_PHONE_TOKEN`, and the phone's Tailscale
   IP into `[phone] host` in `config.toml`.

> **Two Android settings decide whether half of this works**, and neither is
> discoverable:
>
> - **Battery → Unrestricted** for Termux, plus `termux-wake-lock`. Otherwise Android
>   freezes the listener and every request fails while `tailscale ping` still answers.
> - **Display over other apps** — on HyperOS/MIUI it's *Other permissions → Display
>   pop-up windows while running in background*. Without it Android silently drops
>   anything that starts an activity — opening an app, placing a call — while the screen
>   is locked. **The command still exits 0.**
>
> Grant Termux:API its Contacts, SMS and Phone permissions too, or those actions return
> empty results rather than errors.

**Verify it:** with the phone **locked**, say *"find my phone"*. It should speak, not
just buzz.

---

## When things go wrong

| Symptom | Cause |
|:--|:--|
| Phone requests fail, but `tailscale ping` answers | Android froze Termux → wake-lock + unrestricted battery |
| A call or app-open reports success, nothing happens | Background activity starts blocked → the permission above |
| "Find my phone" buzzes but doesn't speak | An older bridge on the phone → re-fetch `nova_bridge.py` |
| `401` from the phone | `NOVA_PHONE_TOKEN` ≠ the phone's `~/.nova_bridge_token` |
| `401` opening the canvas | Open it once with `?token=` (from `data/nova_token`); it leaves a cookie |
| Canvas edits do nothing | `npm run build` not run, or run from Git Bash |
| An automation fails one step | Expected — Claude finishes the request, then patches the automation |

---

## Packaging and autostart

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1   # -> dist\Nova\
dist\Nova\nova-cli.exe autostart install                       # Task Scheduler, at logon
dist\Nova\nova-cli.exe autostart status | remove
```

A PyInstaller `--onedir` build containing `Nova.exe` (no console: tray and window) and
`nova-cli.exe` (console: `--text`, `sysindex`, `autostart`). Settings live in
`dist\Nova\config.toml`; logs go to `%LOCALAPPDATA%\Nova\nova.log`. The autostart task
starts 20 s after logon, restarts up to 3 times on a crash, and needs no UAC prompt.

<details>
<summary><b>Desktop app details</b></summary>

- **Window mode** hosts the canvas in a native WebView2 window. Closing hides to the tray.
- **Tray icon**: the ring colour follows status — green listening, amber thinking, violet
  working, slashed when muted. Menu: Show canvas, Mute, Cancel task, Quit.
- **Hotkey**: `hotkey = "ctrl+alt+n"` under `[ui]`.
- **Single instance**: launching again just brings the window forward.

</details>

<details>
<summary><b>Permissions for Claude Code</b></summary>

`[agents.claude] permission_mode = "auto"` (default): safe actions including shell
commands are approved and run; risky ones are denied and Nova tells you, so you can
answer by voice. `acceptEdits` allows file edits only. `bypassPermissions` skips all
checks — use with care. To always allow specific tools, add entries like
`"Bash(npm test:*)"` to `allowed_tools`.

</details>

---

## Further reading

| Document | What it covers |
|:--|:--|
| [`docs/overview.md`](docs/overview.md) | The whole system: architecture, abilities, security, and honest limits |
| [`AGENTS.md`](AGENTS.md) | The rules any coding agent follows in this repo |
| [`automations/README.md`](automations/README.md) | The TOML automation format |
| [`docs/phone.md`](docs/phone.md) · [`phone/README.md`](phone/README.md) | The phone bridge end to end |
| [`docs/personalization.md`](docs/personalization.md) · [`docs/history.md`](docs/history.md) | Profile and work history |
| [`docs/recipes.md`](docs/recipes.md) | How a job that worked is recorded and fed back |
| [`docs/journal.md`](docs/journal.md) | The day-by-day record that gives Nova continuity |
| [`docs/standing_agent.md`](docs/standing_agent.md) | Standing goals, screen capture and editing |
| [`docs/publishing.md`](docs/publishing.md) | GitHub, LinkedIn, Instagram — and the gate in front of them |
| [`docs/document_standards.md`](docs/document_standards.md) | How written documents and diagrams are produced |
| [`docs/business_pipeline.md`](docs/business_pipeline.md) | A designed-but-unbuilt outreach pipeline |

<div align="center">
<br>
<sub>Built to get quieter, cheaper and more useful the longer it runs.</sub>
</div>
