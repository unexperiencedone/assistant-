"""Talk modes: just chatting, or brainstorming, the way you would with a person.

Everything else Nova does is aimed at finishing a job: short replies, tools, scripts, a
wake word in front of every sentence. None of that suits a conversation. A mode changes
four things while it is on, and nothing else:

- **Listening.** The microphone stays open after every reply (not only after a question),
  so you answer the way you would answer a person, without saying "Nova" each time.
- **Routing.** What you say goes to the conversation, not to the script matchers. A
  name or a half-thought is talk, and must never turn into an action -- "gulzar ali",
  said on its own, once became a phone call.
- **The brain.** The free fast model, with no tool schemas attached: the cheapest turn
  there is, and quick enough for back-and-forth.
- **The voice.** The model may use delivery tags -- [chuckles], [sighs], [excited] -- which
  Fish's voice performs and the screen never shows (audio/fish.py).

Brainstorming adds a recap: "what have we got" or "wrap it up" asks for the ideas as a
list, which is saved to Documents\\Nova Brainstorms and put on screen.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .persona import delivery

IDLE_MINUTES = 15        # a mode nobody has spoken in for this long ends quietly
LISTEN_SECONDS = 20      # how long the microphone stays open after each reply

# The delivery rule, in full: the table from the moment to the tag (persona/delivery.py).
VOICE = delivery.guide("talk")
# Without this the free models fall back on "I'm a text-only assistant" when asked why
# they can't speak, which is false: every reply here is said aloud on the PC.
SPOKEN = ("You have a voice: every reply you give is spoken aloud through the PC's speakers, so never "
          "say you are text-only or can't speak. If they say they didn't hear you, say the audio may not "
          "have played and suggest checking the volume or output device.")

CHAT = """You are in a casual conversation with {owner}, not doing a task. Talk the way a sharp, warm friend \
would: react to what they said, have opinions and say them, ask one thing back when you are curious, \
and let a joke land when there is one. Keep turns short -- one to three sentences, like real speech -- so it \
stays a back-and-forth rather than a lecture. No lists, no headings, no "great question", no offers of help. \
Don't steer back to work unless they do. When they have nothing in particular, or ask you to pick, take the \
lead: bring up something specific -- one of their projects or interests from what you know about them, \
something from their day, or a genuinely interesting idea or question -- and run with it, rather than \
asking them what they want to talk about. You are still software and say so if asked, but you don't keep \
reminding them. {spoken} {voice}"""

BRAINSTORM = """You are brainstorming with {owner}{about}. Your job is to make the ideas better and stranger, \
not to be agreeable: build on what they say ("yes, and..."), add two or three ideas of your own per turn, \
include at least one that is unexpected, and say plainly when an idea has a hole in it. Name ideas with a \
few memorable words so you can both refer back to them. End most turns with one sharp question that \
pushes the thinking further. Keep it spoken: short sentences, no markdown, no lists unless asked for a \
recap. Remember every idea raised, theirs and yours; you will be asked for them at the end. {spoken} {voice}"""

RECAP = """Recap this brainstorm. Write it as Markdown: a one-line title, then the ideas grouped under short \
headings, each idea as one bullet with its memorable name in bold and one line on why it is interesting. \
Mark the two or three strongest with (strong). Finish with "Open questions" as bullets. Then, on the last \
line, one spoken sentence saying how many ideas there were and which one you would pick."""


@dataclass
class TalkMode:
    kind: str                 # "chat" | "brainstorm"
    topic: str = ""
    since: float = 0.0
    last: float = 0.0
    lead: bool = False        # they handed over the choice of topic: Nova opens with one

    @property
    def label(self) -> str:
        if self.kind == "brainstorm":
            return f"Brainstorming{': ' + self.topic if self.topic else ''}"
        return "Just talking"

    def instructions(self, owner: str) -> str:
        owner = owner or "the user"
        if self.kind == "brainstorm":
            about = f" about {self.topic}" if self.topic else ""
            return BRAINSTORM.format(owner=owner, about=about, spoken=SPOKEN, voice=VOICE)
        return CHAT.format(owner=owner, spoken=SPOKEN, voice=VOICE)

    def touch(self) -> None:
        self.last = time.time()

    def idle(self) -> bool:
        return time.time() - (self.last or self.since) > IDLE_MINUTES * 60

    def to_dict(self) -> dict:
        # "subject", not "topic": EventBus.publish takes the bus topic as its first argument.
        return {"kind": self.kind, "subject": self.topic, "label": self.label, "since": self.since}


def start(kind: str, topic: str = "") -> TalkMode:
    now = time.time()
    topic = re.sub(r"^(?:about|on|for|some ideas for|ideas for)\s+", "", (topic or "").strip(" .,!?"), flags=re.I)
    # "talk to me about anything" names no topic; it hands the choice over.
    lead = topic.lower() in VAGUE_TOPICS
    mode = TalkMode(kind, "" if lead else topic, now, now)
    mode.lead = lead
    return mode


VAGUE_TOPICS = {"anything", "anything at all", "whatever", "something", "stuff", "random stuff", "nothing",
                "you pick", "your choice"}
LEAD_PROMPT = "You pick. Start the conversation with something specific."


def opening(mode: TalkMode) -> str:
    """What Nova says as a mode starts. The tag is for the voice; the screen drops it."""
    if mode.kind == "brainstorm":
        if mode.topic:
            return f"[excited] Ooh, {mode.topic}. Go on then, what's the first thing in your head?"
        return "[excited] I'm in. What are we brainstorming?"
    return "[warmly] Sure, I'm all ears. What's on your mind?"


def recap_folder() -> Path:
    """Documents\\Nova Brainstorms, next to the Word documents Nova writes."""
    docs = Path(os.path.expandvars(r"%USERPROFILE%\OneDrive\Documents"))
    if not docs.is_dir():
        docs = Path.home() / "Documents"
    return docs / "Nova Brainstorms"


def save_recap(markdown: str, topic: str, folder: Path | None = None) -> Path:
    folder = folder or recap_folder()
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\s-]", "", topic or "brainstorm").strip()[:60] or "brainstorm"
    path = folder / f"{time.strftime('%Y-%m-%d %H%M')} {stem}.md"
    path.write_text(markdown.strip() + "\n", encoding="utf-8")
    return path
