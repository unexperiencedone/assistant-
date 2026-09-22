"""Typed settings loaded from config.toml, with defaults for every key."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .paths import APP_DIR

DEFAULT_CONFIG = APP_DIR / "config.toml"


@dataclass
class AssistantSettings:
    name: str = "Nova"
    # Who the assistant says made it. Whatever runs underneath is an implementation
    # detail; this is the answer to "who are you" (assistant/persona/).
    maker: str = "Kaiketsu Tech"
    # Catch a known script said a different way (assistant/matching.py), and when the
    # rules cannot decide, let a free Groq model choose between a script and the agent.
    loose_matching: bool = True
    loose_classifier: bool = True
    wake_word: str = "nova"
    wake_aliases: list[str] = field(default_factory=list)  # extra spellings Whisper produces for the wake word
    # When you may answer without the wake word: "questions" (after "Hey Nova" on its own,
    # or when Nova asked you something), "always" (after every reply), or "off".
    follow_up: str = "questions"
    follow_up_seconds: float = 8
    filler_after_seconds: float = 1.5


@dataclass
class SpeechSettings:
    enabled: bool = True  # false: no microphone (type in the canvas instead)
    engine: str = "whisper"
    whisper_model: str = "base.en"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    language: str = "en"
    energy_threshold: int = 300
    pause_threshold: float = 1.0          # silence that ends a phrase
    continuation_seconds: float = 0.7     # after a phrase, keep listening this long for more
    incomplete_wait_seconds: float = 4.0  # total wait when the sentence sounds unfinished
    phrase_time_limit: float = 30
    min_avg_logprob: float = -1.0         # Whisper confidence below this is treated as noise (see audio/gate.py)


@dataclass
class VoiceSettings:
    # Local speech only: cloud voices rate-limit mid-reply on the free tiers.
    engine: str = "auto"  # auto | sapi (Windows) | pyttsx3
    rate: int = 0
    volume: int = 100
    voice_contains: str = ""


@dataclass
class ClaudeAgentSettings:
    executable: str = "claude"
    permission_mode: str = "auto"
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""


@dataclass
class AntigravityAgentSettings:
    executable: str = "agy"
    mode: str = "accept-edits"
    skip_permissions: bool = False
    model: str = ""
    print_timeout: str = "30m"


@dataclass
class ChatAgentSettings:
    """Any OpenAI-compatible chat provider. The model must support tool calling."""

    model: str = ""
    api_key_env: str = ""       # read from the environment or .env
    max_tool_calls: int = 6     # model/tool rounds per request
    max_tokens: int = 1200
    temperature: float = 0.3
    timeout: float = 120
    history_messages: int = 24
    # When this tier runs out of steps, or can't be reached at all, pass the whole job
    # to Claude Code instead of handing the user a dead end and asking them to re-route
    # it themselves. Off means a failure is reported as a failure.
    escalate_to_claude: bool = True


@dataclass
class GroqAgentSettings(ChatAgentSettings):
    # Groq answers in about a second. This key's models are listed by `python main.py models`;
    # Llama chat models aren't offered on it, and the Llama-based groq/compound ones refuse
    # tool calls, so the default is the strongest model here that can use Nova's tools.
    model: str = "openai/gpt-oss-120b"
    api_key_env: str = "GROQ_API_KEY"


@dataclass
class OpenRouterAgentSettings(ChatAgentSettings):
    # "openrouter/free" routes to whatever free model is up;
    # "nvidia/nemotron-3.5-lightning:free" is a fixed alternative.
    model: str = "openrouter/free"
    api_key_env: str = "OPENROUTER_API_KEY"


@dataclass
class AgentSettings:
    default: str = "claude"
    # How many agent tasks may run at once. Each extra task opens its own agent session,
    # gets its own canvas lane, and stays silent except when it finishes (one voice only).
    max_parallel: int = 2
    workspace: str = "."
    continue_session: bool = True
    claude: ClaudeAgentSettings = field(default_factory=ClaudeAgentSettings)
    antigravity: AntigravityAgentSettings = field(default_factory=AntigravityAgentSettings)
    openrouter: OpenRouterAgentSettings = field(default_factory=OpenRouterAgentSettings)
    groq: GroqAgentSettings = field(default_factory=GroqAgentSettings)


@dataclass
class UISettings:
    # browser: canvas in your browser | window: native app window | tray: start hidden in the tray
    mode: str = "browser"
    hotkey: str = "ctrl+alt+n"  # toggles the canvas window in window/tray mode
    terminal_dashboard: bool = True
    web_dashboard: bool = True  # the canvas server; needed by every mode
    web_port: int = 8765
    open_browser: bool = True
    # Which interface the canvas server binds to. Loopback keeps it on this PC; widening
    # it is what lets a phone reach Nova, and is only safe with require_token on.
    host: str = "127.0.0.1"
    require_token: bool = True          # a shared secret on every request; see ui/auth.py
    token_file: str = "data/nova_token"  # generated on first start, git-ignored


@dataclass
class LocalSettings:
    enabled: bool = True
    db_path: str = "data/index.db"
    roots: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=list)
    max_depth: int = 10
    index_hidden: bool = False
    reindex_minutes: float = 30
    full_rescan_hours: float = 24
    half_life_days: float = 14  # for any kind not listed below
    # Apps are habits (slow to fade), files belong to this week's project, commands in between.
    half_life_by_kind: dict[str, float] = field(
        default_factory=lambda: {"app": 30, "file": 7, "folder": 14, "command": 10})


@dataclass
class SessionSettings:
    """Raw per-turn agent logs: the complete record behind the canvas summaries."""
    enabled: bool = True
    folder: str = "data/sessions"
    keep_days: int = 14


@dataclass
class HistorySettings:
    """Timed work sessions: everything said and done, browsable in the canvas."""
    enabled: bool = True
    db_path: str = "data/history.db"
    idle_minutes: float = 30   # a session ends after this long with no activity (never mid-task)
    keep_days: int = 365


@dataclass
class AutomationSettings:
    enabled: bool = True
    folder: str = "automations"  # TOML automation files; relative to config.toml


@dataclass
class PromotionSettings:
    """Turning requests that keep going through the agent into instant automations, so
    routine tasks stop costing tokens over the life of the assistant. See AGENTS.md
    section 12 and assistant/automation/promotion.py."""
    enabled: bool = True
    store_path: str = "data/task_frequency.json"
    # How many times a [[TASK: type]] shows up before Nova writes the automation itself,
    # no confirmation needed.
    auto_after: int = 3
    # Below auto_after, ask once by voice before writing anything if a turn used at
    # least this many tool calls, or cost at least this much (whichever comes first).
    ask_min_turns: int = 4
    ask_min_cost_usd: float = 0.01
    # ...or if it simply took a long time. Two minutes of waiting is worth never
    # waiting through again, whatever it cost; anything quicker is not worth a question.
    ask_min_seconds: float = 123


@dataclass
class AmbientSettings:
    """The briefing panel: weather, markets and headlines, compiled on request.

    Nothing is polled. Asking costs one agent turn; the result is cached in
    `cache_path` and shown until you ask again."""
    enabled: bool = True
    cache_path: str = "data/ambient.json"
    place: str = ""                     # where "the weather" means; empty asks the agent to work it out
    markets: list[str] = field(default_factory=lambda: ["NIFTY 50", "S&P 500"])
    headlines: int = 3
    topics: list[str] = field(default_factory=list)   # what to prefer when picking headlines


@dataclass
class AwarenessSettings:
    """What you were doing on this PC: app names and window titles, nothing else.

    Off by default, and even when enabled it only starts watching when you say so
    (or when watch_on_start is true). Raw events are short-lived; the collapsed
    sessions are what questions are answered from."""
    enabled: bool = False
    watch_on_start: bool = False
    db_path: str = "data/awareness.db"
    poll_seconds: float = 2
    dwell_seconds: float = 12      # shorter than this is Alt-Tabbing past, not working
    idle_seconds: float = 120      # no input for this long ends the stretch
    merge_gap_seconds: float = 120  # back within this = the same stretch of work
    raw_days: float = 7
    session_days: float = 19


@dataclass
class CaptureSettings:
    """Screen recordings and phone photos, and where they land.

    Nothing in the inbox is ever deleted automatically: footage is the one thing that
    cannot be re-shot. Editing is local ffmpeg on the CPU (there is no GPU here), so a
    long render takes minutes. See docs/standing_agent.md."""
    enabled: bool = True
    inbox: str = "data/inbox"
    fps: int = 25
    monitor: int = 1      # which screen to record; 0 records the whole desktop, both screens
    audio: bool = False   # system audio needs a loopback device; off until one is set up


@dataclass
class PublishSettings:
    """The publishing arms, and the gate in front of them.

    Nothing here sends by itself. Everything is staged as a draft, said back to you, and
    goes out only when you approve that specific draft. Credentials for LinkedIn and
    Instagram live in .env, never here."""
    enabled: bool = True
    db_path: str = "data/publish.db"


@dataclass
class GoalsSettings:
    """Standing goals: the things Nova starts on its own, without being asked each time.

    A goal is a request in Nova's own words, submitted through the same pipeline you
    speak into -- so every confirmation that guards a text, a call or a post still
    guards a goal. Nothing fires while you are mid-conversation, and nothing fires
    during quiet hours. See docs/standing_agent.md."""
    enabled: bool = True
    db_path: str = "data/goals.db"
    check_seconds: float = 120   # how often it looks for something due
    quiet_from: int = 22         # no goal fires between these hours (set equal to disable quiet hours)
    quiet_to: int = 8
    one_at_a_time: bool = True   # a backlog goes out one per tick, never in a burst


@dataclass
class JournalSettings:
    """Nova's own record of its days: one short entry per finished day.

    Written from the work history (and the activity record, when that is on), never
    from nothing. The last few entries go into every agent's instructions, which is
    what gives Nova continuity across sessions rather than a fresh introduction each
    time. See docs/journal.md."""
    enabled: bool = True
    db_path: str = "data/journal.db"
    check_minutes: float = 30   # how often it looks for a finished day that has no entry
    backfill_days: int = 3      # how far back it will still write an entry (after the laptop was off)
    context_days: int = 3       # entries put in front of the agents
    keep_days: float = 730
    narrate: bool = True        # let a free chat model phrase the entry; the facts still come from the rows


@dataclass
class NarratorSettings:
    """Turning several raw results into one thing Nova would actually say.

    Only where the text reaching you was never written in one voice by anything: a
    finished group of parallel tasks, whose answer is two or three backends' replies
    stapled together. Never on an instant intent -- those answer offline in
    microseconds, and a network call to sound marginally warmer is the exact trade the
    cascade exists to refuse. Every narration is checked against the source and thrown
    away if it lost a number or a name, so this cannot make an answer less accurate."""
    enabled: bool = True
    groups: bool = True         # narrate a finished multi-step group
    max_chars: int = 600


@dataclass
class OrchestrateSettings:
    """Running several pieces of work for one request, and answering once.

    The fan-out cap is the important number. A small model asked to decompose will
    decompose anything, and four background tasks for "what time is it" would undo the
    cost cascade this sits on top of. Three is enough for a real multi-part request."""
    enabled: bool = True
    max_fanout: int = 3          # most steps one request may spawn; extras are dropped
    backend: str = "claude"      # which brain runs a step; falls back to the current one


@dataclass
class RecipeSettings:
    """What worked last time, recorded and fed back (docs/recipes.md).

    Not a script: a recipe is evidence that a route worked, so a model reading one is
    expected to adapt it. This is what lets the cheap tier take over a job the
    expensive tier solved once, which is the only way the running cost actually falls."""
    enabled: bool = True
    db_path: str = "data/recipes.db"
    hint_agents: bool = True     # put a matching recipe in front of the agent for the turn


@dataclass
class SkillsSettings:
    """Agent skills: folders holding a SKILL.md that teaches a model one kind of job.

    Shared with Claude Code rather than duplicated -- the same folder serves both, so a
    skill written once improves every brain Nova has. Only the names go in the system
    prompt (about fifty tokens); a document is loaded only when a model asks for it."""
    enabled: bool = True
    paths: list[str] = field(default_factory=lambda: ["C:/claude_skills", ".claude/skills"])
    read_limit: int = 6000   # characters of one skill handed over at a time


@dataclass
class PhoneSettings:
    """Nova's control of your phone through the Termux bridge (phone/README.md).

    Reachable over Tailscale only, and only for the named actions the bridge knows;
    the token is a credential, so it comes from .env rather than this file."""
    enabled: bool = False
    host: str = ""                        # the phone's Tailscale address (100.x.x.x)
    port: int = 8766
    token_env: str = "NOVA_PHONE_TOKEN"   # put the value in .env next to config.toml
    timeout: float = 12
    confirm_destructive: bool = True      # a text message is always asked about first
    sim_slot: int = 0                     # dual-SIM: 0 is SIM 1, 1 is SIM 2 (texts)


@dataclass
class Settings:
    assistant: AssistantSettings = field(default_factory=AssistantSettings)
    speech: SpeechSettings = field(default_factory=SpeechSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    agents: AgentSettings = field(default_factory=AgentSettings)
    ui: UISettings = field(default_factory=UISettings)
    local: LocalSettings = field(default_factory=LocalSettings)
    automations: AutomationSettings = field(default_factory=AutomationSettings)
    promotion: PromotionSettings = field(default_factory=PromotionSettings)
    sessions: SessionSettings = field(default_factory=SessionSettings)
    history: HistorySettings = field(default_factory=HistorySettings)
    ambient: AmbientSettings = field(default_factory=AmbientSettings)
    phone: PhoneSettings = field(default_factory=PhoneSettings)
    awareness: AwarenessSettings = field(default_factory=AwarenessSettings)
    journal: JournalSettings = field(default_factory=JournalSettings)
    goals: GoalsSettings = field(default_factory=GoalsSettings)
    capture: CaptureSettings = field(default_factory=CaptureSettings)
    publish: PublishSettings = field(default_factory=PublishSettings)
    skills: SkillsSettings = field(default_factory=SkillsSettings)
    recipes: RecipeSettings = field(default_factory=RecipeSettings)
    orchestrate: OrchestrateSettings = field(default_factory=OrchestrateSettings)
    narrator: NarratorSettings = field(default_factory=NarratorSettings)
    # Folder containing config.toml; relative paths in the config resolve against it.
    base_dir: Path = field(default_factory=lambda: APP_DIR)

    @property
    def workspace(self) -> Path:
        path = Path(os.path.expandvars(self.agents.workspace)).expanduser()
        return (path if path.is_absolute() else self.base_dir / path).resolve()


def _merge(target: Any, values: dict[str, Any]) -> None:
    """Copy known keys from a TOML table onto a dataclass, recursing into sub-tables."""
    known = {f.name for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            print(f"[config] ignoring unknown key: {key}")
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(target, key, value)


def load_env_file(path: Path) -> None:
    """Read KEY=value lines from .env into the environment (real env vars win).

    Keeps API keys out of config.toml, which is tracked by git.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings(path: str | Path = DEFAULT_CONFIG) -> Settings:
    """Load settings; works the same no matter which folder you launch from."""
    settings = Settings()
    path = Path(path).expanduser().resolve()
    for folder in {path.parent, APP_DIR}:  # next to the config, and next to Nova itself
        load_env_file(folder / ".env")
    if path.exists():
        with path.open("rb") as fh:
            _merge(settings, tomllib.load(fh))
        settings.base_dir = path.parent
    else:
        print(f"[config] {path} not found, using defaults")
    return settings
