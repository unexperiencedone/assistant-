# Automations

Each `.toml` file here is a scripted action Nova runs when you say one of its phrases. Automations run instantly, use no Claude usage, and light up step by step on the canvas; if a step fails, its box turns red and says why. Nova picks up new or edited files without restarting.

Try one from a terminal:

```
python main.py macro list
python main.py macro run "calculator demo"
python main.py macro run "search wikipedia for alan turing"
```

## Spotify

`play_song`, `play_genre`, `media_keys`, `next_track`, `previous_track`, `spotify_shuffle_on`,
`spotify_shuffle_off`, `spotify_smart_shuffle`, `spotify_repeat_one`, `spotify_repeat_all` and
`spotify_repeat_off` drive the desktop app. Two things to know before editing them:

- Spotify names the player-bar buttons after **what the next press will do**. Shuffle is
  "Enable Shuffle" (off) -> "Enable Smart Shuffle" (plain shuffle on) -> "Disable Smart Shuffle"
  (smart shuffle on); repeat is "Enable repeat" (off) -> "Enable repeat one" (looping the list) ->
  "Disable repeat" (looping one song). So a chain of `optional` clicks on those names lands on a
  known state from any starting point, which is how the shuffle and repeat automations work.
- "play X" searches with the `spotify:search:` URI and clicks the top result's "Play" button, which
  Spotify only exposes while something is already playing - hence the two attempts. Genre searches
  have no top result at all, so "play some X" is a separate automation that clicks the first
  "Play <playlist>" card.

## File format

```toml
name = "Wikipedia search"
description = "What it does, in one sentence."
phrases = ["search wikipedia for {query}", "look up {query} on wikipedia"]   # {query} captures words

[[steps]]
do = "browser_goto"
url = "https://en.wikipedia.org/"

[[steps]]
do = "browser_fill"
selector = "input[name='search']:visible"
value = "{query}"          # placeholders: phrase captures, earlier save_as values, {query_url} (URL-encoded)
press_enter = true
```

Any step can have `label = "..."` (the text shown on the canvas), `timeout = 10` (seconds) and `optional = true` (skip instead of stopping if it fails).

## Desktop app steps (Windows UI Automation)

| `do` | Fields | Notes |
|---|---|---|
| `open` | `app` (Start Menu name) or `target` (path, URL or URI like `spotify:search:{query_url}`) | Uses Nova's app index |
| `wait_window` | `title` | Waits until a window whose title matches is visible |
| `focus` | `window` | Brings it to the front |
| `click` | `window`, `name` or `auto_id`, optional `control_type`, `index`, `exact` | Presses the control without moving the mouse when it can. `exact = true` turns off the prefix/substring fallback, for short names like "Play" that would otherwise match "Play Album - Ed Sheeran" |
| `type` | `window`, `text`, optional `into` (field name) | |
| `keys` | `window` (optional), `keys` | pywinauto syntax: `^` Ctrl, `%` Alt, `+` Shift, `{ENTER}`, `{TAB}` |
| `read` | `window`, `name` or `auto_id`, `save_as` | Saves the control's text for later steps |
| `media` | `key` = `play_pause`, `next`, `previous`, `stop`, `volume_up`, `volume_down`, `mute` | Works with Spotify and most players |

`window` is matched against the title: exact, then prefix, then substring, then regex. Apps that rename
their window constantly (Spotify puts the current track there) are easier to hit by process instead:
`window = "exe:spotify"` matches whatever window `spotify.exe` owns.

To find control names, open the app and run:

```
python main.py ui controls "Calculator" --filter plus
```

It prints `Type<TAB>Name<TAB>AutomationId` for each clickable control. Use the Name in `click`; if it's empty, use the AutomationId with `auto_id`.

## Web steps (Playwright driving Microsoft Edge)

| `do` | Fields |
|---|---|
| `browser_goto` | `url`, optional `new_tab` |
| `browser_click` | one of `text`, `selector`, `role` + `name`, `label`, `placeholder` |
| `browser_fill` | a target as above, `value`, optional `press_enter` |
| `browser_press` | `key` (e.g. `Enter`, `Control+L`) |
| `browser_wait` | a target as above |
| `browser_read` | a target as above, `save_as` |

Nova starts Edge with its own profile (`%LOCALAPPDATA%\Nova\browser-profile`), so your normal Edge is untouched and sites you sign into there stay signed in. The window stays open when the automation ends. Selectors are Playwright selectors; `:visible` and `:has-text('...')` are handy when a page has hidden duplicates.

## Other steps

| `do` | Fields |
|---|---|
| `say` | `text` (spoken, may use saved values) |
| `wait` | `seconds` |

## Automations vs asking Claude

Use an automation for things you repeat and that always work the same way: it's instant and free. For one-off or open-ended requests ("find a cheap flight"), just say it: Claude drives the browser with its Playwright tools and desktop apps with `main.py ui`. When Claude works something out that you'll want again, ask it to save the steps as a new file here.

Nova also does this on its own now: every voice turn tags its reply with a hidden `[[TASK: type]]` marker, and once the same kind of request has gone through Claude enough times (or cost enough), Nova asks Claude, in the background, to write the automation itself from what it actually did. See AGENTS.md section 12 for exactly how the tagging and thresholds work, and `data/task_frequency.json` for the running counts. Say "remember that" or "teach yourself that" right after a request to skip the wait and save it immediately. If a saved automation ever fails on some case, Nova falls back to Claude for that one request and then asks it to patch the file, so a bad selector gets fixed instead of failing the same way forever.

## Driving the phone

`do = "phone"` runs one action on your phone through the Termux bridge (phone/README.md),
with no model in the loop — which is the point: a spoken phrase, about a second, no tokens.

```toml
[[steps]]
do = "phone"
action = "torch_off"

[[steps]]
do = "phone"
action = "notify"
title = "Nova"
text = "Goodnight."
```

Every key other than `do`, `action`, `label`, `optional` and `timeout` is passed to the
action as an argument. See `automations/phone_goodnight.toml` for a whole one.

Actions: `battery`, `torch_on`, `torch_off`, `vibrate`, `notify`, `open`, `clipboard_get`,
`clipboard_set`, `volume`, `location`, and the reading ones (`contacts`, `sms_list`,
`call_log`, `notifications`) where your profile allows them.

**Not** `sms_send` or `call_dial`: an automation runs unattended, and those are exactly
the two that have to be asked about first. The runner refuses them, and so does the phone.
