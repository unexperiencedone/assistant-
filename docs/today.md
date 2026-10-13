# Today, the schedule, and Nova speaking first

Three things Nova now does without being asked, and one thing it now always knows.

| What | Where | Costs |
|---|---|---|
| Knows the date, weather, markets, headlines, holidays and your plans on every turn | `assistant/today/` | One ~60-token line per agent turn; the data is plain HTTP |
| Greets you when it opens, in fresh words each time | `today/greeting.py`, `app._say_greeting` | One free Groq turn (about 2.5 s) |
| Goes through your mail on the day's first greeting | `app._mail_digest` | One Claude turn a day |
| Reminds you 60, 30 and 15 minutes before things | `assistant/schedule/` | Nothing: no model involved |
| Offers in the evening to plan an empty tomorrow | `schedule/service.py` | Nothing until you say yes |

## Where "today" comes from

`today/sources.py`, refreshed every 15 minutes in the background, cached in `data/today.json`:

- **Weather: MSN Weather**, the service behind the Windows Widgets board, so it gives the same
  figures you see there. It has no public API. The key is the public one that MSN's own pages ship
  with, so it could stop working one day; Open-Meteo takes over automatically when it does.
  The Widgets board itself can't be read without opening it on screen, so Nova reads the
  service behind it.
- **Markets: Yahoo Finance** for Nifty, Sensex, Nifty Bank and Nifty Midcap 100, the same four
  as the Widgets markets card. Change is measured against the previous close.
- **News**: the RSS feeds in `ambient/news.py` (NDTV, The Hindu, Google News India), plus
  Google News Business and Economic Times Markets for finance.
- **Holidays**: Google's public Indian holidays calendar.
- **Your calendar**: any iCalendar address you put in `.env`:

  ```
  NOVA_CALENDAR_ICS=https://calendar.google.com/calendar/ical/<you>/private-<key>/basic.ics
  ```

  For Google Calendar: Settings → your calendar → *Integrate calendar* → **Secret address in
  iCal format**. Several addresses can be listed, separated by commas. Treat it like a password:
  it goes in `.env`, never in `config.toml`. Timed events from it get reminders too.

A source that fails keeps its last good value: stale beats empty. The canvas briefing panel
(`data/ambient.json`) is fed from the same data, so it stays current without an agent turn.

## The greeting

On start, and again when you open the window from the tray after `greet_again_hours`, Nova says
hello. A free model writes the greeting from today's facts every time, using a different approach
each time:

- the last five greetings go in, with their closing lines quoted, under "don't reuse these";
- the order of weather, news, markets and plans is shuffled (weather leads 60% of the time);
- one of six angles is drawn at random ("open casually, like a friend who just walked in"...).

If no model answers, `greeting.fallback` assembles one from randomly chosen parts.

**Mail, once a day.** On the day's first greeting Nova says it is going through your mail. It
then hands a read-only Gmail pass to Claude (the only brain with the Gmail connector). That pass
always answers, even if only to say nothing came in. The hourly mail goal is pushed back an hour
so the same mail isn't read twice. Through the rest of the day, that hourly goal speaks only
when something matters (`[[QUIET]]` otherwise).

## The schedule and reminders

Set it up any way you like:

- **By voice**: "set up my schedule for tomorrow", "remind me about the dentist at 5". Groq
  gets the `schedule_add` / `schedule_list` / `schedule_remove` tools and asks for anything
  missing. A time it can't read is a question, never a guess (`schedule/when.py`).
- **Read back instantly**: "what's my schedule tomorrow", "what do I have today" is an
  instant command with no agent turn.
- **From a shell** (and from Claude sessions):
  `python main.py schedule add "Standup" --at "tomorrow 10am"` · `schedule list friday` ·
  `schedule remove 3`.

**Reminders** go out at each threshold in `remind_minutes` (default 60, 30, 15). Unlike standing
goals, they **don't wait their turn**. They fire in the middle of a task, a conversation or
quiet hours, because a reminder that waits is no longer a reminder. Each one is:

- spoken on this PC, even if the last request came from the phone (`controller.announce`);
- shown as a tray notification;
- sent to the phone through the bridge, when it's reachable.

Each threshold is announced once. If the laptop slept through the 60- and 30-minute marks and
wakes 20 minutes out, you get one reminder, not three. The wording varies, and the last one tells
you to wrap up.

**The evening check-in.** From `plan_nudge_hour` (21:00), if tomorrow is empty, Nova asks once
whether you want to plan it. The question ends in "?" so you can answer without the wake word.
"Yes" starts the planning conversation. "No" twice in a row stops it asking for a week. This is
the AfterThought rule: silence is the default, and a nudge you keep turning down is noise.

## The proactive memory

AfterThought, the layer that remembers what you work on and brings it up when it matters
again, has its own document: `docs/afterthought.md`.
