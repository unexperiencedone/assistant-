# Nova's hands on your phone (Termux bridge)

Nova asks your phone to do a short list of things — torch, battery, open an app, buzz,
notify, clipboard, a text message — over Tailscale, with a shared secret.

It is deliberately small. There is **no "run this command" endpoint**: the phone only
understands the named actions in `nova_bridge.py`, so the worst a stolen token can do
is the list below. Sending a text is refused outright unless Nova says a human
confirmed it, and Nova only says that after asking you.

---

## On the phone, once

**1. Install from F-Droid, not the Play Store.** The Play Store builds of Termux are
abandoned and the add-ons do not talk to them.

F-Droid lists the whole Termux family. Two of them matter:

| Package | |
|---|---|
| [Termux](https://f-droid.org/packages/com.termux/) | **required** — the terminal |
| [Termux:API](https://f-droid.org/packages/com.termux.api/) | **required** — what actually provides `termux-torch`, `termux-battery-status`, `termux-sms-send` |
| [Termux:Boot](https://f-droid.org/packages/com.termux.boot/) | only if you want the bridge to start after a reboot |
| Termux:Widget | optional: home-screen buttons for scripts |
| Termux:Float · Styling · Tasker · GUI · X11 | not needed here |
| Forks by anyone other than Termux (e.g. "Termux Monet") | skip |

**Install every one of them from the same source.** Android only lets apps talk to each
other when their signatures match, so an F-Droid Termux with a Play Store Termux:API
fails silently — the commands hang or report a permission error, and nothing says why.

**2. In Termux:**

```bash
pkg update && pkg install python termux-api
termux-setup-storage        # only if you want file actions later
```

**3. Check Termux:API works.** `pkg install termux-api` (above) installs the *commands*;
the Termux:API *app* is what they talk to. You need both, and this proves it — the phone
should buzz:

```bash
termux-vibrate -d 500
```

If it hangs or says "command not found", the Termux:API *app* is missing (the `pkg`
above only installs the commands that talk to it).

**4. Get the bridge onto the phone.** Nova serves it, so the phone fetches it with the
token it already has — no SSH server, no cable, no cloud round trip. In Termux, with
Nova running on the PC:

```bash
TOKEN=<the value in C:\Assisstant\data\nova_token>
curl -H "X-Nova-Token: $TOKEN" http://100.113.189.29:8765/api/phone/bridge -o nova_bridge.py
```

Use your own PC address from `tailscale ip -4`. The route sits behind the same token as
everything else, so serving it exposes nothing.

Why `scp` fails with "connection refused": **Termux runs no SSH server by default**, and
once you install one it listens on **port 8022**, not 22:

```bash
pkg install openssh && passwd && sshd
# then from the PC:  scp -P 8022 C:\Assisstant\phone\nova_bridge.py <phone-ip>:~/
```

**5. Run it:**

```bash
python nova_bridge.py
```

It binds to the phone's own `100.x.x.x` Tailscale address — never `0.0.0.0` — and
refuses to start on anything else, so no other network can see it. It prints the token
on first run; get it again any time with:

```bash
python nova_bridge.py --print-token
```

**6. Keep it running.** Termux will eventually sleep the session, so:

```bash
termux-wake-lock             # stops Android killing it
```

To start it on boot, install Termux:Boot from F-Droid and put this in
`~/.termux/boot/nova-bridge.sh`:

```bash
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
python ~/nova_bridge.py >> ~/nova-bridge.log 2>&1
```

---

## On the PC, once

Put the phone's token in `.env` next to `config.toml` (never in `config.toml` — that
file is committed):

```
NOVA_PHONE_TOKEN=<the token the bridge printed>
```

Then in `config.toml`:

```toml
[phone]
enabled = true
host = "100.88.150.39"   # the phone, from `tailscale status`
```

Restart Nova and ask it: **"is my phone there"**.

### Two SIMs

Texts go out on the slot named by `sim_slot` in `[phone]` (`0` is SIM 1, `1` is SIM 2);
Nova puts it on the wire, and `nova_bridge.py` passes it to `termux-sms-send -s`. If you
change the slot, re-copy `nova_bridge.py` to the phone and restart the listener.

Calls cannot be aimed the same way: `termux-telephony-call` dials on whatever Android has
set as the **default calling SIM**. To make calls use SIM 1, set it in Android's *Settings
-> Network & internet -> SIM cards -> Calls -> SIM 1* (the wording varies by phone).

---

## What you can say

| You say | What happens |
|---|---|
| "turn on the torch" / "torch off" | `termux-torch` |
| "what's my phone battery" | `termux-battery-status` |
| "find my phone" | buzzes it for a second |
| "open spotify on my phone" | opens the app by its own link |
| "notify my phone saying dinner is ready" | a notification |
| "is my phone there" | health check over Tailscale |
| "text +91 98765 43210 saying running late" | **asks you first**, then sends |
| "call +91 98765 43210" | **asks you first**, then dials |
| "take a picture with my phone" | `termux-camera-photo`; the JPEG comes back in the reply and lands in the capture inbox on the PC |

The confirmation appears wherever you asked from — say it on the phone and the phone
asks; say it at the desk and the desk asks. Both are refused by the phone itself unless
the request says you confirmed, so neither can fire by accident.

Numbers only, for now: "call mum" needs the contact lookup, which belongs with the
phone-sensing work and its Profile toggle rather than here.

**Android permissions.** Sending and dialling need Termux:API to hold them, and Android
only asks the first time:

- Settings → Apps → **Termux:API** → Permissions → **SMS** and **Phone** → Allow
- the same under **Termux** if your Android version asks there too

Until they are granted, the bridge returns the permission error from `termux-sms-send`
or `termux-telephony-call` and Nova reads it back to you.

Teaching it a new app is one line in `APP_LINKS` in `nova_bridge.py`; an unknown app
name is refused rather than guessed at.

**The camera.** `camera_photo` is newer than the rest of this file, so if the bridge on
your phone predates it, copy `nova_bridge.py` over again and restart the listener. It is
a sensor, not an action: it is off until you switch **Camera** on in the Phone section of
your profile, and Nova says it took a picture every single time. The photo is written to
a temp file on the phone, read back as base64 inside the reply, and deleted there — a
picture Nova took should not quietly accumulate on your phone. Anything over 8 MB is
refused rather than sent.

---

## If something is wrong

| What Nova says | What it means |
|---|---|
| "My phone bridge isn't set up yet" | `[phone] enabled`, `host`, or `NOVA_PHONE_TOKEN` is missing |
| "I can't reach your phone" | Tailscale is off, or the bridge is not running |
| "Your phone rejected the token" | `.env` and `~/.nova_bridge_token` disagree |
| "…is not installed. Is Termux:API set up?" | the Termux:API **app** is missing |

Rotating the phone's token: delete `~/.nova_bridge_token`, restart the bridge, copy the
new value into `.env`.
