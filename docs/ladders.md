# Fallback ladders

When an approach fails, a model tends to guess again, and often retries the same wrong
command. Every guess costs a turn. Ladders fix the order in advance: for each kind of
request, the cheapest route first and the next one to take when it fails. They also record
which route actually worked, so the next attempt starts there.

## Two layers

**1. In code: zero tokens.** Clicking in the browser escalates by itself:

| Rung | What | Why it's needed |
|---|---|---|
| 1 | scripted click inside the page | fast, and enough for most buttons |
| 2 | real mouse click (`chrome.debugger`) | Chrome only lets a real click open a window, a popup or an app |
| 3 | app link (`whatsapp:`, `mailto:`, `tel:` …) opened through Windows | Chrome won't hand those to an app from a script; only known app schemes are allowed |
| 4 | Windows accessibility (UI Automation) on the Chrome window | Chrome treats it as a person: the click that finally worked on a WhatsApp button |

- **Starting point:** a site where rung 1 never works is remembered, and its next click starts at rung 2.
- **Never twice:** nothing that could send, pay, post or delete is clicked a second time automatically. The reply says "it may still have worked; check before retrying".
- **Recognising success:** a click that opens a new tab or window, or turns a toggle on (`(pressed)`), counts as a change, so it isn't repeated.

Code: `extension/background.js` (`click`) handles rungs 1–2, and `finish_click` in `assistant/automation/extension.py` handles rungs 3–4.

**2. In the prompt: a few dozen tokens.** `ladders.toml` lists, for each kind of request,
the order to try things in. Nova adds the matching ladder to a request, at most two, and a second only
when two or more of its words match. Claude Code and Antigravity get the command forms; the free chat models get their tool
names.

| Kind | Starts with | Then |
|---|---|---|
| click | `web click` (escalates by itself) | another element → keyboard → accessibility by name |
| open | `sysindex open` | website → Start menu → path |
| close | the app's own close (Alt+F4, Ctrl+W) | close button → stop the process (ask first) |
| find_file | `sysindex find` | the named folder → widen one level → ask after two misses |
| fill_form | `web form` (whole form in one call) | one field → accessibility typing → submit only after a yes |
| send_mail | a draft for approval | Gmail compose link → mail app |
| message | SMS through the phone | WhatsApp app link → WhatsApp Web |
| research | search snippets | read 1–3 pages → real browser → hand to Antigravity |
| plan | what is already known | read only what decides it |
| write_doc | draft once to .md | Word → social post (staged) |
| app_control | saved automation | control by name → keyboard shortcut |

Small talk ("what time is it") matches nothing and costs nothing.

## Learning

Agent tool calls are already published on the bus. For a task that was given a ladder,
each command is matched against the rungs' `signs`. When the task ends, the last rung used
gets the credit and the ones before it are marked as tried and abandoned. Results are kept per site or window in
`data/ladders.json`, and once a rung has worked twice it's offered back:

> On WhatsApp, 'accessibility by name' worked 2 of 2 times: start there; 'web click' keeps failing, skip it.

The words in a request pick the target ("whatsapp" finds `web.whatsapp.com`). With no
evidence, nothing is said, because a confident wrong hint costs more than no hint.

## Editing

`ladders.toml` is re-read when it changes. Add a kind with its `when` words, its `rungs`
(name, `cli`, `tool`, `signs`), a `done` check and a `stop` line. Keep each rung to one line:
everything in the file is sent to a model whenever it matches.

Settings: `[ladders]` in config.toml. Tests: `tests/test_ladders.py`.
