import FlowCanvas from "./FlowCanvas.jsx";
import Sidebar from "./Sidebar.jsx";
import { useAssistant } from "./useAssistant.js";

export default function App() {
  const { snapshot, connected, say } = useAssistant();
  const status = connected ? snapshot?.status ?? "idle" : "offline";

  return (
    <div className="app">
      <header className="topbar">
        <h1>{snapshot?.name ?? "Nova"}</h1>
        <div className={`status status-${status}`}>
          <span className="orb" />
          {status}
        </div>
        {snapshot?.agent_running ? <span className="working">Agent working</span> : null}
        <span className="chip">
          Agent <b>{snapshot?.backend ?? "—"}</b>
        </span>
        <span className="chip chip-path" title={snapshot?.workspace}>
          Workspace <b>{snapshot?.workspace ?? "—"}</b>
        </span>
      </header>
      <main className="main">
        <section className="canvas" aria-label="Live plan canvas">
          <FlowCanvas graph={snapshot?.graph} />
        </section>
        <Sidebar snapshot={snapshot} say={say} />
      </main>
    </div>
  );
}
