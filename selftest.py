"""Nova's self-test. One script, run it, see what works.

    python selftest.py                  # every check
    python selftest.py --offline        # skip anything that needs the network
    python selftest.py config intents   # only these groups
    python selftest.py --list           # what the groups are

Three things it also does, because they need a person in the loop and no automated
check can stand in for them:

    python selftest.py --say "hello"    # speak it, so you can hear which voice came out
    python selftest.py --mic            # say a line yourself; see what Nova heard
    python selftest.py --mic --expect "open spotify"
    python selftest.py --compare        # whisper models and beam sizes, side by side
    python selftest.py --warm           # cache the lines Nova repeats, so they are instant

Independent of the running app: it builds what it needs, checks it, and exits non-zero
if anything failed, so it can sit in a check nobody reads.

This is not a replacement for `python -m unittest discover -s tests`, which proves the
logic. It answers the different question the unit tests cannot: whether this machine,
this config and these keys actually work together right now -- whether the Groq key is
live, whether Chrome will drive, whether a sound comes out of the speakers. Those fail
for reasons that have nothing to do with the code, which is exactly why they need
checking separately and by hand.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

PASS, FAIL, SKIP = "  ok  ", " FAIL ", " skip "
_results: list[tuple[str, bool]] = []


def check(label: str, passed: bool | None, detail: str = "") -> bool:
    """Record and print one result. `None` means skipped, which is not a failure."""
    mark = SKIP if passed is None else (PASS if passed else FAIL)
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    if passed is not None:
        _results.append((label, passed))
    return bool(passed)


def group(name: str):
    print(f"\n{name}")
    print("-" * len(name))


# -- the groups -------------------------------------------------------------------------
def check_config(offline: bool) -> None:
    group("config")
    from assistant.config import load_settings

    settings = load_settings()
    check("config.toml loads", True, f"agent default = {settings.agents.default}")
    import os

    for env, what in (("GROQ_API_KEY", "the fast tier"),
                      ("OPENROUTER_API_KEY", "the fallback chat model"),
                      (settings.voice.fish_key_env, "the neural voice"),
                      ("GOOGLE_PLACES_API_KEY", "prospect discovery"),
                      ("NOVA_PHONE_TOKEN", "the phone bridge")):
        present = bool(os.environ.get(env, "").strip())
        # A missing key disables one capability; it is not a broken install.
        check(f"{env}", None if not present else True,
              f"{what} is off without it" if not present else what)
    for folder in ("data", settings.automations.folder):
        check(f"{folder}/ exists", (HERE / folder).is_dir())


def check_intents(offline: bool) -> None:
    group("routing (the cases that have regressed before)")
    from assistant.intents import match_intent

    cases = [
        ("okay do one thing clear all the plans that were made", "clear_plan"),
        ("drop that plan", "clear_plan"),
        ("list the plans", "read_plan"),
        ("drop that", "publish_discard"),
        ("brief me on recent news", "news_briefing"),
        ("show me that", "show_reader"),
        ("open spotify", "open_item"),
    ]
    for said, expected in cases:
        found = match_intent(said)
        got = found.name if found else "(none)"
        check(f'"{said[:44]}"', got == expected, f"{got}" if got != expected else got)
    for said in ("this is not news this is more of an article", "you havent told me proper news"):
        check(f'"{said[:44]}" matches nothing', match_intent(said) is None)


def check_tools(offline: bool) -> None:
    group("tools")
    import json

    from assistant.agents.tools import CORE, TOOLS, ToolContext, relevant, schemas
    from assistant.planning import Plan

    ctx = ToolContext(plan=Plan(), browser=True, publisher=object(), runner=object(),
                      delegate=lambda _t: "", delegate_agy=lambda _t: "",
                      capture=object(), goals=object(), local_system=object(),
                      automations=object())
    check("every core tool exists", CORE <= set(TOOLS), ", ".join(sorted(CORE - set(TOOLS))) or "all present")
    everything = len(json.dumps(schemas(ctx))) // 4
    narrow = len(json.dumps(relevant(ctx, "what time is it"))) // 4
    check("per-request selection cuts the schema cost", narrow < everything // 2,
          f"{narrow} tokens for a simple request, {everything} if everything were sent")
    check("a plan request brings the plan tools",
          "clear_plan" in {s["function"]["name"] for s in relevant(ctx, "clear the plans")})
    check("nothing is offered that is not usable",
          {s["function"]["name"] for s in relevant(ctx, "anything")} <= set(TOOLS))


def check_web(offline: bool) -> None:
    group("web reach")
    if offline:
        check("search", None, "skipped with --offline")
        return
    from assistant.agents import web

    rows = web.search("python release notes", count=3)
    check("keyless search returns results", bool(rows), f"{len(rows)} hits")
    if rows:
        check("results carry a usable link", rows[0]["url"].startswith("http"), rows[0]["url"][:50])
    title, text = web.read("https://example.com")
    check("a page reads back as text", bool(text) and "won't fetch" not in text,
          f"{title or 'no title'} / {len(text)} chars")
    check("loopback is refused", bool(web.allowed("http://127.0.0.1:8765")),
          "a fetched page must not be able to reach this machine")


def check_agents(offline: bool) -> None:
    group("brains")
    from assistant.agents.registry import AgentRegistry
    from assistant.config import load_settings

    registry = AgentRegistry(load_settings())
    for name, backend in registry.backends.items():
        available = backend.is_available()
        # Not every brain has to be installed; the default one does.
        check(f"{name}", True if available else None,
              backend.label if available else backend.unavailable_reason()[:60])
    check("the configured default is available", registry.current.is_available(),
          registry.current_name)


def check_voice(offline: bool) -> None:
    group("voice")
    from assistant.audio.fish import duration_of, sentences, strip_tags
    from assistant.config import load_settings

    voice = load_settings().voice
    line = "[chuckle] Right, the layout is done. [long pause] Now the structure."
    check("a reply splits so sound starts early", len(sentences(line)) == 2)
    check("delivery tags never reach the local voice", "[" not in strip_tags(line))
    if voice.engine != "fish":
        check("neural voice", None, f'engine is "{voice.engine}" -- set it to "fish" in config.toml')
        return
    if offline:
        check("neural synthesis", None, "skipped with --offline")
        return
    import os

    if not os.environ.get(voice.fish_key_env, "").strip():
        check("neural synthesis", None, f"no {voice.fish_key_env} in .env")
        return
    from assistant.audio import fish
    from assistant.events import EventBus

    speaker = fish.FishSpeaker.__new__(fish.FishSpeaker)
    speaker.settings, speaker._warned, speaker.bus = voice, False, EventBus()
    speaker._cache_dir = HERE / "data" / "tts-cache"
    speaker._cache_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    path = speaker._audio_for("The self test is running.")
    took = time.time() - started
    if check("synthesises a sentence", path is not None, f"{took:.2f}s"):
        plays = duration_of(path)
        # A streaming WAV header declares nonsense; believing it froze the speaker.
        check("its duration is sane", 0.2 <= plays <= fish.MAX_SECONDS, f"{plays:.1f}s of audio")


def check_hearing(offline: bool) -> None:
    group("hearing")
    fixture = HERE / "data" / "hearing-fixtures" / "phrase0.wav"
    if not fixture.exists():
        check("transcriber", None, "no fixture yet -- run hearingtest.py once to make them")
        return
    import speech_recognition as sr

    from assistant.audio.gate import rejection
    from assistant.audio.transcriber import WhisperTranscriber
    from assistant.config import load_settings
    from assistant.events import EventBus

    settings = load_settings()
    with sr.AudioFile(str(fixture)) as source:
        audio = sr.Recognizer().record(source)
    started = time.time()
    transcript = WhisperTranscriber(settings.speech, EventBus(), "Nova Groq Claude").transcribe(audio)
    took = time.time() - started
    check("transcribes a known clip", "brief" in transcript.text.lower(),
          f'heard "{transcript.text[:40]}" in {took:.2f}s')
    check("the noise gate keeps it", rejection(transcript, settings.speech.min_avg_logprob) is None,
          f"confidence {transcript.avg_logprob:+.2f} vs threshold {settings.speech.min_avg_logprob}")


def check_orchestrator(offline: bool) -> None:
    group("orchestrator")
    from assistant.orchestrate import Orchestrator

    started: list[str] = []
    orch = Orchestrator(lambda prompt, _label: started.append(prompt) or f"t{len(started)}")
    orch.start("three parts", [("first", []), ("second", []), ("write up", [1, 2])])
    check("independent steps start together", started == ["first", "second"], ", ".join(started))
    check("a dependent step waits", "write up" not in started)
    check("a finished step says nothing on its own", orch.finished("t1", True, "a") == "")
    orch.finished("t2", True, "b")
    check("the blocked step starts once its blockers finish", "write up" in started)
    answer = orch.finished("t3", True, "c")
    check("the group answers once, at the end", "a" in answer and "c" in answer, answer.replace("\n", " / "))


def check_skills(offline: bool) -> None:
    group("skills")
    from assistant.agents import skills as registry
    from assistant.config import load_settings

    settings = load_settings()
    roots = [Path(p) if Path(p).is_absolute() else HERE / p for p in settings.skills.paths]
    found = registry.discover(roots)
    check("skills discovered", bool(found), f"{len(found)} from {', '.join(settings.skills.paths)}")
    if found:
        names = registry.names(found)
        check("the index stays cheap", len(names) < 900, f"{len(names)} chars in every prompt")
        check("a request is matched to a skill locally",
              bool(registry.suggest(found, "what is the bouncy popover effect called")),
              registry.suggest(found, "what is the bouncy popover effect called") or "no match")


def check_gate(offline: bool) -> None:
    group("the approval gate")
    import tempfile

    from assistant.agents.tools import ToolContext, call
    from assistant.publish import PublishService

    service = PublishService(Path(tempfile.mkdtemp()) / "p.db")
    ctx = ToolContext(publisher=service, assistant_name="Nova",
                      profile=type("P", (), {"preferred_name": "Aakshant"})())
    bad = ("I am Nova and I will build your site free for the first month, "
           "within 3 days, and I guarantee results.")
    refused = call(ctx, "draft_outreach", {"recipient": "a@b.in", "subject": "Hi", "body": bad})
    check("a draft that oversells is refused", "can't go out" in refused,
          "signed as Nova, free offer, deadline, guarantee")
    check("and is not stored for someone to catch later", service.gate.pending() == [])
    good = "Hi, I'm Aakshant. I build websites for small businesses and yours could use a refresh."
    call(ctx, "draft_outreach", {"recipient": "a@b.in", "subject": "Hi", "body": good})
    check("a clean draft is staged", len(service.gate.pending()) == 1)
    check("approving it sends nothing", "can't send email" in service.approve())
    service.close()


# -- the actions that need a person ----------------------------------------------------
# Deliberately not part of the pass/fail run: one wants your ears, one wants your voice,
# and one takes a minute and downloads models.
HARD_LINES = [
    "On it.", "Working on it.", "Okay, give me a moment.", "Sure, one sec.",
    "Done.", "Torch is on.", "Torch is off.", "Plan cleared.", "Cancelled.",
    "I couldn't finish that.", "Nothing is waiting to go out.", "On screen.",
]
PHRASES = [
    "Nova, brief me on recent news.",
    "Open Aakshant Kumar's resume.",
    "Use Groq instead of Claude for this one.",
    "Find local businesses in Lucknow for my website gig.",
    "Clear all the plans that were made.",
    "Every morning tell me what changed in my repos.",
]
HOTWORDS = "Nova Groq Claude Antigravity Aakshant Lucknow repos Spotify"


def _words(text: str) -> list[str]:
    keep = "".join(c.lower() if (c.isalnum() or c.isspace()) else " " for c in text or "")
    return keep.split()


def wer(reference: str, heard: str) -> float:
    """Word error rate: edit distance over words, over the reference length."""
    a, b = _words(reference), _words(heard)
    if not a:
        return 0.0 if not b else 1.0
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1] / len(a)


def fixture_wav(text: str, index: int) -> Path:
    """One phrase as a WAV, spoken by the local voice and kept between runs."""
    folder = HERE / "data" / "hearing-fixtures"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"phrase{index}.wav"
    if path.exists():
        return path
    import comtypes.client

    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    stream.Open(str(path), 3)
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Speak(text, 0)
    stream.Close()
    return path


def action_say(text: str) -> int:
    """Speak a line through the configured engine, so you can hear which one answered."""
    group("say it out loud")
    from assistant.audio.tts import make_speaker
    from assistant.config import load_settings
    from assistant.events import EventBus

    settings = load_settings()
    bus = EventBus()
    notes: list[str] = []
    bus.subscribe("log", lambda event: notes.append(str(event.data.get("text", ""))))
    speaker = make_speaker(settings.voice, bus)
    time.sleep(1.2)                      # the worker opens its voice on its own thread
    print(f'  engine "{settings.voice.engine}", saying: {text}')
    speaker.say(text)
    speaker.wait_idle(120)
    time.sleep(0.4)
    fell_back = getattr(speaker, "_warned", False)
    speaker.stop()
    for note in notes:
        print(f"         note: {note}")
    return 0 if check("spoke", True, "the local voice answered" if fell_back
                      else "the configured voice answered") else 1


def action_mic(expected: str, seconds: float) -> int:
    """Record from the real microphone: your voice, your room, which is the real test."""
    group("what Nova heard you say")
    import speech_recognition as sr

    from assistant.audio.gate import rejection
    from assistant.audio.transcriber import WhisperTranscriber
    from assistant.config import load_settings
    from assistant.events import EventBus

    settings = load_settings()
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = settings.speech.energy_threshold
    recognizer.pause_threshold = settings.speech.pause_threshold
    print(f"  say something{(' -- expecting: ' + expected) if expected else ''}")
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.6)
            print(f"  ambient noise set the threshold to {recognizer.energy_threshold:.0f}")
            print(f"  listening for up to {seconds:.0f}s...")
            audio = recognizer.listen(source, timeout=seconds,
                                      phrase_time_limit=settings.speech.phrase_time_limit)
    except Exception as error:
        check("recorded from the microphone", False, f"{type(error).__name__}: {error}")
        return 1
    transcript = WhisperTranscriber(settings.speech, EventBus(), HOTWORDS).transcribe(audio)
    print(f'  heard: "{transcript.text or "(nothing)"}"')
    dropped = rejection(transcript, settings.speech.min_avg_logprob)
    check("something was transcribed", bool(transcript.text.strip()))
    # The useful number: how close your real speech came to being thrown away.
    check("the noise gate kept it", dropped is None,
          dropped or f"confidence {transcript.avg_logprob:+.2f} vs threshold {settings.speech.min_avg_logprob}")
    if expected:
        score = wer(expected, transcript.text)
        check(f"word error rate {score:.0%}", score < 0.2)
    else:
        print("  pass --expect \"what you said\" to score it")
    return 0 if all(ok for _l, ok in _results) else 1


def action_compare() -> int:
    """Whisper models and beam sizes on the same clips, so a change can be justified."""
    group("whisper models compared")
    from faster_whisper import WhisperModel

    from assistant.config import load_settings

    speech = load_settings().speech
    clips = [(phrase, fixture_wav(phrase, n)) for n, phrase in enumerate(PHRASES)]
    configs = [(speech.whisper_model, 1), (speech.whisper_model, 5),
               ("small.en", 5), ("distil-small.en", 5)]
    print(f"  {len(clips)} clips. A model that is not cached will be downloaded.\n")
    print(f"  {'config':28} {'WER':>7} {'per clip':>10}")
    for name, beam in configs:
        try:
            model = WhisperModel(name, device="cpu", compute_type=speech.whisper_compute_type)
        except Exception as error:
            print(f"  {name + ' beam=' + str(beam):28} unavailable ({type(error).__name__})")
            continue
        total, seconds = 0.0, 0.0
        for phrase, path in clips:
            started = time.time()
            segments, _info = model.transcribe(str(path), language="en", beam_size=beam,
                                               vad_filter=True, condition_on_previous_text=False,
                                               hotwords=HOTWORDS)
            heard = " ".join(s.text for s in segments).strip()
            seconds += time.time() - started
            total += wer(phrase, heard)
        print(f"  {name + ' beam=' + str(beam):28} {total / len(clips):6.1%} {seconds / len(clips):9.2f}s")
    print("\n  Slower is only worth it if the WER column actually moves.")
    return 0


def action_warm() -> int:
    """Synthesise the lines Nova repeats, so the first time you hear them is instant."""
    group("warming the voice cache")
    from assistant.audio import fish
    from assistant.config import load_settings
    from assistant.events import EventBus

    voice = load_settings().voice
    speaker = fish.FishSpeaker.__new__(fish.FishSpeaker)
    speaker.settings, speaker._warned, speaker.bus = voice, False, EventBus()
    speaker._cache_dir = HERE / "data" / "tts-cache"
    speaker._cache_dir.mkdir(parents=True, exist_ok=True)
    done = sum(1 for line in HARD_LINES if speaker._audio_for(line) is not None)
    return 0 if check(f"cached {done} of {len(HARD_LINES)} repeated lines",
                      done == len(HARD_LINES)) else 1


GROUPS = {
    "config": check_config, "intents": check_intents, "tools": check_tools,
    "web": check_web, "agents": check_agents, "voice": check_voice,
    "hearing": check_hearing, "orchestrator": check_orchestrator,
    "skills": check_skills, "gate": check_gate,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that Nova works on this machine.")
    parser.add_argument("groups", nargs="*", help=f"any of: {' '.join(GROUPS)}")
    parser.add_argument("--offline", action="store_true", help="skip anything needing the network")
    parser.add_argument("--list", action="store_true", help="list the groups and exit")
    parser.add_argument("--say", default="", metavar="TEXT", help="speak a line and listen to it")
    parser.add_argument("--mic", action="store_true", help="record yourself and see what Nova heard")
    parser.add_argument("--expect", default="", help="what you said, for scoring --mic")
    parser.add_argument("--seconds", type=float, default=6.0, help="how long --mic listens")
    parser.add_argument("--compare", action="store_true", help="whisper models side by side")
    parser.add_argument("--warm", action="store_true", help="cache the lines Nova repeats")
    args = parser.parse_args(argv)

    # The actions that need a person run on their own and nothing else runs with them.
    if args.say:
        return action_say(args.say)
    if args.mic:
        return action_mic(args.expect, args.seconds)
    if args.compare:
        return action_compare()
    if args.warm:
        return action_warm()

    if args.list:
        for name, function in GROUPS.items():
            print(f"  {name:14} {(function.__doc__ or '').strip().splitlines()[0] if function.__doc__ else ''}")
        return 0

    wanted = args.groups or list(GROUPS)
    unknown = [name for name in wanted if name not in GROUPS]
    if unknown:
        print(f"no such group: {', '.join(unknown)}\ntry: {' '.join(GROUPS)}")
        return 2

    started = time.time()
    for name in wanted:
        try:
            GROUPS[name](args.offline)
        except Exception as error:
            # A group that blows up is one failure, not the end of the run: the point is
            # to find out everything that is wrong in one pass.
            check(f"{name} group crashed", False, f"{type(error).__name__}: {error}")

    failed = [label for label, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)} of {len(_results)} checks passed "
          f"in {time.time() - started:.1f}s")
    if failed:
        print("\nfailed:")
        for label in failed:
            print(f"  - {label}")
    print("\nALL GOOD" if not failed else "\nSOMETHING IS WRONG")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
