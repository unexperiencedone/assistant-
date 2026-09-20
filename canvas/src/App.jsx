import { useCallback, useEffect, useRef, useState } from "react";
import Awareness from "./Awareness.jsx";
import Briefing from "./Briefing.jsx";
import FlowCanvas from "./FlowCanvas.jsx";
import History from "./History.jsx";
import Inspector from "./Inspector.jsx";
import NovaEntity from "./NovaEntity.jsx";
import Profile from "./Profile.jsx";
import { ResizeHandle, useIsNarrow, useWidth } from "./Resizer.jsx";
import Sidebar from "./Sidebar.jsx";
import { useAssistant } from "./useAssistant.js";

function MicMeter({ mic, status }) {
  if (status !== "listening" && status !== "transcribing") return null;
  const level = Math.max(0, Math.min(1, mic?.level ?? 0));
  const bars = [0.15, 0.4, 0.65, 0.9];
  return (
    <span className="mic" title={`Microphone level ${(level * 100).toFixed(0)}%`} aria-label="Microphone level">
      {bars.map((threshold) => (
        <i key={threshold} className={level >= threshold ? "on" : ""} />
      ))}
    </span>
  );
}

function timeLabel(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Plan or Simple task. Remembered, because it is a preference, not a per-run choice. */
function useTaskMode() {
  const [mode, setMode] = useState(() => {
    try {
      return localStorage.getItem("nova-task-mode") === "simple" ? "simple" : "plan";
    } catch {
      return "plan";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("nova-task-mode", mode);
    } catch {
      /* private window: it just won't be remembered */
    }
  }, [mode]);
  return [mode, setMode];
}

/** The one ambient line under the blob. Read from the same cache the panel shows. */
function useAmbientLine(revision) {
  const [line, setLine] = useState("");
  useEffect(() => {
    let cancelled = false;
    fetch("/api/ambient", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const parts = [];
        if (data.weather?.place) parts.push(`${data.weather.place} ${data.weather.temp ?? ""}`.trim());
        for (const market of (data.markets ?? []).slice(0, 2)) parts.push(`${market.name} ${market.change}`);
        setLine(parts.join(" · "));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [revision]);
  return line;
}

/** Two levels of dark: bright by default, dim for night. Survives reloads. */
function useInkLevel() {
  const [level, setLevel] = useState(() => {
    try {
      return localStorage.getItem("nova-ink") === "dim" ? "dim" : "bright";
    } catch {
      return "bright";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.ink = level;
    try {
      localStorage.setItem("nova-ink", level);
    } catch {
      /* private window: it just won't be remembered */
    }
  }, [level]);
  return [level, () => setLevel((current) => (current === "dim" ? "bright" : "dim"))];
}

const VIEWS = { "#profile": "profile", "#history": "history", "#awareness": "awareness" };

export default function App() {
  const { snapshot, connected, say, dismissDenials } = useAssistant();
  const status = connected ? snapshot?.status ?? "idle" : "offline";
  const [mode, setMode] = useTaskMode();
  const [ink, toggleInk] = useInkLevel();
  const [selected, setSelected] = useState(null); // node id shown in the inspector
  const [viewRun, setViewRun] = useState(null); // null = the live run
  const [pastGraph, setPastGraph] = useState(null);
  const [lane, setLane] = useState(null); // null = follow whichever lane moved last
  const [briefing, setBriefing] = useState(false);
  const [view, setView] = useState(() => VIEWS[location.hash] ?? "canvas");
  const mainRef = useRef(null);

  // Every tray is draggable, and remembers how wide you left it.
  const narrow = useIsNarrow();
  const [entityW, setEntityW, resetEntityW] = useWidth("entity", 400, 300, 760);
  const [sideW, setSideW, resetSideW] = useWidth("sidebar", 372, 280, 620);
  const [inspectorW, setInspectorW, resetInspectorW] = useWidth("inspector", 340, 260, 560);
  const [briefingW, setBriefingW, resetBriefingW] = useWidth("briefing", 372, 300, 640);

  const toggleView = useCallback((name) => setView((current) => (current === name ? "canvas" : name)), []);
  const showCanvas = useCallback(() => setView("canvas"), []);

  useEffect(() => {
    const target = view === "canvas" ? "" : `#${view}`;
    // window.history: `history` below is the request rail, this is the browser's.
    if (location.hash !== target) window.history.replaceState(null, "", `${location.pathname}${location.search}${target}`);
  }, [view]);

  const lanes = snapshot?.graph?.lanes ?? {};
  const activeLane = lane ?? snapshot?.graph?.lane ?? "agent";
  const liveGraph = lanes[activeLane] ?? snapshot?.graph;
  // Only a task still in flight earns a chip: a finished one is already in the
  // history part of the same rail, and showing both said everything twice.
  const busyLanes = Object.entries(lanes).filter(([, g]) => g.nodes?.some((n) => n.status === "running"));
  const history = snapshot?.graph?.history ?? [];
  const denials = snapshot?.denials ?? [];
  const queue = snapshot?.queue ?? [];
  const ambientLine = useAmbientLine(snapshot?.ambient?.revision);

  // Loading a past request from the history rail.
  useEffect(() => {
    if (viewRun == null) {
      setPastGraph(null);
      return undefined;
    }
    let cancelled = false;
    fetch(`/api/run/${viewRun}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("gone"))))
      .then((graph) => !cancelled && setPastGraph(graph))
      .catch(() => !cancelled && setViewRun(null));
    return () => {
      cancelled = true;
    };
  }, [viewRun]);

  // Plan or Simple task follows the work: a drafted plan switches the canvas to Plan
  // so the graph is there to watch, and finishing (or clearing) it goes back to Simple.
  // A manual toggle still stands until the plan next appears or disappears.
  // Keyed on which plan this is, not just whether one exists: drafting a second plan
  // while the first is still on screen has to bring the graph back too.
  const planSteps = snapshot?.plan?.steps ?? [];
  const planKey = `${snapshot?.plan?.title ?? ""}|${planSteps.map((step) => step.text).join("|")}`;
  const seenPlan = useRef(null);
  useEffect(() => {
    if (seenPlan.current === planKey) return;
    seenPlan.current = planKey;
    // "Let's plan X" clears the old plan and only then asks for a new one, so an empty
    // plan while the agent is working is a plan being drafted, not a cleared one:
    // dropping to Simple there would flicker for the second before the steps land.
    if (planSteps.length === 0 && snapshot?.agent_running) return;
    setMode(planSteps.length > 0 ? "plan" : "simple");
  }, [planKey, planSteps.length, snapshot?.agent_running, setMode]);

  // A briefing always shows itself. The revision moves when one is asked for and again
  // when the agent finishes writing the cache, so this covers "brief me" typed here,
  // said out loud, or run as part of some larger request.
  const ambientRevision = snapshot?.ambient?.revision;
  const seenAmbient = useRef(null);
  useEffect(() => {
    if (ambientRevision == null) return;
    if (seenAmbient.current != null && ambientRevision > seenAmbient.current) {
      setBriefing(true);
      setView("canvas");
    }
    seenAmbient.current = ambientRevision;
  }, [ambientRevision]);

  const moveStep = useCallback((from, to) => {
    fetch("/api/plan/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from, to }),
    }).catch(() => {});
  }, []);

  const showLive = useCallback(() => {
    setViewRun(null);
    setLane(null);
    setSelected(null);
  }, []);

  const graph = viewRun == null ? liveGraph : pastGraph;
  const simple = mode === "simple";
  const columns = simple
    ? `minmax(0, 1fr) ${sideW}px`
    : selected
      ? `${entityW}px minmax(0, 1fr) ${inspectorW}px ${sideW}px`
      : `${entityW}px minmax(0, 1fr) ${sideW}px`;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-ink" aria-hidden="true" />
          <h1>{snapshot?.name ?? "Nova"}</h1>
        </div>

        <nav className="views" aria-label="Screens">
          <button type="button" className={view === "canvas" ? "on" : ""} onClick={showCanvas} aria-pressed={view === "canvas"}>
            Canvas
          </button>
          <button
            type="button"
            className={view === "history" ? "on" : ""}
            onClick={() => toggleView("history")}
            aria-pressed={view === "history"}
            title={snapshot?.history?.current ? "A work session is in progress" : "Past work sessions"}
          >
            {snapshot?.history?.current ? <i className="live-dot" /> : null}
            History
          </button>
          <button
            type="button"
            className={view === "profile" ? "on" : ""}
            onClick={() => toggleView("profile")}
            aria-pressed={view === "profile"}
          >
            {snapshot?.profile?.name ? `Profile · ${snapshot.profile.name}` : "Profile"}
          </button>
          <button
            type="button"
            className={view === "awareness" ? "on" : ""}
            onClick={() => toggleView("awareness")}
            aria-pressed={view === "awareness"}
            title="What you were doing on this PC"
          >
            {snapshot?.awareness?.watching ? <i className="live-dot" /> : null}
            Activity
          </button>
        </nav>

        <div className={`status status-${status}`}>
          <span className="orb" />
          {status}
          <MicMeter mic={snapshot?.mic} status={status} />
        </div>

        <span className="spacer" />

        <button
          type="button"
          className={`pill${briefing ? " on" : ""}`}
          onClick={() => {
            setBriefing((open) => !open);
            setView("canvas");
          }}
          aria-pressed={briefing}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M4 6h16M4 12h10M4 18h7" />
          </svg>
          Briefing
        </button>

        <button
          type="button"
          className="pill ink-toggle"
          onClick={toggleInk}
          title={ink === "dim" ? "Dim — click for brighter" : "Bright — click to dim"}
          aria-label={ink === "dim" ? "Brighten the canvas" : "Dim the canvas"}
        >
          {ink === "dim" ? "◑" : "◐"}
        </button>
      </header>

      <div className="statusbar">
        <span className="where" title={snapshot?.workspace}>
          <i />
          {snapshot?.workspace ?? "—"}
        </span>
        <span className="sep" />
        <span>
          agent <b>{snapshot?.backend ?? "—"}</b>
        </span>
        <span className="sep" />
        <span className="cost" title={`All time: $${(snapshot?.cost_total_usd ?? 0).toFixed(2)}, as reported by the agent.`}>
          cost <b>{snapshot?.cost_usd ? `$${snapshot.cost_usd.toFixed(3)}` : "free"}</b>
        </span>
        <span className="sep" />
        <span>
          turns <b>{snapshot?.turns ?? 0}</b>
        </span>
        {queue.length ? (
          <>
            <span className="sep" />
            <span title={queue.join("\n")}>
              waiting <b>{queue.length}</b>
            </span>
          </>
        ) : null}
        <span className="spacer" />
        <span className="note">everything below the blob is a summary — full detail lives in History</span>
      </div>

      {denials.length > 0 ? (
        <div className="banner banner-denied" role="status">
          <span>
            <b>Blocked:</b> {[...new Set(denials.map((d) => d.tool))].join(", ")}. Nobody can approve permissions while
            the agent runs in the background, so it stopped there.
          </span>
          <button type="button" className="banner-close" onClick={dismissDenials} aria-label="Dismiss">
            Dismiss
          </button>
        </div>
      ) : null}

      {view === "profile" ? (
        <Profile onClose={showCanvas} revision={snapshot?.profile?.revision} />
      ) : view === "history" ? (
        <History onClose={showCanvas} live={snapshot?.history} />
      ) : view === "awareness" ? (
        <Awareness onClose={showCanvas} revision={snapshot?.awareness?.revision} />
      ) : (
        <>
          {/* One rail, not two: what is running now, then what already ran. Lane chips
              only appear when more than one task is actually in flight. */}
          {busyLanes.length > 1 || history.length > 0 ? (
            <div className="history" aria-label="Requests">
              <button type="button" className={viewRun == null ? "active" : ""} onClick={showLive}>
                Live
              </button>
              {busyLanes.length > 1
                ? busyLanes.map(([name, laneGraph]) => (
                    <button
                      key={name}
                      type="button"
                      className={`lane${activeLane === name && viewRun == null ? " active" : ""}`}
                      title={`${laneGraph.title}: ${laneGraph.label || "running"}`}
                      onClick={() => {
                        setLane(name);
                        setViewRun(null);
                        setSelected(null);
                      }}
                    >
                      <i className="lane-dot" />
                      {laneGraph.label?.slice(0, 26) || laneGraph.title}
                    </button>
                  ))
                : null}
              {busyLanes.length > 1 && history.length ? <span className="rail-sep" /> : null}
              {history.map((item) => (
                <button
                  key={item.run}
                  type="button"
                  className={`${viewRun === item.run ? "active" : ""} run-${item.status}`}
                  title={`${item.label} · ${timeLabel(item.started)}`}
                  onClick={() => {
                    setViewRun(item.run);
                    setSelected(null);
                  }}
                >
                  {item.label || `request ${item.run}`}
                </button>
              ))}
            </div>
          ) : null}

          <main
            className={`main${selected && !simple ? " with-inspector" : ""}${simple ? " simple" : ""}`}
            ref={mainRef}
            style={narrow ? undefined : { gridTemplateColumns: columns }}
          >
            <NovaEntity
              snapshot={snapshot}
              status={status}
              mode={mode}
              onMode={setMode}
              ambientLine={ambientLine}
              onOpenBriefing={() => setBriefing(true)}
              rightEdge={
                narrow || simple ? null : (
                  <ResizeHandle
                    side="right"
                    width={entityW}
                    onResize={setEntityW}
                    onReset={resetEntityW}
                    label="Resize Nova"
                  />
                )
              }
            />

            {simple ? null : (
            <section className="canvas" aria-label="Live plan canvas">
              {viewRun != null ? (
                <div className="viewing-past">
                  Viewing an earlier request. <button type="button" onClick={showLive}>Back to live</button>
                </div>
              ) : null}
              <FlowCanvas
                graph={graph}
                onSelect={setSelected}
                selectedId={selected}
                onMoveStep={viewRun == null ? moveStep : null}
              />
            </section>
            )}

            {selected && !simple ? (
              <Inspector
                nodeId={selected}
                run={viewRun}
                onClose={() => setSelected(null)}
                say={say}
                planSteps={snapshot?.plan?.steps?.length ?? 0}
                leftEdge={
                  narrow ? null : (
                    <ResizeHandle
                      side="left"
                      width={inspectorW}
                      onResize={setInspectorW}
                      onReset={resetInspectorW}
                      label="Resize the inspector"
                    />
                  )
                }
              />
            ) : null}

            <Sidebar
              snapshot={snapshot}
              say={say}
              graph={graph}
              viewRun={viewRun}
              mode={mode}
              leftEdge={
                narrow ? null : (
                  <ResizeHandle
                    side="left"
                    width={sideW}
                    onResize={setSideW}
                    onReset={resetSideW}
                    label="Resize the sidebar"
                  />
                )
              }
            />

            {briefing ? (
              <Briefing
                onClose={() => setBriefing(false)}
                revision={snapshot?.ambient?.revision}
                onAsk={() => setView("canvas")}
                width={narrow ? undefined : briefingW}
                leftEdge={
                  narrow ? null : (
                    <ResizeHandle
                      side="left"
                      width={briefingW}
                      onResize={setBriefingW}
                      onReset={resetBriefingW}
                      label="Resize the briefing"
                    />
                  )
                }
              />
            ) : null}
          </main>
        </>
      )}
    </div>
  );
}
