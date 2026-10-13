import { useEffect, useRef, useState } from "react";
import { getJSON } from "./api.js";
import NeedsYou from "./NeedsYou.jsx";

// Now: the conversation, what's running, what's waiting on you, and today.

function useStickToBottom(dep) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [dep]);
  return ref;
}

function useToday() {
  const [items, setItems] = useState([]);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getJSON("/api/schedule/today")
        .then((data) => !cancelled && setItems(data.items ?? []))
        .catch(() => {});
    load();
    const timer = setInterval(() => {
      setNow(Date.now());
      load();
    }, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
  return [items, now];
}

/** A standing goal's instruction, shortened, with its control tokens ([[QUIET]]) removed. */
const goalText = (text) => {
  const clean = text.replace(/\[\[[A-Z]+\]\]\.?/g, "").replace(/\s+/g, " ").trim();
  return clean.length > 120 ? `${clean.slice(0, 117)}…` : clean;
};

const clock = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

function Timeline({ items, now }) {
  const day = new Date(now);
  const at = (h) => new Date(day.getFullYear(), day.getMonth(), day.getDate(), h).getTime();
  const timed = items.filter((i) => !i.all_day).map((i) => ({ ...i, ms: i.start * 1000 }));
  const hours = [new Date(now).getHours(), ...timed.map((i) => new Date(i.ms).getHours())];
  const from = at(Math.min(8, ...hours));
  const to = at(Math.min(24, Math.max(22, ...hours.map((h) => h + 1))));
  const pos = (ms) => `${Math.max(2, Math.min(98, ((ms - from) / (to - from)) * 100))}%`;
  return (
    <div className="m-line" aria-label="Today">
      {timed.length === 0 ? (
        <span className="m-tick" style={{ left: "22%" }}>
          Nothing scheduled today · say “remind me at…”
        </span>
      ) : (
        timed.map((item) => (
          <span key={`${item.title}-${item.ms}`} className="m-tick" style={{ left: pos(item.ms) }} title={item.title}>
            <b>{clock(item.ms)}</b> {item.title.length > 30 ? `${item.title.slice(0, 29)}…` : item.title}
          </span>
        ))
      )}
      <span className="m-tick now" style={{ left: pos(now) }}>
        now · {clock(now)}
      </span>
    </div>
  );
}

function Mission({ graph, running }) {
  const steps = (graph?.nodes ?? []).filter((n) => n.kind !== "request" && n.kind !== "route");
  if (!running || steps.length === 0) return null;
  const done = steps.filter((s) => s.status === "done").length;
  return (
    <div className="m-panel m-mission" aria-label="Running now">
      <div className="hd">
        <span>{graph.title || graph.label || "Working on it"}</span>
        <span className="m-stamp solid">
          {done} of {steps.length}
        </span>
      </div>
      {steps.slice(-5).map((step) => (
        <div key={step.id} className="m-obj">
          <i className={`m-mark${step.status === "done" ? " done" : step.status === "running" ? " live" : ""}`} />
          <span>{step.label}</span>
        </div>
      ))}
    </div>
  );
}

export default function Now({ snapshot, graph, say, active }) {
  const conversation = snapshot?.conversation ?? [];
  const items = snapshot?.inbox?.items ?? [];
  const live = snapshot?.live;
  const job = live?.running?.[0];
  const [text, setText] = useState("");
  const listRef = useStickToBottom(conversation.length);
  const [today, now] = useToday();

  const submit = (event) => {
    event.preventDefault();
    if (!text.trim()) return;
    say(text);
    setText("");
  };

  return (
    <div className="m-now">
      <div className="m-story">
        <section className="m-panel splash m-convo-frame" aria-label="Conversation">
          <div className="m-convo" ref={listRef}>
          {conversation.length === 0 ? (
            <p className="m-empty">Say “Nova…”, or type below. One conversation: Nova decides when to act.</p>
          ) : (
            conversation.slice(-30).map((m, i) =>
              m.role === "user" && m.source === "goal" ? (
                <div key={i} className="m-me">
                  <div className="m-caption" title={m.text}>
                    <span className="m-tab">
                      Standing goal · <span lang="hi">नियम</span>
                    </span>
                    <b>{goalText(m.text)}</b>
                  </div>
                </div>
              ) : m.role === "user" ? (
                <div key={i} className="m-me">
                  <div className="m-caption">
                    <span className="m-tab">
                      You · <span lang="hi">आप</span>
                    </span>
                    <b>{m.text}</b>
                  </div>
                </div>
              ) : (
                <div key={i} className="m-nova">
                  <span className="m-orb" aria-hidden="true" />
                  <div className="m-balloon">
                    <p className="m-said">{m.text}</p>
                  </div>
                </div>
              ),
            )
          )}
          <Mission graph={graph} running={Boolean(snapshot?.agent_running)} />
          </div>
        </section>
        <form className="m-panel m-compose" onSubmit={submit}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Say it, or type it. Nova decides when to act." aria-label="Message Nova" />
          <button type="submit" className="send" aria-label="Send">
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="#0d0d0d" strokeWidth="2.8" strokeLinecap="square">
              <path d="M2 9h12M9 3l6 6-6 6" />
            </svg>
          </button>
        </form>
      </div>

      <NeedsYou items={items} active={active} />

      <div className="m-today">
        <div className="m-panel m-running">
          <b>{job ? `Running · ${job.phase || job.label || job.command?.tool?.replace(/_/g, " ") || "a task"}` : "Nothing running"}</b>
          <div className={`m-bar${job ? " busy" : ""}`}>
            <i className={job ? "m-tone" : ""} />
          </div>
        </div>
        <Timeline items={today} now={now} />
      </div>
    </div>
  );
}
