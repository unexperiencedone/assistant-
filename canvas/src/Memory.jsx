import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * AfterThought: what Nova remembers, what it brought up, and what it may watch
 * (assistant/afterthought, docs/afterthought.md).
 *
 * Every long-term entry is listed here and can be forgotten one by one. Apps are
 * watched only once switched on below; the denylist (password managers, banking,
 * private windows) is enforced before anything is read, whatever this screen says.
 */

const clock = (ts) =>
  ts ? new Date(ts * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";

async function getJson(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

const post = (path, body) =>
  getJson(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

const OUTCOME_COLOR = { accepted: "var(--done)", dismissed: "var(--failed)", shown: "var(--accent)" };

function Sources({ sources }) {
  if (!sources?.length) return null;
  return (
    <div className="aw-chips mem-sources">
      {sources.slice(0, 4).map((source, i) => (
        <span key={i} className="aw-chip" title={source.title}>
          {source.app || source.kind} · {(source.title || "").slice(0, 40)} · {clock(source.at)}
        </span>
      ))}
      {sources.length > 4 ? <span className="aw-chip">+{sources.length - 4} more</span> : null}
    </div>
  );
}

export default function Memory({ onClose, revision }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState(null);
  const [searching, setSearching] = useState(false);
  const [filter, setFilter] = useState("");
  const askRef = useRef(null);

  const load = useCallback(async () => {
    try {
      setDoc(await getJson("/api/afterthought"));
      setError("");
    } catch (e) {
      setError(e.message.startsWith("404") ? "" : `Couldn't read the memory store: ${e.message}`);
      if (e.message.startsWith("404")) setDoc({ enabled: false });
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

  const act = async (path, body, message) => {
    try {
      const result = await post(path, body);
      setNote(result?.text || message || "");
      load();
    } catch (e) {
      setError(`Couldn't do that: ${e.message}`);
    }
  };

  const search = async (event) => {
    event.preventDefault();
    if (!query.trim() || searching) return;
    setSearching(true);
    try {
      setHits((await post("/api/afterthought/recall", { query: query.trim() })).hits);
    } catch (e) {
      setError(`Couldn't search: ${e.message}`);
    }
    setSearching(false);
  };

  const forgetAll = () => {
    if (!window.confirm("Forget every memory, short- and long-term? This cannot be undone.")) return;
    act("/api/afterthought/forget", { all: true });
  };

  const longTerm = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const items = doc?.long ?? [];
    if (!needle) return items;
    return items.filter((m) =>
      [m.title, m.text, ...(m.entities ?? [])].some((field) => (field || "").toLowerCase().includes(needle)),
    );
  }, [doc, filter]);

  if (doc && doc.enabled === false) {
    return (
      <div className="awareness">
        <header className="profile-bar">
          <div>
            <h2>Memory</h2>
          </div>
          <button type="button" className="close" onClick={onClose} aria-label="Close memory">
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="2.4" strokeLinecap="square" /></svg>
          </button>
        </header>
        <div className="aw-off">
          <p>
            AfterThought is turned off. Set <span className="mono">enabled = true</span> under{" "}
            <span className="mono">[afterthought]</span> in config.toml, then restart Nova.
          </p>
        </div>
      </div>
    );
  }

  const paused = doc?.paused;
  const watched = (doc?.apps ?? []).filter((a) => a.watched).length;

  return (
    <div className="awareness memory">
      <header className="profile-bar">
        <div>
          <h2>What I remember</h2>
          <p className="mono">
            on this PC only · apps you switch on below · passwords, banking and private windows never read
          </p>
        </div>
        <div className="profile-bar-actions">
          {paused ? (
            <>
              <span className="save-state">suggestions paused until {clock(doc.paused_until)}</span>
              <button type="button" onClick={() => act("/api/afterthought/pause", { seconds: 0 }, "Suggestions are back on.")}>
                Resume
              </button>
            </>
          ) : (
            <>
              {doc?.ready ? (
                <span className="save-state" style={{ color: "var(--done)" }}>
                  <i className="live-dot" />
                  {doc?.watch_all
                    ? `watching every app${doc.excluded?.length ? ` except ${doc.excluded.join(", ")}` : ""}`
                    : watched
                      ? `watching ${watched} app${watched === 1 ? "" : "s"}`
                      : "watching nothing yet"}
                </span>
              ) : (
                <span className="save-state">loading the model…</span>
              )}
              <button type="button" onClick={() => act("/api/afterthought/pause", { seconds: 3600 }, "Paused for an hour.")}>
                Pause 1 h
              </button>
            </>
          )}
          <button type="button" className="close" onClick={onClose} aria-label="Close memory">
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="2.4" strokeLinecap="square" /></svg>
          </button>
        </div>
      </header>
      {error ? <div className="banner banner-denied">{error}</div> : null}
      {note ? (
        <div className="banner mem-note" role="status">
          <span>{note}</span>
          <button type="button" className="banner-close" onClick={() => setNote("")} aria-label="Dismiss">
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="2.4" strokeLinecap="square" /></svg>
          </button>
        </div>
      ) : null}

      <div className="awareness-body">
        <section>
          <div className="aw-card">
            <h3>
              Brought up <span>only above similarity {doc?.threshold ?? "—"} · your answer moves the ranking</span>
            </h3>
            {(doc?.suggestions ?? []).length ? (
              doc.suggestions.map((s) => (
                <div key={s.id} className="mem-suggestion" style={{ borderLeftColor: OUTCOME_COLOR[s.outcome] }}>
                  <p className="mem-said">{s.snippet}</p>
                  <div className="mem-meta">
                    <span>{s.source}</span>
                    <span>{s.when}</span>
                    <span>similarity {s.similarity.toFixed(2)}</span>
                    <span>{clock(s.at)}</span>
                    {s.spoken ? <span>spoken</span> : null}
                  </div>
                  {s.outcome === "shown" ? (
                    <div className="mem-actions">
                      <button type="button" className="primary" onClick={() => act("/api/afterthought/answer", { id: s.id, useful: true })}>
                        Useful
                      </button>
                      <button type="button" onClick={() => act("/api/afterthought/answer", { id: s.id, useful: false })}>
                        Not useful
                      </button>
                    </div>
                  ) : (
                    <span className="mem-outcome" style={{ color: OUTCOME_COLOR[s.outcome] }}>
                      {s.outcome}
                    </span>
                  )}
                </div>
              ))
            ) : (
              <p className="pad-block">
                Nothing yet. Silence is the default: something comes up only when what is in front of you clearly
                connects to something you did before.
              </p>
            )}
          </div>

          <div className="aw-card">
            <h3>
              Long-term <span>{doc?.long?.length ?? 0} entries · consolidated nightly · {clock(doc?.consolidated_at)}</span>
            </h3>
            <div className="aw-ask-form mem-filter">
              <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter by word or name" />
            </div>
            {longTerm.length ? (
              longTerm.map((m) => (
                <div key={m.id} className="mem-entry">
                  <div className="mem-entry-top">
                    <b>{m.title}</b>
                    <span className="mono">
                      {m.kind} · refs {m.refs} · ✓{m.accepted} ✗{m.dismissed}
                    </span>
                    <button type="button" className="mem-forget" onClick={() => act("/api/afterthought/forget", { id: m.id })}>
                      Forget
                    </button>
                  </div>
                  <p className="mem-text">{m.text}</p>
                  {m.entities?.length ? (
                    <div className="aw-chips">
                      {m.entities.slice(0, 8).map((tag) => (
                        <span key={tag} className="aw-chip mem-tag">
                          {tag}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <Sources sources={m.sources} />
                </div>
              ))
            ) : (
              <p className="pad-block">
                {filter ? "No entry matches." : "Empty. Say “remember this” to keep something now; the nightly pass adds the rest."}
              </p>
            )}
          </div>

          <div className="aw-card">
            <h3>
              Recent <span>raw chunks · expire after 7 days unless referenced twice</span>
            </h3>
            {(doc?.short ?? []).length ? (
              doc.short.map((m) => (
                <div key={m.id} className="aw-row">
                  <span className="when">{clock(m.start)}</span>
                  <span className="app">{m.app}</span>
                  <span className="what" title={m.text}>
                    {m.context || m.title}
                  </span>
                  <button type="button" className="mem-forget" onClick={() => act("/api/afterthought/forget", { id: m.id })}>
                    Forget
                  </button>
                </div>
              ))
            ) : (
              <p className="pad-block">Nothing recent. Switch an app on, on the right, and work in it for a bit.</p>
            )}
          </div>
        </section>

        <section>
          <div className="aw-card aw-ask">
            <h3>Ask your memory</h3>
            <form className="aw-ask-form" onSubmit={search}>
              <input
                ref={askRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="the payments rollout date"
              />
              <button type="submit" className="primary" disabled={searching}>
                {searching ? "…" : "Search"}
              </button>
            </form>
            {hits ? (
              hits.length ? (
                hits.map((h) => (
                  <div key={h.id} className="mem-hit">
                    <p className="answer">{h.focus}</p>
                    <div className="mem-meta">
                      <span>{h.title}</span>
                      <span>{h.tier}</span>
                      <span>similarity {h.similarity.toFixed(2)}</span>
                    </div>
                    <Sources sources={h.sources} />
                  </div>
                ))
              ) : (
                <p className="answer">Nothing about that.</p>
              )
            ) : (
              <p className="answer">Or say “what do you remember about …”. Each answer comes with where it came from.</p>
            )}
          </div>

          <div className="aw-card">
            <h3>
              What I watch <span>{doc?.watch_all ? "every app, except the ones below" : "off unless switched on"}</span>
            </h3>
            {(doc?.apps ?? []).length ? (
              doc.apps.map((app) => (
                <label key={app.name} className="mem-app">
                  <span>{app.name}</span>
                  <input
                    type="checkbox"
                    disabled={Boolean(doc?.watch_all)}
                    checked={app.watched}
                    onChange={(event) => act("/api/afterthought/consent", { app: app.name, on: event.target.checked })}
                  />
                </label>
              ))
            ) : (
              <p className="pad-block">Apps appear here as you use them. Or say “remember what I do in Chrome”.</p>
            )}
            <div className="brief-note">
              window titles, the focused window's text (accessibility) and what you copy, from switched-on apps only ·
              secrets stripped before anything is kept
            </div>
          </div>

          <div className="aw-card">
            <h3>Under the hood</h3>
            <div className="mem-stats mono">
              <span>model</span>
              <b>{doc?.model ?? "—"}</b>
              <span>short-term</span>
              <b>{doc?.short_count ?? doc?.short?.length ?? 0}</b>
              <span>long-term</span>
              <b>{doc?.long?.length ?? 0}</b>
              <span>gate</span>
              <b>cosine ≥ {doc?.threshold ?? "—"}</b>
              <span>network calls</span>
              <b>0</b>
            </div>
            <div className="mem-actions">
              <button type="button" onClick={() => act("/api/afterthought/consolidate", {}, "Consolidated.")}>
                Consolidate now
              </button>
              <button type="button" className="danger" onClick={forgetAll}>
                Forget everything
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
