#!/data/data/com.termux/files/usr/bin/bash
# Wake the phone's screen from Termux, without root.
#
# Why this exists: Android will not let a background app start an activity while the
# device is asleep or locked, so `open` and `call_dial` can exit 0 and do nothing. The
# permission toggles (Display over other apps / Display pop-up windows while running in
# background) fix that on most ROMs. On the ones they don't, lighting the screen first
# is the remaining lever.
#
# It cannot be done with plain `input`: injecting a key event needs INJECT_EVENTS, which
# is a system permission. The way round it without root is ADB talking to this same
# phone over wireless debugging -- adb's shell holds permissions Termux's does not.
#
# ONE-TIME SETUP
#   1. pkg install android-tools
#   2. Settings -> Developer options -> Wireless debugging -> On
#   3. "Pair device with pairing code" -- note the pairing port and code, then:
#        adb pair 127.0.0.1:<pairing-port>       # enter the 6-digit code
#      The pairing port is not the connect port; the connect one is on the main
#      Wireless debugging screen and changes on every reboot.
#   4. adb connect 127.0.0.1:<connect-port>
#
# After a reboot you do NOT have to pair again -- the phone remembers your host key.
# What changes is the connect port: adbd picks a new random one each time wireless
# debugging comes up, so this script finds it over mDNS rather than making you read it
# off the screen. The toggle itself does reset on some ROMs, and nothing here can turn
# it back on; that part stays manual.
#
# WHAT IT DOES NOT DO: unlock. It lights the screen and dismisses a swipe-only lock
# screen. If you have a PIN, pattern or password, the keyguard stays up by design --
# that is what it is for. Typing a stored PIN from a script would turn your lock screen
# into a file on disk, readable by anything that can read this repo, in exchange for
# not walking to your phone. Don't. If you want it anyway, it is your device and your
# call, and the command is `input text <pin>` followed by `input keyevent 66` -- added
# by you, on the phone, not kept here.

set -u

if ! command -v adb >/dev/null 2>&1; then
    echo "adb is not installed. Run: pkg install android-tools" >&2
    exit 3
fi

# The port is whatever adbd chose this boot. Ask mDNS; fall back to an argument.
PORT="${1:-}"
if [ -z "$PORT" ]; then
    PORT=$(adb mdns services 2>/dev/null | grep -m1 '_adb-tls-connect' | awk '{print $NF}' | awk -F: '{print $NF}')
fi
if [ -z "$PORT" ]; then
    echo "No wireless debugging port found. Is it switched on? Or pass one: wake_screen.sh <port>" >&2
    exit 2
fi

adb connect "127.0.0.1:$PORT" >/dev/null 2>&1
if ! adb devices | grep -q "127.0.0.1:$PORT.*device"; then
    echo "adb could not connect on port $PORT. Is wireless debugging on, and paired?" >&2
    exit 4
fi

state=$(adb shell dumpsys power | grep -m1 "mWakefulness=" | cut -d= -f2 | tr -d '\r')
if [ "$state" != "Awake" ]; then
    adb shell input keyevent KEYCODE_WAKEUP
    sleep 1
fi
adb shell input swipe 540 1600 540 600    # dismisses a swipe-only lock screen; a PIN stays up

# Say what is actually true now, so the caller doesn't assume more than happened.
locked=$(adb shell dumpsys window 2>/dev/null | grep -m1 -o "mDreamingLockscreen=[a-z]*" | cut -d= -f2 | tr -d '\r')
echo "screen: awake, lockscreen: ${locked:-unknown}"
