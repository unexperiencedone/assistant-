import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import FlowCanvas from "./FlowCanvas.jsx";
import Inspector from "./Inspector.jsx";

// Work history: timed sessions (assistant/history). A session is one stretch of activity;
// it ends after the configured idle time, never while a task is running.

async function getJson(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

const pad = (n) => String(n).padStart(2, "0");
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function duration(seconds) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  const h = Math.floor(minutes / 60);
  return h ? `${h} h ${pad(minutes % 60)} min` : `${minutes} min`;
}

function dayLabel(ts) {
  const date = new Date(ts * 1000);
  const today = new Date();
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(today) - startOf(date)) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short",
    year: date.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function sessionEnd(s) {
  return s.ended_at ?? s.last_activity;
}

/** Events -> a readable timeline: messages, and one card per request with its actions and result. */
function buildTimeline(events) {
  const items = [];
  const open = new Map(); // task id -> its request card
  for (const e of events) {
    if (e.kind === "nova") {
      // Nova reading out a result that its card already shows: say it once.
      const card = [...items].reverse().find((i) => i.type === "request");
      const spoken = e.text.trim();
      if (card?.end && e.ts - card.end.ts < 10 && spoken && card.end.text.startsWith(spoken)) continue;
    }
    if (e.kind === "you" || e.kind === "nova" || e.kind === "plan" || e.kind === "log") {
      items.push({ type: e.kind, event: e });
      continue;
    }
    const key = e.task || "";
    if (e.kind === "request") {
      const previous = open.get("") ?? null;
      // A local command that fell through to the agent: same words, moments apart, one request.
      if (previous && !previous.end && previous.request.text === e.text && e.ts - previous.request.ts < 5) {
        open.delete("");
        open.set(key, previous);
        continue;
      }
      const card = { type: "request", request: e, backend: e.backend, actions: [], end: null };
      // What you said becomes the card's title: don't show it twice.
      const last = items[items.length - 1];
      if (last?.type === "you" && last.event.text === e.text && e.ts - last.event.ts < 5) items.pop();
      items.push(card);
      open.set(key, card);
      continue;
    }
    let card = open.get(key);
    if (e.kind === "start" && (!card || card.end)) {
      card = { type: "request", request: e, backend: e.backend, actions: [], end: null }; // e.g. running a plan
      items.push(card);
      open.set(key, card);
    }
    if (!card) {
      items.push({ type: "other", event: e });
    } else if (e.kind === "start") {
      card.backend = e.backend;
    } else if (e.kind === "tool") {
      card.actions.push(e);
    } else if (e.kind === "result" || e.kind === "error") {
      card.end = e;
    }
  }
  return items;
}

/* ---------------------------------------------------------------- pieces */

function SessionRow({ session, active, onClick }) {
  return (
    <button type="button" className={`session-row${active ? " on" : ""}`} onClick={onClick}>
      <span className="session-time">
        {session.live ? <i className="live-dot" title="Session in progress" /> : null}
        {clock(session.started_at)} – {session.live ? "now" : clock(sessionEnd(session))}
        <span className="muted"> · {duration(sessionEnd(session) - session.started_at)}</span>
      </span>
      <span className="session-title">{session.title || "Untitled session"}</span>
      <span className="session-meta">
        {session.requests} request{session.requests === 1 ? "" : "s"}
        {session.failures ? <span className="bad"> · {session.failures} failed</span> : null}
        {session.cost_usd ? ` · $${session.cost_usd.toFixed(2)}` : ""}
        {session.projects.map((p) => (
          <span key={p} className="project-tag">
            {p}
          </span>
        ))}
      </span>
    </button>
  );
}

function ActionRow({ action }) {
  const [open, setOpen] = useState(false);
  const long = (action.text || "").length > 140;
  return (
    <li className={`hist-action${open ? " open" : ""}`}>
      <span className="t">{clock(action.ts)}</span>
      <span className="hist-tool">{action.tool || action.backend}</span>
      <button type="button" className="hist-text" onClick={() => long && setOpen((v) => !v)} disabled={!long}>
        {action.text || "(no arguments)"}
      </button>
    </li>
  );
}

function RequestCard({ card, live }) {
  const [expanded, setExpanded] = useState(false);
  const { request, end, actions } = card;
  const status = end ? (end.kind === "result" ? "done" : "failed") : live ? "running" : "local";
  const seconds = end ? end.ts - request.ts : null;
  return (
    <article className={`request-card status-${status}`}>
      <header>
        <span className={`dot dot-${status}`} />
        <div>
          <p className="request-text">{request.text || "(request)"}</p>
          <p className="muted request-meta">
            {clock(request.ts)}
            {card.backend ? ` · ${card.backend}` : ""}
            {seconds != null ? ` · ${seconds < 60 ? `${Math.round(seconds)} s` : duration(seconds)}` : ""}
            {status === "failed" ? " · failed" : status === "running" ? " · running" : ""}
          </p>
        </div>
      </header>
      {actions.length ? (
        <>
          <button type="button" className="link" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
            {expanded ? "Hide" : "Show"} {actions.length} action{actions.length === 1 ? "" : "s"}
          </button>
          {expanded ? (
            <ol className="hist-actions">
              {actions.map((a) => (
                <ActionRow key={a.id} action={a} />
              ))}
            </ol>
          ) : null}
        </>
      ) : null}
      {end?.text ? <p className={`request-result${end.kind === "error" ? " bad" : ""}`}>{end.text}</p> : null}
    </article>
  );
}

function Timeline({ events, live }) {
  const items = useMemo(() => buildTimeline(events), [events]);
  if (!items.length) return <p className="muted pad-block">Nothing recorded in this session.</p>;
  return (
    <div className="timeline">
      {items.map((item, i) => {
        if (item.type === "request") return <RequestCard key={`r${item.request.id}`} card={item} live={live} />;
        const e = item.event;
        if (item.type === "you" || item.type === "nova") {
          return (
            <div key={e.id} className={`msg msg-${item.type === "you" ? "user" : "assistant"}`}>
              <span className="muted msg-time">{clock(e.ts)}</span>
              {e.text}
            </div>
          );
        }
        if (item.type === "plan") {
          return (
            <details key={e.id} className="plan-draft">
              <summary>
                Plan drafted: <b>{e.text || "untitled"}</b> <span className="muted">· {clock(e.ts)}</span>
              </summary>
              <ol>
                {(e.data?.steps ?? []).map((s, j) => (
                  <li key={j}>{s.text}</li>
                ))}
              </ol>
            </details>
          );
        }
        return (
          <p key={e.id ?? i} className={`hist-log${item.type === "log" ? " warn" : ""}`}>
            <span className="muted">{clock(e.ts)}</span> {e.text}
          </p>
        );
      })}
    </div>
  );
}

function Runs({ runs }) {
  const [runId, setRunId] = useState(runs.length ? runs[runs.length - 1].id : null);
  const [graph, setGraph] = useState(null);
  const [selected, setSelected] = useState(null);
  useEffect(() => {
    setSelected(null);
    if (runId == null) return undefined;
    let cancelled = false;
    getJson(`/api/history/runs/${runId}`).then((g) => !cancelled && setGraph(g)).catch(() => !cancelled && setGraph(null));
    return () => {
      cancelled = true;
    };
  }, [runId]);
  if (!runs.length) return <p className="muted pad-block">No finished requests with a graph in this session.</p>;
  return (
    <div className="runs">
      <div className="history run-strip" aria-label="Requests in this session">
        {runs.map((r) => (
          <button
            key={r.id}
            type="button"
            className={`${r.id === runId ? "active" : ""} run-${r.status}`}
            title={`${r.label} · ${clock(r.ts)}`}
            onClick={() => setRunId(r.id)}
          >
            {r.label || "request"}
          </button>
        ))}
      </div>
      <div className={`run-view${selected ? " with-inspector" : ""}`}>
        <section className="canvas">
          <FlowCanvas graph={graph} onSelect={setSelected} selectedId={selected} onMoveStep={null} />
        </section>
        {selected ? (
          <Inspector
            nodeId={selected}
            source={`/api/history/runs/${runId}`}
            onClose={() => setSelected(null)}
            say={null}
            planSteps={0}
          />
        ) : null}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- screen */

export default function History({ onClose, live }) {
  const [sessions, setSessions] = useState([]);
  const [more, setMore] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState("timeline");
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const searchRef = useRef(null);

  const loadList = useCallback(async (q, before = null) => {
    const params = new URLSearchParams({ limit: "60" });
    if (q.trim()) params.set("q", q.trim());
    if (before != null) params.set("before", String(before));
    const result = await getJson(`/api/history/sessions?${params}`);
    setEnabled(result.enabled);
    setMore(result.more);
    setSessions((current) => (before == null ? result.sessions : [...current, ...result.sessions]));
    return result.sessions;
  }, []);

  // The list: on open, on search (debounced) and whenever the recorder reports a change.
  useEffect(() => {
    const id = setTimeout(() => {
      loadList(query)
        .then((list) => {
          setError("");
          setSelectedId((current) => current ?? list[0]?.id ?? null);
        })
        .catch((e) => setError(`Couldn't load the history: ${e.message}`));
    }, query ? 300 : 0);
    return () => clearTimeout(id);
  }, [query, live?.revision, loadList]);

  const selected = sessions.find((s) => s.id === selectedId) ?? detail?.session ?? null;

  // The open session's detail; a live one refreshes as it changes.
  useEffect(() => {
    setConfirmDelete(false);
    if (selectedId == null) {
      setDetail(null);
      return undefined;
    }
    let cancelled = false;
    getJson(`/api/history/sessions/${selectedId}`)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null));
    return () => {
      cancelled = true;
    };
  }, [selectedId, selectedId === live?.current?.id ? live?.revision : null]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const remove = async () => {
    try {
      await getJson(`/api/history/sessions/${selectedId}`, { method: "DELETE" });
      const remaining = sessions.filter((s) => s.id !== selectedId);
      setSessions(remaining);
      setSelectedId(remaining[0]?.id ?? null);
    } catch (e) {
      setError(`Couldn't delete: ${e.message}`);
    }
    setConfirmDelete(false);
  };

  const groups = useMemo(() => {
    const byDay = [];
    for (const s of sessions) {
      const label = dayLabel(s.started_at);
      const last = byDay[byDay.length - 1];
      if (last?.label === label) last.sessions.push(s);
      else byDay.push({ label, sessions: [s] });
    }
    return byDay;
  }, [sessions]);

  const s = detail?.session;

  return (
    <div className="history-screen">
      <header className="profile-bar">
        <div>
          <h2>History</h2>
          <p className="muted">
            Each session is one stretch of work. It ends after a while with nothing happening, never mid-task.
          </p>
        </div>
        <div className="profile-bar-actions">
          <input
            ref={searchRef}
            className="history-search"
            type="search"
            value={query}
            placeholder="Search everything said and done  ( / )"
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="button" className="close" onClick={onClose} aria-label="Close history">
            ×
          </button>
        </div>
      </header>
      {error ? <div className="banner banner-denied">{error}</div> : null}

      <div className="history-body">
        <nav className="session-list" aria-label="Sessions">
          {!enabled ? (
            <p className="muted pad-block">History is turned off: set enabled = true under [history] in config.toml.</p>
          ) : !sessions.length ? (
            <p className="muted pad-block">
              {query ? `No session mentions "${query}".` : "No sessions yet. Each stretch of work with Nova shows up here."}
            </p>
          ) : (
            groups.map((g) => (
              <section key={g.label}>
                <h3>{g.label}</h3>
                {g.sessions.map((session) => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    active={session.id === selectedId}
                    onClick={() => setSelectedId(session.id)}
                  />
                ))}
              </section>
            ))
          )}
          {more ? (
            <button type="button" className="add load-more" onClick={() => loadList(query, sessions[sessions.length - 1].started_at)}>
              Load older sessions
            </button>
          ) : null}
        </nav>

        <section className="session-detail" aria-label="Session">
          {!s ? (
            <p className="muted pad-block">{selected ? "Loading…" : "Pick a session."}</p>
          ) : (
            <>
              <header className="session-head">
                <div className="session-head-main">
                  <p className="muted">
                    {s.live ? <i className="live-dot" /> : null}
                    {dayLabel(s.started_at)}, {clock(s.started_at)} – {s.live ? "now" : clock(sessionEnd(s))} ·{" "}
                    {duration(sessionEnd(s) - s.started_at)}
                    {s.live ? " · in progress" : ""}
                  </p>
                  <h2>{s.title || "Untitled session"}</h2>
                  <div className="session-stats">
                    <span className="chip">
                      <b>{s.requests}</b> request{s.requests === 1 ? "" : "s"}
                    </span>
                    <span className="chip">
                      <b>{s.actions}</b> action{s.actions === 1 ? "" : "s"}
                    </span>
                    {s.failures ? (
                      <span className="chip bad">
                        <b>{s.failures}</b> failed
                      </span>
                    ) : null}
                    {s.cost_usd ? (
                      <span className="chip">
                        <b>${s.cost_usd.toFixed(3)}</b>
                      </span>
                    ) : null}
                    {s.backends.length ? <span className="chip">{s.backends.join(", ")}</span> : null}
                    {s.projects.map((p) => (
                      <span key={p} className="project-tag">
                        {p}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="session-head-actions">
                  {confirmDelete ? (
                    <>
                      <span className="muted">Delete this session for good?</span>
                      <button type="button" onClick={() => setConfirmDelete(false)}>
                        Keep
                      </button>
                      <button type="button" className="danger" onClick={remove}>
                        Delete
                      </button>
                    </>
                  ) : (
                    <button type="button" className="danger" onClick={() => setConfirmDelete(true)}>
                      Delete
                    </button>
                  )}
                </div>
              </header>

              {detail.files.length ? (
                <details className="files-changed">
                  <summary>
                    {detail.files.length} file{detail.files.length === 1 ? "" : "s"} changed
                  </summary>
                  <ul>
                    {detail.files.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                </details>
              ) : null}

              <div className="preview-tabs session-tabs" role="tablist">
                <button type="button" role="tab" aria-selected={tab === "timeline"} className={tab === "timeline" ? "on" : ""} onClick={() => setTab("timeline")}>
                  Timeline
                </button>
                <button type="button" role="tab" aria-selected={tab === "runs"} className={tab === "runs" ? "on" : ""} onClick={() => setTab("runs")}>
                  Graphs ({detail.runs.length})
                </button>
              </div>
              {detail.truncated ? (
                <p className="muted pad-block">This session is very long: only its first 5000 events are shown.</p>
              ) : null}
              {tab === "timeline" ? (
                <Timeline events={detail.events} live={s.live} />
              ) : (
                <Runs key={s.id} runs={detail.runs} />
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
