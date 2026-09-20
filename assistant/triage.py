"""How heavy is a spoken request?

Two quick errands can happily run at once: open a file, search for something, play a
song, send a mail. Two big jobs can't — they fight over the same windows, the same
files and the same attention, and the user can only follow one of them anyway. So a
request is sorted into `LIGHT` or `HEAVY` before it is started, and the runner allows
only one heavy task at a time.

When in doubt this says HEAVY: starting a big job alongside another is the mistake
that costs something, while queueing an errand only costs a few seconds.
"""

import re

LIGHT = "light"
HEAVY = "heavy"

# Errands: one action, one target, no thinking. These win over everything below.
_LIGHT_STARTS = re.compile(r"""^(?:
      open | launch | start(?!\s+(?:working|building|writing)) | run | play | resume | pause | stop\s+the\s+music
    | skip | next\s+(?:song|track|video) | previous | mute | unmute | volume
    | find | search | look\s+up | locate | where(?:'s|\s+is| are) | which
    | show | display | reveal | list | open\s+up
    | send | mail | email | reply\s+to | text | message | call
    | check | what(?:'s|\s+is|\s+are) | who(?:'s|\s+is) | when(?:'s|\s+is) | how\s+(?:many|much|long|old)
    | tell\s+me | remind | note | copy | paste | screenshot | close | minimi[sz]e | switch\s+to | focus
    | go\s+to | navigate | scroll | click | type | press
  )\b""", re.I | re.X)

# Projects: they write, change or reorganise things, or need real reasoning.
_HEAVY_WORDS = re.compile(r"""\b(?:
      write | draft | compose | author | rewrite | edit
    | build | create | make | generate | implement | add\s+a\s+(?:feature|test|page) | code | script | program
    | refactor | fix | debug | repair | migrate | upgrade | install | set\s+up | configure | deploy
    | clean\s+up | tidy | organi[sz]e | sort\s+(?:out|through) | rename\s+all | move\s+all | delete\s+all
    | batch | every\s+file | all\s+the\s+files | duplicates?
    | research | analy[sz]e | summari[sz]e | compare | review | audit | plan | design | figure\s+out | work\s+out
    | go\s+through | walk\s+through | step\s+by\s+step | test\s+the | run\s+the\s+tests
  )\b""", re.I | re.X)

_LONG_ENOUGH_TO_BE_A_PROJECT = 18   # words
_CLAUSES = re.compile(r"\b(?:and\s+then|after\s+that|then\s+(?:also\s+)?(?:open|write|make|create|send))\b", re.I)


def weight(text: str, executing_plan: bool = False) -> str:
    """`LIGHT` for an errand that can run alongside anything, `HEAVY` for a real job."""
    if executing_plan:          # a plan is several steps by definition
        return HEAVY
    words = text.strip()
    if not words:
        return HEAVY
    if _HEAVY_WORDS.search(words):
        return HEAVY
    if _CLAUSES.search(words):  # "open X and then write Y" is really two requests
        return HEAVY
    if _LIGHT_STARTS.match(words):
        return LIGHT
    if len(words.split()) >= _LONG_ENOUGH_TO_BE_A_PROJECT:
        return HEAVY
    # Short, no project verb, no errand verb: a question or a nudge. Cheap enough.
    return LIGHT if len(words.split()) <= 8 else HEAVY


def is_light(text: str, executing_plan: bool = False) -> bool:
    return weight(text, executing_plan) == LIGHT
