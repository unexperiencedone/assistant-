# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person: the owner, Aakshant, a third-year CSE (AI) student, builder and co-founder in
Kanpur, working on a Windows 11 laptop with a big external monitor. He talks to Nova far more
than he types, and works across VS Code, Chrome, Claude, Spotify and many other apps all day.

## Product Purpose

Nova is a personal voice assistant that acts on the owner's computer: it opens and drives apps
and websites, runs tasks in the background with several AI brains, remembers what he worked
on, reminds him, publishes his work (LinkedIn, portfolio) behind an approval gate, and gets
cheaper over time by turning repeated requests into saved automations. Success is that he
reaches for Nova instead of doing the thing by hand, on anything he's working on.

## Positioning

It lives on his own machine with his own logins and data, acts instead of only answering,
proposes before anything goes out under his name, and learns his habits locally. No
cloud assistant can see and drive his actual desktop the same way.

## Operating Context

- **Summoned, not parked.** The window lives in the tray and is opened when needed; it is
  not a permanent pane. (Confirmed 2026-10-13.)
- **Available over anything, without a window.** The owner wants to ask, while in any app,
  "help me with what's on the screen" and get help in place, with Nova aware of the screen
  he is looking at. Reaching Nova must not require switching to its window. (Confirmed.)
- Voice is the primary input: a wake word ("Nova") for speech, none for typing. A global
  hotkey and tray already exist (`ui/shell.py`).
- Two screens: a small laptop panel and a big external monitor.
- Desktop only; the phone needs to not break, not a designed layout. (Confirmed.)

## Capabilities and Constraints

- Jobs the UI must make easy, all confirmed as top priority: **deciding fast** (approvals),
  **seeing what Nova is doing** (live tasks and plans), **talking and thinking with it**, and
  **recalling his own stuff** (memory, activity, where he left off).
- Agreed information architecture (docs/ui_rethink.md): four spaces, Now / Work / Studio /
  You, a global composer, and a Needs-you inbox for every pending decision.
- Canvas is React (Vite) in `canvas/`, served by `ui/server.py`; state arrives as versioned
  slices patched over the wire. The shell window is a webview (`ui/shell.py`).
- The entity (the blob) must be driven by real status (idle, listening, transcribing,
  thinking, speaking, working), never a decorative loop.
- Nothing outward-facing is sent without approval of that specific item.
- Speech is local SAPI; no cloud TTS. No Anthropic API calls from the app.
- Talk modes are being removed: one continuous conversation, with Nova deciding per turn
  whether to act or talk.

## Brand Commitments

- Name: Nova. Personality is defined in `assistant/persona/character.md`.
- The incumbent visual language, "Living Ink" (canvas/src/styles.css), is the evidence a
  redesign starts from; the owner asked for new directions, so it is not binding.

## Evidence on Hand

Real data exists for publishing (drafts, scheduled posts, ledger), schedule, goals,
automations (20 saved), history, usage and profile. AfterThought memory has captured nothing
yet (it was just switched to watch every app except WhatsApp and Instagram), so any memory
content in a mockup is illustrative and must be labelled as such.

## Product Principles

1. Reachable in place: help comes to the app he's in; opening Nova's window is the deeper
   option, not the entry point.
2. Decisions never evaporate: anything waiting on him persists somewhere he can act on it.
3. Show the work honestly: what's running, what it did, what failed, in plain words.
4. Voice first, hands optional: every surface can be driven by speech; buttons mirror voice.
5. Quiet by default: proactive help appears when it's relevant, never as constant noise.
