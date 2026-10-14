import { useCallback, useEffect, useRef, useState } from "react";
import Briefing from "./Briefing.jsx";
import FlowCanvas from "./FlowCanvas.jsx";
import History from "./History.jsx";
import Inspector from "./Inspector.jsx";
import NovaEntity from "./NovaEntity.jsx";
import Reader from "./Reader.jsx";
import { ResizeHandle, useIsNarrow, useWidth } from "./Resizer.jsx";
import Sidebar from "./Sidebar.jsx";
import { useAssistant } from "./useAssistant.js";
import Masthead, { SPACES } from "./spaces/Masthead.jsx";
import Now from "./spaces/Now.jsx";
import Studio from "./spaces/Studio.jsx";
import You from "./spaces/You.jsx";
import Overlay from "./spaces/Overlay.jsx";
import { typing } from "./spaces/api.js";

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

const HASH_SPACE = {
  "#now": "now", "#work": "work", "#history": "work", "#studio": "studio",
  "#you": "you", "#memory": "you", "#awareness": "you", "#profile": "you", "#usage": "you", "#connections": "you",
};
// The overlay and the reader are windows of their own, opened on that hash. Whatever the
// canvas does later (a briefing arriving switches it to Work) must never turn them into
// the full app: the overlay window lives for the whole session, built ahead of the hotkey.
const WINDOW = location.hash === "#overlay" ? "overlay" : location.hash === "#reader" ? "reader" : null;
const HASH_YOU = { "#memory": "memory", "#awareness": "awareness", "#profile": "profile", "#usage": "usage", "#connections": "connections" };

export default function App() {
  const { snapshot, connected, say, dismissDenials } = useAssistant();
  const status = connected ? snapshot?.status ?? "idle" : "offline";
  const [mode, setMode] = useTaskMode();
  const [selected, setSelected] = useState(null); // node id shown in the inspector
  const [viewRun, setViewRun] = useState(null); // null = the live run
  const [pastGraph, setPastGraph] = useState(null);
  const [lane, setLane] = useState(null); // null = follow whichever lane moved last
  const [briefing, setBriefing] = useState(false);
  const [view, setView] = useState(() =>
    location.hash === "#reader" ? "reader" : location.hash === "#overlay" ? "overlay" : location.hash === "#history" ? "history" : "canvas",
  );
  // Four spaces (docs/ui_rethink.md). Old links still land: #profile opens You on Profile.
  const [space, setSpace] = useState(() => HASH_SPACE[location.hash] ?? "now");
  const [youTab, setYouTab] = useState(() => HASH_YOU[location.hash] ?? "memory");
  const mainRef = useRef(null);

  // Every tray is draggable, and remembers how wide you left it.
  const narrow = useIsNarrow();
  const [entityW, setEntityW, resetEntityW] = useWidth("entity", 400, 300, 760);
  const [sideW, setSideW, resetSideW] = useWidth("sidebar", 372, 280, 620);
  const [inspectorW, setInspectorW, resetInspectorW] = useWidth("inspector", 340, 260, 560);
  const [briefingW, setBriefingW, resetBriefingW] = useWidth("briefing", 372, 300, 640);

  const showCanvas = useCallback(() => setView("canvas"), []);

  useEffect(() => {
    if (view === "reader" || view === "overlay") return;
    const target =
      space === "now" ? "" : space === "work" ? (view === "history" ? "#history" : "#work") : space === "you" ? `#${youTab}` : `#${space}`;
    // window.history: `history` below is the request rail, this is the browser's.
    if (location.hash !== target) window.history.replaceState(null, "", `${location.pathname}${location.search}${target}`);
  }, [view, space, youTab]);

  // Keys 1–4 switch spaces, the HUD half of the grammar. Never while typing.
  useEffect(() => {
    const onKey = (event) => {
      if (event.ctrlKey || event.altKey || event.metaKey || typing(event)) return;
      const target = SPACES.find((s) => s.key === event.key);
      if (target) setSpace(target.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const lanes = snapshot?.graph?.lanes ?? {};
  const activeLane = lane ?? snapshot?.graph?.lane ?? "agent";
  const liveGraph = lanes[activeLane] ?? snapshot?.graph;
  // Only a task still in flight earns a chip: a finished one is already in the
  // history part of the same rail, and showing both said everything twice.
  const busyLanes = Object.entries(lanes).filter(([, g]) => g.nodes?.some((n) => n.status === "running"));
  const history = snapshot?.graph?.history ?? [];
  const denials = snapshot?.denials ?? [];
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
    if (!WINDOW && seenAmbient.current != null && ambientRevision > seenAmbient.current) {
      setBriefing(true);
      setView("canvas");
      setSpace("work");
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

  // The reader is its own always-on-top window, so it renders bare: no rail, no trays,
  // no status bar. Those belong to the canvas, which is a live activity graph; this is
  // a page to read, and chrome around it would only take space from the text.
  // Nova over another app: its own frameless window, nothing but the frame and the panel.
  if (WINDOW === "overlay") return <Overlay snapshot={snapshot} />;

  if (WINDOW === "reader" || view === "reader") {
    return (
      <div className="app is-reader">
        <Reader document={snapshot?.document} />
      </div>
    );
  }

  return (
    <div className="app">
      <Masthead
        space={space}
        onSpace={setSpace}
        status={status}
        working={Boolean(snapshot?.agent_running)}
        count={snapshot?.inbox?.count ?? 0}
        live={snapshot?.live}
        talk={snapshot?.talk}
        onBriefing={() => {
          setSpace("work");
          setView("canvas");
          setBriefing((open) => !open);
        }}
      />

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

      {space === "now" ? (
        <Now snapshot={snapshot} graph={liveGraph} say={say} active onSetup={() => (setYouTab("connections"), setSpace("you"))} />
      ) : space === "studio" ? (
        <Studio snapshot={snapshot} active />
      ) : space === "you" ? (
        <You tab={youTab} onTab={setYouTab} snapshot={snapshot} onClose={() => setSpace("now")} />
      ) : view === "history" ? (
        <>
          <div className="m-subtabs" style={{ margin: "16px 32px 0" }} role="toolbar" aria-label="Work">
            <button type="button" aria-pressed={false} onClick={showCanvas}>Live canvas</button>
            <button type="button" aria-pressed>Sessions</button>
          </div>
          <History onClose={showCanvas} live={snapshot?.history} />
        </>
      ) : (
        <>
          <div className="m-subtabs" style={{ margin: "16px 32px 0" }} role="toolbar" aria-label="Work">
            <button type="button" aria-pressed>Live canvas</button>
            <button type="button" aria-pressed={false} onClick={() => setView("history")}>Sessions</button>
          </div>
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
                      {laneGraph.short || laneGraph.title}
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
                  {item.short || item.label || (item.run ? `request ${item.run}` : "earlier task")}
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
