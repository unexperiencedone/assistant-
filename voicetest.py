"""Say something and check it came out. A diagnostic for Nova's voice.

    python voicetest.py                          # a default line, spoken
    python voicetest.py "hello there"            # your own words
    python voicetest.py --silent "hello there"   # synthesise only, play nothing
    python voicetest.py --engine sapi "hello"    # force the local voice
    python voicetest.py --warm                   # cache the lines Nova repeats most

Every stage reports pass or fail and the whole thing exits non-zero if the voice did not
work, so it can sit in a check without anyone reading the output.

It exists because the voice is the one part of this assistant that cannot be verified by
a unit test: the tests prove the splitting and the tag stripping, and none of them can
tell you whether a sound came out of the speakers. This does the round trip -- settings,
key, synthesis, cache, playback, fallback -- and says which stage broke.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The lines Nova says most often, from controller.ACKS and the common confirmations.
# Cached once, they are instant and cost nothing from the free allowance afterwards.
COMMON = [
    "On it.", "Working on it.", "Okay, give me a moment.", "Sure, one sec.",
    "Done.", "Torch is on.", "Torch is off.", "Plan cleared.", "Cancelled.",
    "I couldn't finish that.", "Nothing is waiting to go out.", "On screen.",
]
DEFAULT_LINE = ("[chuckle] Right, that's the layout done. [long pause] "
                "Now I'm ideating the page structure.")

OK, BAD = "  ok  ", " FAIL "


def stage(passed: bool, label: str, detail: str = "") -> bool:
    print(f"[{OK if passed else BAD}] {label}" + (f" -- {detail}" if detail else ""))
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that Nova's voice works.")
    parser.add_argument("text", nargs="*", help="what to say (default: a sample line)")
    parser.add_argument("--silent", action="store_true", help="synthesise but do not play")
    parser.add_argument("--engine", default="", help="override the configured engine")
    parser.add_argument("--warm", action="store_true", help="cache the lines Nova repeats most")
    args = parser.parse_args(argv)

    from assistant.config import load_settings

    settings = load_settings()          # also loads .env, which is where the key lives
    voice = settings.voice
    if args.engine:
        voice.engine = args.engine
    line = " ".join(args.text) or DEFAULT_LINE

    print(f"engine   : {voice.engine}")
    print(f"fallback : the local SAPI voice ({voice.voice_contains or 'default'})")
    print()

    if voice.engine != "fish":
        return _local_only(voice, line, args.silent)

    from assistant.audio import fish

    ok = True
    ok &= stage(bool(fish.FishSpeaker.api_key.fget(_stub(voice))),
                f"key found in {voice.fish_key_env}",
                "put it in .env next to config.toml" if not _key(voice) else "")
    if not _key(voice):
        print("\nWithout a key Nova still speaks, using the local voice. "
              "Run with --engine sapi to check that path.")
        return 1

    pieces = fish.sentences(line)
    stage(bool(pieces), f"split into {len(pieces)} piece(s) so sound starts early",
          " | ".join(p[:40] for p in pieces))

    if args.warm:
        return _warm(voice, fish)

    cache = _cache_dir(voice)
    before = len(list(cache.glob("*.wav"))) if cache else 0

    timings, failures = [], []
    for piece in pieces:
        started = time.time()
        got = _synthesise(fish, voice, piece)
        elapsed = time.time() - started
        if got is None:
            failures.append(piece)
            continue
        timings.append((elapsed, fish.duration_of(got), got))

    ok &= stage(not failures, f"synthesised {len(timings)} of {len(pieces)}",
                "; ".join(f[:40] for f in failures))
    for n, (fetch_s, plays_s, path) in enumerate(timings, 1):
        hit = "cached" if fetch_s < 0.15 else f"{fetch_s:.2f}s"
        print(f"          piece {n}: {plays_s:.2f}s of audio, fetched in {hit}")
        # A duration read straight from the header would be nonsense here: Fish sends a
        # streaming WAV that declares itself 48,695 seconds long.
        ok &= stage(0.2 <= plays_s <= fish.MAX_SECONDS, f"piece {n} duration is sane",
                    f"{plays_s:.1f}s")

    if timings and not args.silent:
        total = sum(p for _f, p, _path in timings)
        print(f"\nplaying {total:.1f}s -- you should hear the neural voice, not Zira")
        spoken = _speak(voice, line)
        ok &= stage(spoken is not False, "played through the speaker",
                    "fell back to the local voice" if spoken is False else "")

    after = len(list(cache.glob("*.wav"))) if cache else 0
    if cache:
        stage(True, f"cache at {cache}", f"{after} files ({after - before} new)")

    print()
    print("ALL GOOD" if ok else "SOMETHING FAILED -- see the FAIL lines above")
    return 0 if ok else 1


# -- helpers ----------------------------------------------------------------------------
def _stub(voice):
    holder = type("S", (), {})()
    holder.settings = voice
    return holder


def _key(voice) -> str:
    import os

    return os.environ.get(voice.fish_key_env or "", "").strip()


def _cache_dir(voice) -> Path | None:
    import os

    folder = getattr(voice, "fish_cache", "") or ""
    if not folder:
        return None
    path = Path(os.path.expandvars(folder)).expanduser()
    if not path.is_absolute():
        path = HERE / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _synthesise(fish, voice, piece: str):
    """One sentence to a WAV on disk, through the real code path."""
    speaker = fish.FishSpeaker.__new__(fish.FishSpeaker)
    speaker.settings = voice
    speaker._warned = False
    speaker._cache_dir = _cache_dir(voice)
    speaker.bus = type("B", (), {"log": staticmethod(lambda *a, **k: None)})()
    return speaker._audio_for(piece)


def _speak(voice, line: str):
    """Through the actual Speaker, queue and worker thread included."""
    from assistant.audio.fish import FishSpeaker
    from assistant.events import EventBus

    bus = EventBus()
    problems: list[str] = []
    bus.subscribe("log", lambda event: problems.append(str(event.data.get("text", ""))))
    speaker = FishSpeaker(voice, bus)
    time.sleep(1.2)                       # the worker opens COM on its own thread
    speaker.say(line)
    speaker.wait_idle(120)
    time.sleep(0.4)
    fell_back = getattr(speaker, "_warned", False)
    speaker.stop()
    for problem in problems:
        print(f"          note: {problem}")
    return False if fell_back else True


def _warm(voice, fish) -> int:
    print(f"caching {len(COMMON)} lines Nova repeats constantly...")
    done = 0
    for text in COMMON:
        if _synthesise(fish, voice, text) is not None:
            done += 1
        else:
            print(f"  failed: {text!r}")
    stage(done == len(COMMON), f"cached {done} of {len(COMMON)}")
    return 0 if done == len(COMMON) else 1


def _local_only(voice, line: str, silent: bool) -> int:
    """Check the local voice on its own, which is also the fallback path."""
    from assistant.audio.fish import strip_tags
    from assistant.audio.tts import make_speaker
    from assistant.events import EventBus

    spoken = strip_tags(line)
    stage(bool(spoken), "tags stripped for the local voice", spoken[:60])
    if silent:
        return 0
    speaker = make_speaker(voice, EventBus())
    time.sleep(1.0)
    speaker.say(spoken)
    speaker.wait_idle(60)
    return 0 if stage(True, "spoke locally") else 1


if __name__ == "__main__":
    raise SystemExit(main())
