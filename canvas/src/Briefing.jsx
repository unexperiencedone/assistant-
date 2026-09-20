import { useCallback, useEffect, useState } from "react";

/**
 * The briefing panel: weather, markets and headlines, compiled by the agent when you
 * ask for it and never on a timer. Everything it shows carries the source it came
 * from and the time it was read; nothing it could not read is filled in from memory.
 *
 * It slides over the sidebar rather than taking a column of its own, so opening it
 * never reflows the entity or the graph.
 */

function clock(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function ago(ts) {
  if (!ts) return "";
  const minutes = Math.round((Date.now() / 1000 - ts) / 60);
  if (minutes < 60) return `${Math.max(1, minutes)} min old`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h old` : `${Math.round(hours / 24)} d old`;
}

/** Daily closes, drawn small. Not a live feed — the shape of the last few sessions. */
function Spark({ points, color }) {
  if (!Array.isArray(points) || points.length < 2) return null;
  const low = Math.min(...points);
  const high = Math.max(...points);
  const span = high - low || 1;
  const step = 70 / (points.length - 1);
  const path = points.map((value, i) => `${(i * step).toFixed(1)},${(16 - ((value - low) / span) * 14).toFixed(1)}`);
  return (
    <svg width="70" height="18" viewBox="0 0 70 18" fill="none" aria-hidden="true">
      <polyline points={path.join(" ")} stroke={color} strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

const TONE = { up: "up", down: "down" };

function Market({ row }) {
  const tone = TONE[row.direction] ?? "flat";
  const color = tone === "up" ? "#a6f5cf" : tone === "down" ? "#ff8a8a" : "#9a92aa";
  return (
    <div className="brief-row">
      <span className="name">{row.name}</span>
      <Spark points={row.points} color={color} />
      <span className={`value ${tone}`}>{row.change}</span>
    </div>
  );
}

function Headline({ item }) {
  return (
    <div className="brief-card">
      <div className="title">{item.title}</div>
      {item.summary ? <div className="summary">{item.summary}</div> : null}
      {item.url ? (
        <a className="brief-src" href={item.url} target="_blank" rel="noreferrer">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M14 4h6v6M20 4l-9 9M9 5H5v14h14v-4" />
          </svg>
          {item.source || new URL(item.url).hostname.replace(/^www\./, "")}
          {item.at ? ` · ${clock(item.at)}` : ""}
        </a>
      ) : (
        <span className="brief-src">{item.source || "source unknown"}</span>
      )}
    </div>
  );
}

export default function Briefing({ onClose, revision, onAsk, width, leftEdge }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [asking, setAsking] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/ambient", { cache: "no-store" });
      if (!response.ok) throw new Error(`${response.status}`);
      setData(await response.json());
      setError("");
    } catch (e) {
      // Nothing cached and nothing reachable is simply the empty state below.
      setError(`Couldn't read the cache: ${e.message}`);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, revision]);

  const refresh = async () => {
    setAsking(true);
    try {
      await fetch("/api/ambient/refresh", { method: "POST" });
      if (onAsk) onAsk();
    } catch {
      /* the agent turn reports its own failure in the activity feed */
    }
    setTimeout(() => setAsking(false), 1500);
  };

  const working = data?.status === "working" || asking;
  const markets = data?.markets ?? [];
  const headlines = data?.headlines ?? [];
  const weather = data?.weather;
  const empty = !weather && !markets.length && !headlines.length;
  const stale = data?.updated_at ? Date.now() / 1000 - data.updated_at > 6 * 3600 : false;

  return (
    <aside className="briefing" aria-label="Briefing" style={width ? { width } : undefined}>
      {leftEdge}
      <header className="briefing-head">
        <span className="label">Briefing</span>
        <span className="when">
          {data?.updated_at ? `${clock(data.updated_at)} · ${data.sources_read ?? 0} pages` : "never asked"}
          <button type="button" className="close" onClick={onClose} aria-label="Close briefing">
            ×
          </button>
        </span>
      </header>

      <div className="briefing-body">
        {error && (data?.markets?.length || data?.headlines?.length) ? (
          <div className="brief-stale">{error}</div>
        ) : null}

        {working ? (
          <div>
            <h3>Reading</h3>
            {(data?.reading ?? []).map((source, i) => (
              <div key={i} className="brief-working">
                <i className="dot" />
                <span>{source}</span>
              </div>
            ))}
            {!(data?.reading ?? []).length ? (
              <div className="brief-working">
                <i className="dot" />
                <span>asking the agent to read and compile…</span>
              </div>
            ) : null}
          </div>
        ) : null}

        {empty && !working ? (
          <div className="brief-empty">
            <p>Nothing is fetched in the background.</p>
            <p className="small">
              Ask for a briefing and Nova reads the pages once, cites them, and stops. No polling, no timer, nothing
              running while this sits here.
            </p>
            <button type="button" className="primary-btn" onClick={refresh}>
              Brief me
            </button>
          </div>
        ) : null}

        {stale && !working ? (
          <div className="brief-stale">as of {clock(data.updated_at)} · {ago(data.updated_at)}</div>
        ) : null}

        {weather ? (
          <div>
            <h3>Now</h3>
            <div className="brief-row">
              <span className="name">{weather.place}</span>
              <span className="value flat">{weather.temp}</span>
            </div>
            {weather.text ? <div className="brief-note">{weather.text}</div> : null}
          </div>
        ) : null}

        {markets.length ? (
          <div>
            <h3>Markets</h3>
            {markets.map((row, i) => (
              <Market key={i} row={row} />
            ))}
            <div className="brief-note">daily closes · fetched on ask, never polled</div>
          </div>
        ) : null}

        {headlines.length ? (
          <div>
            <h3>Top headlines</h3>
            {headlines.map((item, i) => (
              <Headline key={i} item={item} />
            ))}
          </div>
        ) : null}
      </div>

      <div className="briefing-foot">
        <button type="button" className="ghost-btn" onClick={refresh} disabled={working}>
          {working ? "Working…" : "Refresh"}
        </button>
        <span className="cost">costs one agent turn</span>
      </div>
    </aside>
  );
}
