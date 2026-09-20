# Reaching Nova from your phone

The plan, in the order it has to happen. The rule behind the order: **the lock goes on
before the door opens.** Nothing in this file widens Nova's reach until the shared
secret in front of it is already working.

Never put port 8765 on the public internet. Tailscale only — no ngrok, no Cloudflare
Tunnel. The endpoint behind this token can run commands and open apps.

---

## Step 1-2 — the shared secret (done)

Every request the canvas server answers now has to prove itself: the page, every
`/api/...` call, and the WebSocket. There is no loopback exemption — the desktop canvas
is opened with the token already in its URL.

A request proves itself in one of three ways:

| How | Who uses it |
|---|---|
| `X-Nova-Token: <token>` header | the Termux bridge, scripts, anything not a browser |
| `?token=<token>` in the URL | a new device, once |
| `nova_token` cookie | set automatically after a successful `?token=` visit |

Everything is in `assistant/ui/auth.py`; the guard itself is one middleware plus one
check in the WebSocket route in `assistant/ui/server.py`.

### Where the token lives

`data/nova_token`, generated on first start (32 random url-safe bytes). `data/` is
git-ignored, so it is never committed — verify any time with:

```powershell
git check-ignore -v data/nova_token      # prints the .gitignore rule that covers it
```

`NOVA_TOKEN` in the environment (or in `.env`) overrides the file, for a device or
script that should not read it off disk.

### Reading it

```powershell
Get-Content C:\Assisstant\data\nova_token
```

### Pairing a device

Open, once, on that device:

```
http://<nova-host>:8765/?token=<token>
```

The cookie it leaves lasts a year, so the token never has to be typed or stored on the
phone again.

### Rotating it — do this the moment a device is lost

```powershell
Remove-Item C:\Assisstant\data\nova_token     # delete the secret
# restart Nova; a fresh token is generated on start
Get-Content C:\Assisstant\data\nova_token     # the new one
```

Every paired device stops working immediately and has to be paired again with the new
link. That is the point. The desktop canvas re-pairs itself, because Nova opens it with
the current token.

### Turning the guard off

`[ui] require_token = false` in `config.toml`. Only sane while `host` is loopback.

---

## Step 3 — listen beyond this PC, and firewall it (needs your approval)

Two edits that belong together, in this order:

1. `config.toml`:

   ```toml
   [ui]
   host = "0.0.0.0"     # answer on every interface, including Tailscale
   require_token = true # must stay true
   ```

2. Open the firewall for **Private networks only**, in an **elevated** PowerShell:

   ```powershell
   New-NetFirewallRule -DisplayName "Nova canvas (private)" -Direction Inbound `
     -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private
   ```

   Check it, and remove it, with:

   ```powershell
   Get-NetFirewallRule -DisplayName "Nova canvas (private)"
   Remove-NetFirewallRule -DisplayName "Nova canvas (private)"
   ```

Restart Nova, then confirm from the PC itself that the guard is live:

```powershell
# 401 — no token
Invoke-WebRequest http://127.0.0.1:8765/api/state -UseBasicParsing
# 200 — with it
Invoke-WebRequest http://127.0.0.1:8765/api/state -Headers @{ "X-Nova-Token" = (Get-Content .\data\nova_token) } -UseBasicParsing
```

---

## Step 4 — Tailscale

Install on the PC and the phone, sign both into the same tailnet, then note the PC's
`100.x.x.x` address (`tailscale ip -4`). Test from the phone **off home Wi-Fi**:

```
http://100.x.x.x:8765/?token=<token>
```

---

## Step 5 — the canvas as an app on the phone (built)

`canvas/public/manifest.webmanifest`, four icons (plain and maskable, drawn from the
Living Ink palette) and `canvas/public/sw.js`, all shipped by `npm run build` into
`assistant/ui/canvas_dist`. The worker caches the shell — page, hashed bundles, fonts —
and never touches `/api` or `/ws`, because a stale answer about what Nova is doing
would be a lie. Every request still goes through with its credentials, so the pairing
cookie keeps deciding what is allowed.

One detail that would otherwise fail silently: the manifest and its icons go through
the token guard like everything else, and a browser fetches a manifest **without**
cookies unless told otherwise — hence `crossorigin="use-credentials"` on the
`<link rel="manifest">` in `canvas/index.html`.

One thing the original plan missed: **a service worker needs a secure context.** Over
plain `http://100.x.x.x:8765` Android gives you a bookmark, not an installed app — the
registration in `canvas/src/main.jsx` checks `window.isSecureContext` and quietly does
nothing, so the canvas still works, it just is not installable. The fix stays inside
the tailnet — `tailscale serve` puts real HTTPS in front of Nova without exposing
anything publicly:

```powershell
tailscale serve --bg 8765        # https://<machine>.<tailnet>.ts.net -> 127.0.0.1:8765
```

(`tailscale funnel` is the one that publishes to the internet. Do not use it.)

Then on the phone: open `https://<machine>.<tailnet>.ts.net/?token=<token>` once to
pair, and use Chrome's **Install app** (or Safari's **Add to Home Screen**). The cookie
lasts a year, so the app opens straight into the canvas from then on.

---

## Steps 6-11 — origin-aware replies, the Termux bridge, remote-action logging

Not built yet. In order: verify end to end from the phone; Termux + Termux:API from
F-Droid; the phone-side listener bound to the Tailscale interface only (never every
interface); two or three commands wired through; confirmation prompts returning to the
phone that asked; `origin` (`"desktop"` | `"phone"`) threaded through the existing
dispatch and into the activity log; and only then the phone-side sensing set, behind
its own Profile section toggle — the toggle first, so sensing is never on without a way
to turn it off.
