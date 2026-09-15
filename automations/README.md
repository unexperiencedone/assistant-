# Automations

Each `.toml` file here is a scripted action Nova runs when you say one of its phrases. Automations run instantly, use no Claude usage, and light up step by step on the canvas; if a step fails, its box turns red and says why. Nova picks up new or edited files without restarting.

Try one from a terminal:

```
python main.py macro list
python main.py macro run "calculator demo"
python main.py macro run "search wikipedia for alan turing"
```

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
| `click` | `window`, `name` or `auto_id`, optional `control_type`, `index` | Presses the control without moving the mouse when it can |
| `type` | `window`, `text`, optional `into` (field name) | |
| `keys` | `window` (optional), `keys` | pywinauto syntax: `^` Ctrl, `%` Alt, `+` Shift, `{ENTER}`, `{TAB}` |
| `read` | `window`, `name` or `auto_id`, `save_as` | Saves the control's text for later steps |
| `media` | `key` = `play_pause`, `next`, `previous`, `stop`, `volume_up`, `volume_down`, `mute` | Works with Spotify and most players |

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
