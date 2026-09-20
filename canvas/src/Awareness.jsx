import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * What you were doing: the collapsed view of Windows activity (assistant/awareness).
 *
 * Nothing here is pixels or keystrokes — app names, window titles and page titles,
 * merged into blocks and then sessions by dwell time and idle gaps. The model is only
 * asked at the very end, about a handful of rows, when you ask it something.
 */

const CATEGORY_COLOR = {
  editor: "#ff8a65",
  browser: "#7c5cff",
  call: "#a6f5cf",
  terminal: "#c9a0ff",
  idle: "rgba(255,255,255,0.18)",
  other: "#6b6577",
};

const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

function span(seconds) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours} h ${String(minutes % 60).padStart(2, "0")} m` : `${minutes} m`;
}

async function getJson(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

const post = (path, body) =>
  getJson(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

function Rail({ sessions }) {
  const total = sessions.reduce((sum, s) => sum + Math.max(1, s.seconds), 0) || 1;
  if (!sessions.length) return null;
  return (
    <>
      <div className="aw-rail">
        {sessions.map((session, i) => (
          <i
            key={i}
            title={`${session.app} · ${span(session.seconds)}`}
            style={{
              width: `${(Math.max(1, session.seconds) / total) * 100}%`,
              background: CATEGORY_COLOR[session.category] ?? CATEGORY_COLOR.other,
              opacity: session.category === "idle" ? 1 : 0.55,
              borderTop: `2px solid ${CATEGORY_COLOR[session.category] ?? CATEGORY_COLOR.other}`,
            }}
          />
        ))}
      </div>
      <div className="aw-ticks">
        <span>{clock(sessions[0].start)}</span>
        <span>{clock(sessions[sessions.length - 1].end)}</span>
      </div>
      <div className="aw-legend">
        {[...new Set(sessions.map((s) => s.category))].map((category) => (
          <span key={category}>
            <i style={{ background: CATEGORY_COLOR[category] ?? CATEGORY_COLOR.other }} />
            {category}
          </span>
        ))}
      </div>
    </>
  );
}

export default function Awareness({ onClose, revision }) {
  const [state, setState] = useState(null);
  const [day, setDay] = useState(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const askRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const [config, today] = await Promise.all([getJson("/api/awareness/state"), getJson("/api/awareness/day")]);
      setState(config);
      setDay(today);
      setError("");
    } catch (e) {
      setError(`Couldn't read the activity store: ${e.message}`);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, revision]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "/" && document.activeElement?.tagName !== "INPUT") {
        event.preventDefault();
        askRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const watch = async (on) => {
    try {
      setState(await post("/api/awareness/watch", { on }));
      load();
    } catch (e) {
      setError(`Couldn't change that: ${e.message}`);
    }
  };

  const ask = async (event) => {
    event.preventDefault();
    if (!question.trim() || asking) return;
    setAsking(true);
    try {
      setAnswer(await post("/api/awareness/ask", { question: question.trim() }));
    } catch (e) {
      setError(`Couldn't answer that: ${e.message}`);
    }
    setAsking(false);
  };

  const forget = async () => {
    if (!window.confirm("Delete every recorded event and session? This cannot be undone.")) return;
    await post("/api/awareness/forget");
    load();
  };

  const sessions = day?.sessions ?? [];
  const coding = day?.coding;
  const totals = useMemo(() => {
    const by = new Map();
    for (const session of sessions) {
      if (session.category === "idle") continue;
      by.set(session.app, (by.get(session.app) ?? 0) + session.seconds);
    }
    return [...by.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
  }, [sessions]);

  return (
    <div className="awareness">
      <header className="profile-bar">
        <div>
          <h2>What you were doing</h2>
          <p className="mono">
            app names, window titles and page titles — never keystrokes, never pixels unless you ask for a screenshot
          </p>
        </div>
        <div className="profile-bar-actions">
          {state?.watching ? (
            <>
              <span className="save-state" style={{ color: "#a6f5cf" }}>
                <i className="live-dot" />
                watching
              </span>
              <button type="button" onClick={() => watch(false)}>
                Stop watching
              </button>
            </>
          ) : (
            <button type="button" className="primary" onClick={() => watch(true)} disabled={!state?.enabled}>
              Start watching
            </button>
          )}
          <button type="button" className="close" onClick={onClose} aria-label="Close activity">
            ×
          </button>
        </div>
      </header>
      {error ? <div className="banner banner-denied">{error}</div> : null}

      {state && !state.enabled ? (
        <div className="aw-off">
          <p>
            Activity awareness is turned off. Set <span className="mono">enabled = true</span> under{" "}
            <span className="mono">[awareness]</span> in config.toml, then restart Nova.
          </p>
          <p className="mono">It ships off on purpose: this is a record of your own app use.</p>
        </div>
      ) : (
        <div className="awareness-body">
          <section>
            <div className="aw-card">
              <h3>
                Today
                <span>
                  {day?.tracked ? `${span(day.tracked)} tracked` : "nothing yet"}
                  {day?.switches ? ` · ${day.switches} switches` : ""}
                </span>
              </h3>
              <Rail sessions={sessions} />
              {!sessions.length ? (
                <p className="pad-block">
                  {state?.watching
                    ? "Nothing recorded yet — the first block appears once you stay in one place for a few seconds."
                    : "Not watching, so nothing was recorded."}
                </p>
              ) : null}
            </div>

            <div className="aw-card">
              <h3>
                Sessions <span>runs of the same app and context, merged</span>
              </h3>
              {sessions.length ? (
                sessions
                  .slice()
                  .reverse()
                  .map((session, i) => (
                    <div
                      key={i}
                      className="aw-row"
                      style={{ borderLeftColor: CATEGORY_COLOR[session.category] ?? CATEGORY_COLOR.other }}
                    >
                      <span className="when">
                        {clock(session.start)}–{session.live ? "now" : clock(session.end)}
                      </span>
                      <span className="app">{session.app}</span>
                      <span className="what">{session.context}</span>
                      <span className="dur">{span(session.seconds)}</span>
                    </div>
                  ))
              ) : null}
              <div className="brief-note">
                raw events are kept {state?.retention?.raw_days ?? 7} days · merged sessions{" "}
                {state?.retention?.session_days ?? 19} days · both local, in SQLite
              </div>
            </div>
          </section>

          <section>
            <div className="aw-card aw-ask">
              <h3>Ask</h3>
              {answer ? (
                <>
                  <p className="question">“{answer.question}”</p>
                  <p className="answer">{answer.answer}</p>
                  <div className="aw-chips">
                    <span className="aw-chip">read {answer.rows} rows</span>
                    <span className="aw-chip">no screenshots</span>
                    {answer.backend ? <span className="aw-chip">{answer.backend}</span> : null}
                  </div>
                </>
              ) : (
                <p className="answer">
                  Ask about any stretch of time — “what did I do between 9 and midnight last night”. The answer is
                  written from the rows on the left, and it says how many it read.
                </p>
              )}
              <form className="aw-ask-form" onSubmit={ask}>
                <input
                  ref={askRef}
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="what did I do this morning?  ( / )"
                  aria-label="Ask about your activity"
                />
                <button type="submit" className="primary-btn" disabled={asking}>
                  {asking ? "…" : "Ask"}
                </button>
              </form>
            </div>

            {coding ? (
              <div className="aw-card">
                <h3>
                  Coding · in progress <span>since {clock(coding.since)}</span>
                </h3>
                <div style={{ fontSize: 15, color: "#f2ece2", marginBottom: 6 }}>{coding.project}</div>
                {coding.files?.map((file) => (
                  <div key={file} className="detail-cmd" style={{ padding: "5px 0" }}>
                    <span className="n">·</span>
                    <span className="args">{file}</span>
                  </div>
                ))}
              </div>
            ) : null}

            {totals.length ? (
              <div className="aw-card">
                <h3>Where the time went</h3>
                {totals.map(([app, seconds]) => (
                  <div key={app} className="aw-meter">
                    <div className="top">
                      <span>{app}</span>
                      <b>{span(seconds)}</b>
                    </div>
                    <div className="bar">
                      <i style={{ width: `${(seconds / totals[0][1]) * 100}%`, background: "#ff8a65" }} />
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="aw-card">
              <h3>Kept for</h3>
              <div className="aw-meter">
                <div className="top">
                  <span>Raw focus events</span>
                  <b>{state?.retention?.raw_days ?? 7} days</b>
                </div>
                <div className="bar">
                  <i style={{ width: "33%", background: "#7c5cff" }} />
                </div>
              </div>
              <div className="aw-meter">
                <div className="top">
                  <span>Merged sessions</span>
                  <b>{state?.retention?.session_days ?? 19} days</b>
                </div>
                <div className="bar">
                  <i style={{ width: "78%", background: "#ff8a65" }} />
                </div>
              </div>
              <div className="aw-meter">
                <div className="top">
                  <span>Screenshots</span>
                  <b>not kept</b>
                </div>
                <div className="bar">
                  <i style={{ width: "4%", background: "rgba(255,255,255,0.3)" }} />
                </div>
              </div>
              <button type="button" className="ghost-btn danger" onClick={forget} style={{ color: "#ff8a8a" }}>
                Forget everything
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
