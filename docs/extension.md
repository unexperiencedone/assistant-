# The Nova Chrome extension

Nova can drive your own Chrome through a small extension (`extension/`). It replaces
Playwright as the browser driver whenever it is connected. Playwright stays as the fallback.

| | Playwright (before) | Extension (now) |
|---|---|---|
| Profile | separate Nova profile, no logins | your own Chrome, every login |
| How it connects | DevTools port 9333 into Chrome | Chrome connects *out* to Nova's server |
| What a model reads | page text, 1,800 to 8,000 characters | numbered element map, usually 300 to 2,000 characters |
| After an action | read the whole page again | only what changed (`+ [9] button "Continue"`) |
| A 6-field form | 6 or more tool calls | 1 `browse_form` call |
| Typing into React, Vue or rich editors | `fill()`, usually fine | native value setter + input events; `execCommand` for contenteditable |

## Install (once)

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and choose `C:\Assisstant\extension`.
3. Pair it by opening the Nova canvas in that Chrome once. The extension reads the
   canvas's `nova_token` cookie, so there is nothing to paste. Alternatively, paste
   the contents of `data/nova_token` into the extension's popup.

The toolbar icon shows the status. No badge means connected, `!` means Nova isn't running,
and `?` means the extension needs pairing. Check from a terminal with `python main.py web status`.

## How a page looks to a model

```
Create account — Example
https://example.com/signup
# Create account
[1] textbox "Full name" *
[2] textbox:email "Email" *
[3] textbox:password "Password" =•••
[4] select "Country" ="Choose" (3 options)
[5] checkbox "I agree to the terms" [ ]
[7] button "Create account"
```

`*` marks a required field and `=…` is the current value (secrets are always masked). The model acts by number. A number keeps
pointing at the same element for as long as the page is loaded, and that is what makes the diffs possible.
Long pages are cut to about 60 lines, with what is on screen first. `browse_find` finds the rest by words.

## Who uses it

- **Automations** (`automations/*.toml`): `browser_*` steps run through the extension when it is
  connected. Playwright's `:visible` suffix in selectors is understood.
- **Groq and OpenRouter models**: `browse_open`, `browse_read`, `browse_find`,
  `browse_click`, `browse_fill` and `browse_form` (`assistant/agents/tools.py`).
- **Claude Code**: `python main.py web ...`:

```
python main.py web open https://example.com/signup
python main.py web form 1="Ada Lovelace" 2=ada@example.com 4=India 5=yes
python main.py web click 7
python main.py web find "price"      # cheap: matching elements or text only
python main.py web read --limit 2000 # the page's text, when it's actually needed
python main.py web tabs | tab <id> | map [--all] | press Enter | scroll down | back
python main.py web click 7 --trusted # real mouse input, for sites that ignore synthetic clicks
```

## When a click is ignored

`web click` (and `browse_click`) escalate without a model: page click → real mouse click → app link through Windows → Windows accessibility. A site that needs the real click is remembered. Nothing that sends, pays or deletes is ever clicked twice. Details: docs/ladders.md.

## Files, and picking up a new version

- **`upload`** puts local files into a page's file input, the way picking them by hand
  would: through the debugger (`DOM.setFileInputFiles`), finding the input through shadow
  roots too. Automations and the YouTube fallback use it as `ExtensionBrowser.upload()`;
  over `/api/ext` it's `{"method": "upload", "params": {"files": [...], "selector": "..."}}`.
- **`reload`** restarts the extension so a changed `background.js` is picked up without a
  trip to `chrome://extensions`. A version that predates `reload` still needs that trip
  once.
- **A page in a tab behind another window counts as hidden.** Chrome won't start audio
  there, and "wait until visible" can time out. Automations that need sound or a visible
  page bring Chrome to the front first (the YT Music automation does).

## How to tell it's being used

- **Canvas activity feed:** every call adds a line such as `Chrome extension: click [7] (0.4s)`.
  This covers models, automations, Claude Code and Antigravity alike.
- **Toolbar icon:** shows a `•` while Nova is acting in the browser.
- **`python main.py web status`:** the number of calls since Nova started, and the last one.
- **Antigravity:** it has its own built-in browser and no flag to turn it off. While the extension is connected,
  every Antigravity turn starts with a one-line reminder to use `main.py web`. If it uses
  its own browser anyway (`open_browser_url`, `browser_subagent`, ...), the feed shows a warning:
  `Antigravity used its own browser (...) instead of the Nova extension`.

## Safety

- The extension never opens a port. It connects outward to `127.0.0.1:<web_port>/ext` with the
  same token as the canvas. A wrong token is refused (close code 1008) and the extension doesn't retry with it.
- Password, card and one-time-code fields are never read back. The map shows `•••`.
- When a **model** asks to click something that looks like paying, sending, submitting,
  deleting or posting, the click is refused until the model calls again with `confirmed=true`.
  The tool description says to do that only after the user has agreed. Automations and the CLI are not gated,
  because the user asked for them directly or Claude Code already asks first (AGENTS.md section 6).
- `--trusted` uses `chrome.debugger` for one gesture. Chrome shows its "started debugging" bar
  for that moment.

## Settings

`config.toml`, `[browser]`:

```toml
driver = "auto"   # auto: the extension when connected, else Playwright | extension | playwright
```

## Limits

- Only the top frame. Forms inside iframes, such as Stripe card fields, aren't reached.
- Synthetic key presses don't trigger browser default actions (Tab doesn't move focus).
  Use `press --trusted` when that matters.
- `chrome://` pages and the Chrome Web Store can't be scripted by any extension.

## Code

| Piece | Where |
|---|---|
| Page agent: map, diffs, fill, click | `extension/content.js` |
| Connection, tabs, act → wait → diff | `extension/background.js` |
| Bridge, `ExtensionBrowser`, driver choice | `assistant/automation/extension.py` |
| `/ext` socket, `/api/ext` | `assistant/ui/server.py` |
| CLI | `web_main` in `assistant/automation/__main__.py` |
| Tests | `tests/test_extension.py` |
