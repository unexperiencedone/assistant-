import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useNarrow } from "./device.js";

/**
 * Nova herself: one morphing ink blob, always visible, left of everything else.
 *
 * The blob is not decoration. Its colour and how fast it moves are the real status
 * slice (assistant/events.py publishes idle / listening / transcribing / thinking /
 * speaking; "working" is agent_running). Speaking gets an irregular pulse instead of
 * a clean loop, because speech is not a clean loop.
 */

// Embers rise on both sides of the blob. The right-hand set is the left one mirrored,
// with its drift reversed and its timing nudged, so the two sides answer each other
// instead of marching in step.
const EMBERS = Array.from({ length: 6 }).flatMap((_, i) => {
  const offset = `${26 + i * 26}px`;
  const shared = {
    size: 2.5 + (i % 3),
    color: i % 2 ? "rgba(255,138,101,0.5)" : "rgba(124,92,255,0.5)",
    duration: `${6 + (i % 3) * 1.4}s`,
    drift: (i % 2 ? 1 : -1) * (8 + i * 2),
  };
  return [
    { ...shared, side: "left", offset, delay: `${i * 0.6}s` },
    { ...shared, side: "right", offset, delay: `${i * 0.6 + 0.35}s`, drift: -shared.drift },
  ];
});

/** Which creature the blob is right now, from state the backend already publishes. */
export function inkState({ status, agentRunning }) {
  if (status === "offline") return "offline";
  if (status === "speaking") return "speaking";
  if (agentRunning) return "working";
  if (status === "thinking" || status === "transcribing" || status === "listening") return status;
  return "idle";
}

/**
 * What a tool call is, in plain words. The command itself is no use at a glance —
 * it belongs in the Activity log and in History, where it is kept in full.
 */
const DOING = [
  [/^(bash|shell|powershell|cmd|run)/i, "Running a command"],
  [/^(read|cat|open_file|notebookread)/i, "Reading a file"],
  [/^(write|edit|multiedit|apply_patch|notebookedit)/i, "Editing a file"],
  [/^(glob|grep|search|find)/i, "Searching this PC"],
  [/^(webfetch|websearch|fetch)/i, "Reading the web"],
  [/^browser/i, "Working in the browser"],
  [/^(ui|desktop|macro|automation)/i, "Driving an app"],
  [/^(task|agent)/i, "Thinking it through"],
];

export function doingLabel(tool) {
  for (const [pattern, phrase] of DOING) {
    if (pattern.test(tool || "")) return phrase;
  }
  return tool ? `Running ${tool}` : "Working";
}

/**
 * What Nova is doing right now, in plain words.
 *
 * Only while something is actually running. When the turn is over the line goes back
 * to the resting state rather than drifting into whatever was last said — the reply
 * is already in the conversation, and repeating it here just made the line churn.
 */
export function currentAction(snapshot) {
  if (!snapshot?.agent_running) return "";
  const activity = snapshot.activity ?? [];
  for (let i = activity.length - 1; i >= 0; i -= 1) {
    const row = activity[i];
    if (row.kind === "result" || row.kind === "error") break; // that turn is over
    if (row.kind === "tool" && (row.tool || row.text)) return doingLabel(row.tool);
  }
  return "Working";
}

const STATE_LINE = {
  idle: "Waiting",
  listening: "What to do?",    // replaced by one of the lines below while listening
  transcribing: "Catching that",
  thinking: "Thinking",
  working: "Working",
  speaking: "Speaking",
  offline: "Not connected",
};

// While the microphone is open, Nova asks rather than announces. Which pool depends
// on whether this is the first thing you have said or the next one.
const OPENING = ["What to do?", "Say the word", "I'm listening", "Ready when you are", "Yes?"];
const CONTINUING = ["What else?", "What next?", "Anything else?", "Go on", "And then?"];
const PROMPT_SECONDS = 5;

/** Rotates the listening prompt, starting somewhere different each time. */
function useListeningPrompt(listening, continuing) {
  const [step, setStep] = useState(0);
  const seed = useRef(0);

  useEffect(() => {
    if (!listening) return undefined;
    seed.current = Math.floor(Math.random() * 100);
    setStep(0);
    const id = setInterval(() => setStep((current) => current + 1), PROMPT_SECONDS * 1000);
    return () => clearInterval(id);
  }, [listening]);

  if (!listening) return "";
  const pool = continuing ? CONTINUING : OPENING;
  return pool[(seed.current + step) % pool.length];
}

/** The two states where Nova is busy and has nothing to report yet. */
const BOUNCING = new Set(["thinking", "working"]);

function planLine(plan) {
  const steps = plan?.steps ?? [];
  if (!steps.length) return "";
  const index = steps.findIndex((step) => step.status === "running");
  const at = index >= 0 ? index : steps.filter((step) => step.status === "done").length;
  const step = steps[Math.min(at, steps.length - 1)];
  return `Step ${Math.min(at + 1, steps.length)} of ${steps.length} · ${step?.text ?? ""}`;
}

const REDUCED = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Types the line out the first time it appears, and again whenever it changes. */
function Typed({ text, speed = 22 }) {
  const [shown, setShown] = useState(text);
  const previous = useRef(text);

  useEffect(() => {
    if (text === previous.current) return undefined;
    previous.current = text;
    if (!text || REDUCED()) {
      setShown(text);
      return undefined;
    }
    setShown("");
    let at = 0;
    const id = setInterval(() => {
      at += 1;
      setShown(text.slice(0, at));
      if (at >= text.length) clearInterval(id);
    }, speed);
    return () => clearInterval(id);
  }, [text, speed]);

  return <>{shown}</>;
}

/** Three dots keeping time with each other while Nova has nothing to say yet. */
function Dots() {
  return (
    <span className="dancing" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

function NovaEntity({ snapshot, status, mode, onMode, ambientLine, onOpenBriefing, rightEdge }) {
  const state = inkState({ status, agentRunning: snapshot?.agent_running });
  const simple = mode === "simple";
  const narrow = useNarrow();
  // On a phone the blob shares the screen with the conversation, so it gives way.
  const size = narrow ? 148 : simple ? 260 : 230;
  const action = useMemo(() => (simple ? currentAction(snapshot) : ""), [simple, snapshot]);
  const step = planLine(snapshot?.plan);
  const prompt = useListeningPrompt(state === "listening", (snapshot?.conversation?.length ?? 0) > 0);
  const line = (simple && action) || prompt || STATE_LINE[state];
  const bouncing = BOUNCING.has(state);

  return (
    <section className={`entity ink-${state}`} aria-label="Nova">
      {rightEdge}
      <div className="entity-head">
        <span className="label">{snapshot?.name ?? "Nova"}</span>
        <div className="mode-toggle" role="group" aria-label="How much to show">
          <button type="button" className={simple ? "" : "on"} aria-pressed={!simple} onClick={() => onMode("plan")}>
            Plan
          </button>
          <button type="button" className={simple ? "on" : ""} aria-pressed={simple} onClick={() => onMode("simple")}>
            Simple task
          </button>
        </div>
      </div>

      <div className="blob-stage" style={{ height: `${size + 20}px` }} aria-hidden="true">
        {state !== "idle" && state !== "offline"
          ? EMBERS.map((ember, i) => (
              <span
                key={i}
                className="ember"
                style={{
                  [ember.side]: ember.offset,
                  width: `${ember.size}px`,
                  height: `${ember.size}px`,
                  background: ember.color,
                  animationDuration: ember.duration,
                  animationDelay: ember.delay,
                  "--dx": `${ember.drift}px`,
                }}
              />
            ))
          : null}
        <div className="blob" style={{ width: `${size}px`, height: `${size}px` }} />
      </div>

      <div className="entity-status" role="status" aria-live="polite">
        {simple ? <div className="label">Right now</div> : null}
        <div className={simple ? "now" : "line"}>
          <Typed text={line} />
          {bouncing ? <Dots /> : null}
        </div>
        {!simple && step ? <div className="sub">{step}</div> : null}
      </div>

      <div className="entity-foot">
        <span className="ambient-line">{ambientLine}</span>
        <button type="button" onClick={onOpenBriefing}>
          briefing
        </button>
      </div>
    </section>
  );
}

export default memo(NovaEntity);
