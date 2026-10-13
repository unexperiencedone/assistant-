import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

/**
 * Tokens spent and saved (assistant/usage). Spent is measured from each provider's own
 * counts; saved is an estimate whose method is printed at the bottom of the screen.
 *
 * Series colours are the dataviz reference's dark steps, validated against this surface
 * (#12101a) for the stacked order below: Claude, Antigravity, free models, scripted.
 */

const WHO = [
  { key: "claude", label: "Claude", color: "#d95926" },
  { key: "antigravity", label: "Antigravity", color: "#3987e5" },
  { key: "free_model", label: "Free models", color: "#199e70" },
  { key: "scripted", label: "Scripted", color: "#c98500" },
];
const SPENT = "#d95926";
const SAVED = "#199e70";
const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 0, label: "All" },
];

const usd = (v) => (!v ? "$0" : v >= 100 ? `$${v.toFixed(0)}` : v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(3)}`);
function tok(v) {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(Math.round(v));
}
const when = (ts) =>
  new Date(ts * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const shortDay = (day) => new Date(`${day}T00:00`).toLocaleDateString([], { day: "numeric", month: "short" });

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(600);
  useLayoutEffect(() => {
    if (!ref.current) return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

// A rounded top on a bar whose bottom sits on the baseline: 4px data-end, square base.
function barPath(x, y, w, h, r = 4) {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * exp).find((m) => m >= v);
}

function Tooltip({ tip }) {
  if (!tip) return null;
  return (
    <div className="us-tip" style={{ left: tip.x, top: tip.y }}>
      <b>{tip.title}</b>
      {tip.rows.map(([label, value, color]) => (
        <div key={label}>
          {color ? <i style={{ background: color }} /> : null}
          <span>{label}</span>
          <em>{value}</em>
        </div>
      ))}
    </div>
  );
}

function Legend({ items }) {
  return (
    <div className="aw-legend">
      {items.map((s) => (
        <span key={s.label}>
          <i style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

/** Requests per day, stacked by who answered them. */
function WhoChart({ daily }) {
  const [ref, width] = useWidth();
  const [tip, setTip] = useState(null);
  const height = 170;
  const pad = { l: 30, r: 4, t: 8, b: 20 };
  const max = niceMax(Math.max(1, ...daily.map((d) => WHO.reduce((n, s) => n + d[s.key], 0))));
  const step = (width - pad.l - pad.r) / Math.max(1, daily.length);
  const bw = Math.max(3, Math.min(26, step - 2));
  const y = (v) => pad.t + (height - pad.t - pad.b) * (1 - v / max);
  const labelEvery = Math.ceil(daily.length / Math.max(1, Math.floor(width / 70)));

  return (
    <div ref={ref} className="us-chart">
      <svg width={width} height={height} role="img" aria-label="Requests per day by who answered them">
        {[0, max / 2, max].map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={width - pad.r} y1={y(v)} y2={y(v)} className="us-grid" />
            <text x={pad.l - 6} y={y(v) + 3} className="us-axis" textAnchor="end">
              {v}
            </text>
          </g>
        ))}
        {daily.map((d, i) => {
          const x = pad.l + i * step + (step - bw) / 2;
          let base = 0;
          const total = WHO.reduce((n, s) => n + d[s.key], 0);
          const parts = WHO.filter((s) => d[s.key] > 0);
          return (
            <g key={d.day}>
              {parts.map((s, j) => {
                const top = y(base + d[s.key]);
                const bottom = y(base);
                base += d[s.key];
                const h = Math.max(0, bottom - top - (j > 0 ? 2 : 0)); // 2px surface gap between segments
                return j === parts.length - 1 ? (
                  <path key={s.key} d={barPath(x, top, bw, h)} fill={s.color} />
                ) : (
                  <rect key={s.key} x={x} y={top} width={bw} height={h} fill={s.color} />
                );
              })}
              <rect
                x={pad.l + i * step}
                y={pad.t}
                width={step}
                height={height - pad.t - pad.b}
                fill="transparent"
                onMouseEnter={(e) =>
                  setTip({
                    x: Math.min(pad.l + i * step + step / 2, width - 150),
                    y: 6,
                    title: `${shortDay(d.day)} · ${total} request${total === 1 ? "" : "s"}`,
                    rows: WHO.map((s) => [s.label, d[s.key], s.color]),
                  })
                }
                onMouseLeave={() => setTip(null)}
              />
              {i % labelEvery === 0 ? (
                <text x={x + bw / 2} y={height - 5} className="us-axis" textAnchor="middle">
                  {shortDay(d.day)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

/** Dollars per day: what Claude used against what the cheaper routes probably saved. One axis. */
function MoneyChart({ daily }) {
  const [ref, width] = useWidth();
  const [tip, setTip] = useState(null);
  const height = 150;
  const pad = { l: 40, r: 4, t: 8, b: 20 };
  const max = niceMax(Math.max(0.01, ...daily.flatMap((d) => [d.spent_usd, d.saved_usd])));
  const step = (width - pad.l - pad.r) / Math.max(1, daily.length);
  const bw = Math.max(2, Math.min(12, (step - 4) / 2));
  const y = (v) => pad.t + (height - pad.t - pad.b) * (1 - v / max);
  const labelEvery = Math.ceil(daily.length / Math.max(1, Math.floor(width / 70)));

  return (
    <div ref={ref} className="us-chart">
      <svg width={width} height={height} role="img" aria-label="Claude usage and estimated savings per day, in dollars">
        {[0, max / 2, max].map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={width - pad.r} y1={y(v)} y2={y(v)} className="us-grid" />
            <text x={pad.l - 6} y={y(v) + 3} className="us-axis" textAnchor="end">
              {usd(v)}
            </text>
          </g>
        ))}
        {daily.map((d, i) => {
          const centre = pad.l + i * step + step / 2;
          return (
            <g key={d.day}>
              <path d={barPath(centre - bw - 1, y(d.spent_usd), bw, y(0) - y(d.spent_usd))} fill={SPENT} />
              <path d={barPath(centre + 1, y(d.saved_usd), bw, y(0) - y(d.saved_usd))} fill={SAVED} />
              <rect
                x={pad.l + i * step}
                y={pad.t}
                width={step}
                height={height - pad.t - pad.b}
                fill="transparent"
                onMouseEnter={() =>
                  setTip({
                    x: Math.min(centre, width - 150),
                    y: 6,
                    title: shortDay(d.day),
                    rows: [
                      ["Claude used", usd(d.spent_usd), SPENT],
                      ["Saved (est.)", usd(d.saved_usd), SAVED],
                      ["Tokens", tok(d.tokens)],
                    ],
                  })
                }
                onMouseLeave={() => setTip(null)}
              />
              {i % labelEvery === 0 ? (
                <text x={centre} y={height - 5} className="us-axis" textAnchor="middle">
                  {shortDay(d.day)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

function Tile({ label, value, sub, accent }) {
  return (
    <div className="us-tile">
      <span className="label">{label}</span>
      <b style={accent ? { color: accent } : null}>{value}</b>
      <span className="sub">{sub}</span>
    </div>
  );
}

export default function Usage({ onClose, revision }) {
  const [days, setDays] = useState(() => {
    try {
      return Number(localStorage.getItem("nova.usage.days") ?? 30);
    } catch {
      return 30;
    }
  });
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [showTable, setShowTable] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/usage?days=${days}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      setData(await response.json());
      setError("");
    } catch (e) {
      setError(`Couldn't read the usage ledger: ${e.message}`);
    }
  }, [days]);

  useEffect(() => {
    load();
  }, [load, revision]);

  useEffect(() => {
    try {
      localStorage.setItem("nova.usage.days", String(days));
    } catch {
      /* private window: the range just isn't remembered */
    }
  }, [days]);

  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const spent = data?.spent;
  const saved = data?.saved;
  const base = data?.baseline;
  const daily = useMemo(() => data?.daily ?? [], [data]);
  const maxSource = Math.max(0.0001, ...(saved?.sources ?? []).map((s) => s.tokens));

  return (
    <div className="awareness usage">
      <header className="profile-bar">
        <div>
          <h2>Usage</h2>
          <p className="mono">tokens are each provider's own count · dollars are Claude's list price · savings are estimates</p>
        </div>
        <div className="profile-bar-actions">
          <div className="us-range" role="group" aria-label="Time range">
            {RANGES.map((r) => (
              <button key={r.days} type="button" className={days === r.days ? "on" : ""} onClick={() => setDays(r.days)}>
                {r.label}
              </button>
            ))}
          </div>
          <button type="button" className="close" onClick={onClose} aria-label="Close usage">
            ×
          </button>
        </div>
      </header>
      {error ? <div className="banner banner-denied">{error}</div> : null}

      {data && !data.enabled ? (
        <div className="aw-off">
          <p>
            Usage analytics are off. Set <span className="mono">enabled = true</span> under{" "}
            <span className="mono">[usage]</span> in config.toml, then restart Nova.
          </p>
        </div>
      ) : !data ? (
        <p className="pad-block">Reading the ledger…</p>
      ) : (
        <div className="usage-body">
          <div className="us-tiles">
            <Tile
              label="Claude used"
              value={usd(spent.cost_usd)}
              sub={`list price of ${tok(spent.paid_tokens)} tokens · your plan is a subscription`}
            />
            <Tile
              label="Saved (estimated)"
              value={usd(saved.usd)}
              accent="var(--done)"
              sub={`at least ${usd(saved.floor_usd)} · ${tok(saved.tokens)} tokens kept off Claude`}
            />
            <Tile
              label="Never reached a paid brain"
              value={saved.without_paid_model == null ? "—" : `${Math.round(saved.without_paid_model * 100)}%`}
              sub={`of ${saved.requests} requests`}
            />
            <Tile
              label="Claude cache hits"
              value={spent.cache_hit == null ? "—" : `${Math.round(spent.cache_hit * 100)}%`}
              sub="of Claude's input was read from cache, which is what keeps a turn cheap"
            />
          </div>

          <section className="usage-grid">
            <div>
              <div className="aw-card">
                <h3>
                  Who answered <span>requests per day</span>
                </h3>
                <Legend items={WHO} />
                {daily.length ? <WhoChart daily={daily} /> : <p className="pad-block">Nothing in this range.</p>}
              </div>

              <div className="aw-card">
                <h3>
                  Used and saved <span>dollars per day, one axis</span>
                </h3>
                <Legend
                  items={[
                    { label: "Claude used", color: SPENT },
                    { label: "Saved (estimated)", color: SAVED },
                  ]}
                />
                {daily.length ? <MoneyChart daily={daily} /> : null}
              </div>

              <div className="aw-card">
                <h3>
                  By brain <span>tokens as each provider counted them</span>
                </h3>
                <table className="us-table">
                  <thead>
                    <tr>
                      <th>Brain</th>
                      <th>Turns</th>
                      <th>Fresh in</th>
                      <th>Cache read</th>
                      <th>Cache write</th>
                      <th>Out</th>
                      <th>Per turn</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {spent.by_backend.map((b) => (
                      <tr key={b.backend}>
                        <td>
                          <i className="us-key" style={{ background: WHO.find((w) => w.key === b.backend)?.color ?? (b.free ? SAVED : "#6b6577") }} />
                          {b.backend}
                          {b.delegated ? <span className="us-note"> · {b.delegated} delegated</span> : null}
                        </td>
                        <td>{b.turns}{b.failed ? <span className="us-note"> ({b.failed} failed)</span> : null}</td>
                        <td>{tok(b.input)}</td>
                        <td>{tok(b.cache_read)}</td>
                        <td>{tok(b.cache_write)}</td>
                        <td>{tok(b.output)}</td>
                        <td>{tok(b.avg_tokens)}</td>
                        <td>{b.free ? "free" : b.backend === "antigravity" ? "plan" : usd(b.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {spent.failed_usd ? (
                  <p className="brief-note" style={{ color: "var(--failed)" }}>
                    {usd(spent.failed_usd)} went on {spent.failed_turns} turns that failed: the cost of a job is paid whether it works or not.
                  </p>
                ) : null}
                {spent.hidden_delegated_usd ? (
                  <p className="brief-note">
                    {usd(spent.hidden_delegated_usd)} of Claude's use came from jobs a free model handed over.
                  </p>
                ) : null}
              </div>

              <div className="aw-card">
                <h3>
                  Turns <span>{showTable ? "every turn in range, newest first" : "the most expensive"}</span>
                </h3>
                <table className="us-table us-turns">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Brain</th>
                      <th>Request</th>
                      <th>Tokens</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(showTable ? data.recent : data.top_turns).map((t) => (
                      <tr key={`${t.ts}-${t.backend}`} className={t.ok ? "" : "failed"}>
                        <td>{when(t.ts)}</td>
                        <td>
                          {t.backend}
                          {t.delegated ? <span className="us-note"> · delegated</span> : null}
                          {t.escalated ? <span className="us-note"> · handed to Claude</span> : null}
                        </td>
                        <td className="req" title={t.request}>
                          {t.task_type ? <span className="us-type">{t.task_type}</span> : null}
                          {t.request || "—"}
                        </td>
                        <td>{tok(t.tokens)}</td>
                        <td>{t.cost_usd ? usd(t.cost_usd) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <button type="button" className="ghost-btn" onClick={() => setShowTable((v) => !v)}>
                  {showTable ? "Show the most expensive" : "Show every turn"}
                </button>
              </div>
            </div>

            <div>
              <div className="aw-card">
                <h3>
                  Where the saving came from <span>tokens kept off Claude</span>
                </h3>
                {saved.sources.map((s) => (
                  <div key={s.source} className="aw-meter">
                    <div className="top">
                      <span>
                        {s.label} <span className="us-note">· {s.count}</span>
                      </span>
                      <b>
                        {tok(s.tokens)}
                        {s.source !== "extension" && s.usd ? ` · ${usd(s.usd)}` : ""}
                      </b>
                    </div>
                    <div className="bar">
                      <i style={{ width: `${(s.tokens / maxSource) * 100}%`, background: SAVED }} />
                    </div>
                  </div>
                ))}
              </div>

              {data.next_savings.length ? (
                <div className="aw-card aw-ask">
                  <h3>Where to save next</h3>
                  <p className="answer">
                    Repeated Claude work with no automation yet. Say “save that as an automation” after the next one.
                  </p>
                  {data.next_savings.map((k) => (
                    <div key={k.task_type} className="aw-meter">
                      <div className="top">
                        <span className="mono">{k.task_type}</span>
                        <b>
                          {k.claude_turns} turns · {usd(k.cost_usd)}
                        </b>
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}

              {data.automations.length ? (
                <div className="aw-card">
                  <h3>
                    Scripts that answered <span>no model at all</span>
                  </h3>
                  {data.automations.map((a) => (
                    <div key={a.name} className="aw-meter">
                      <div className="top">
                        <span>{a.name}</span>
                        <b>
                          {a.count}×{a.usd ? ` · ${usd(a.usd)}` : ""}
                        </b>
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}

              <div className="aw-card">
                <h3>
                  Browser extension <span>{data.browser.calls} calls</span>
                </h3>
                {data.browser.calls ? (
                  <>
                    <div className="aw-meter">
                      <div className="top">
                        <span>Sent to the model</span>
                        <b>{tok(data.browser.sent_tokens)} tokens</b>
                      </div>
                      <div className="bar">
                        <i
                          style={{
                            width: `${(data.browser.sent_tokens / Math.max(1, data.browser.page_tokens)) * 100}%`,
                            background: SAVED,
                          }}
                        />
                      </div>
                    </div>
                    <div className="aw-meter">
                      <div className="top">
                        <span>Full page text it stood in for</span>
                        <b>{tok(data.browser.page_tokens)} tokens</b>
                      </div>
                      <div className="bar">
                        <i style={{ width: "100%", background: "rgba(255,255,255,0.22)" }} />
                      </div>
                    </div>
                  </>
                ) : (
                  <p className="answer">No calls yet. Once Chrome is connected, each page read shows here against its full text.</p>
                )}
              </div>

              <div className="aw-card">
                <h3>How saving is worked out</h3>
                <p className="answer">
                  A free-model or Antigravity turn is valued at what a Claude turn of the same kind costs here: the median, {usd(base.median_usd)}
                  {" "}over {base.samples} Claude turns, with {base.task_types_priced} kinds priced on their own. “At least” uses the 25th percentile,{" "}
                  {usd(base.floor_usd)}.
                </p>
                <p className="answer">
                  A scripted answer counts as a Claude turn only for kinds Claude has actually done here. Otherwise it replaced a free-model
                  turn: $0, about {tok(base.free_median_tokens)} tokens. Failed turns and turns handed to Claude save nothing.
                </p>
                <p className="brief-note">
                  ledger since {data.history_from ? when(data.history_from) : "now"} · session logs before that are kept 14 days
                </p>
              </div>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
