# UI rethink: one assistant, four spaces

Status: **proposal, with mockups** (`docs/mockups/nova-ui.html`). Nothing in `canvas/src` has
changed yet. Written 2026-10-13, after the publishing pipeline landed.

## Why the canvas needs a new structure

The canvas grew one tab per feature: Canvas · History · Profile · Activity · Memory · Usage,
plus a Briefing drawer and a talk-mode chip. Three problems came with that:

1. **Half of Nova has no screen.** Drafts and approvals, scheduled posts, the schedule and
   reminders, standing goals, saved and proposed automations, the capture inbox, the journal
   and the phone exist only as voice commands and CLI verbs.
2. **What needs the user has no home.** A draft to approve, a proposed automation, a blocked
   permission, or a reminder is said once, out loud. Miss it and it's gone. There is no inbox.
3. **The tabs mix kinds of thing.** Live work (Canvas), the user's own data (Profile, Memory,
   Activity) and machine metrics (Usage) sit side by side as equals, and the status bar shows
   developer detail (workspace, agent, cost, turns) on every screen.

The talk-mode chip goes away too: explicit modes are being replaced by a worker that tells an
actionable request from conversation on every turn (the next piece of work after this one).

## The structure

Four spaces, one inbox, one composer.

| Space | Question it answers | Holds |
|---|---|---|
| **Now** | What's happening, and what do you need from me? | Nova itself (the blob, driven by the real status), the conversation as one continuous thread, *Needs you* (the inbox), *Running*, *Today* |
| **Work** | What have you been doing for me? | Tasks and their plan graphs (today's Canvas), history of runs, standing goals, automations (saved + pending proposals), recipes |
| **Studio** | What goes out under my name? | The publishing calendar, drafts and scheduled posts with their images, the projects ledger, capture inbox (recordings, phone photos), which arms are signed in |
| **You** | What do you know about me? | Memory (AfterThought), Activity, Journal, Profile, Phone, Usage, settings |

**Needs you** is the one place everything that waits on a decision lands, with its buttons:
approve / schedule / drop a draft, keep / discard a proposed automation, a blocked permission,
a reminder that's due, a sign-in about to expire (LinkedIn warns 7 days ahead). Its count
shows on the Now tab from anywhere. Voice still works ("post it", "keep that automation");
the inbox is where the same decision can be made by hand, later.

**The composer** (type, or just talk) is global and sits at the bottom of Now. `Ctrl K`
opens it from any space. There is one conversation, not one per mode.

## Where every feature lives

| Feature (module) | Today | Proposed home |
|---|---|---|
| Live plan graph (`planning/`, `FlowCanvas`) | Canvas | Work → selected task; a compact *Running* strip on Now |
| Transcript (`Sidebar`) | right sidebar | Now → the conversation |
| Briefing / Today (`ambient/`, `today/`) | drawer | Now → *Today* column |
| History (`history/`) | tab | Work → history list |
| Goals (`goals/`) | none | Work → goals |
| Automations + proposals (`automation/`, `automations/pending/`) | none | Work → automations; proposals also in Needs you |
| Recipes, ladders | none | Work → automations (detail) |
| Drafts, scheduled posts, ledger (`publish/`) | none | Studio; pending ones also in Needs you |
| Capture inbox (`capture/`) | none | Studio → inbox |
| Schedule + reminders (`schedule/`) | none | Now → Today; due reminders in Needs you |
| Memory (`afterthought/`) | tab | You → Memory; a live suggestion can surface on Now |
| Activity (`awareness/`) | tab | You → Activity |
| Journal (`journal/`) | none | You → Journal |
| Profile (`profile/`) | tab | You → Profile |
| Phone (`phone/`) | none | You → Phone |
| Usage (`usage/`) | tab | You → Usage; the dev status bar folds into it |
| Talk modes (`conversation.py`) | chip | removed; the understanding worker replaces it |
| Permission denials | banner | Needs you |

## State and server

No new transport. Each space reads existing slices; two new ones are needed:

- `inbox`: a derived list built in `state.py` from the publish gate (pending + scheduled),
  pending automation proposals, permission denials, due reminders and expiring sign-ins.
  Each item carries `kind`, `title`, `detail`, and the actions it accepts; actions go back
  through the same intents the voice uses, so there is one code path per decision.
- `studio`: the publish service's `state()` plus the showcase ledger and capture inbox.

## Build order

1. Shell: four spaces + global composer + Needs you count (no screen content moves yet).
2. Now: conversation + Needs you + Running + Today.
3. Studio (the newest features, and the ones with no UI at all).
4. Work: move the canvas graph in, add goals and automations.
5. You: move Profile, Memory, Activity, Usage in; add Journal and Phone.
