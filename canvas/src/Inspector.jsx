import { useEffect, useState } from "react";

const KIND_LABEL = { request: "You said", route: "Route", agent: "Agent", step: "Plan step", result: "Result" };

const stepNumber = (node) => Number(String(node.id).replace("step", "")) || 1;

function CopyButton({ text, label = "Copy" }) {
  const [done, setDone] = useState(false);
  if (!text) return null;
  return (
    <button
      type="button"
      className="copy"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        } catch {
          setDone(false);
        }
      }}
    >
      {done ? "Copied" : label}
    </button>
  );
}

/**
 * Full, untruncated detail for one node: every action with its complete text.
 * The graph stays the at-a-glance view; this is where you go for what actually happened.
 */
export default function Inspector({ nodeId, run, onClose, say, planSteps = 0, source = null, leftEdge = null }) {
  const [node, setNode] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!nodeId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        // `source`: a stored request from the work history instead of the live graph tracker.
        const url = source ?? (run == null ? "/api/run" : `/api/run/${run}`);
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) throw new Error(`run ${run ?? "current"} is no longer kept`);
        const detail = await response.json();
        if (cancelled) return;
        setNode(detail.nodes.find((n) => n.id === nodeId) ?? null);
        setError("");
      } catch (err) {
        if (!cancelled) setError(String(err.message || err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [nodeId, run, source]);

  if (!nodeId) return null;
  const actions = node?.actions ?? [];
  const hidden = (node?.action_count ?? 0) - actions.length;

  return (
    <aside className="inspector">
      {leftEdge}
      <header>
        <div>
          <span className="node-kind">{KIND_LABEL[node?.kind] ?? node?.kind ?? "Node"}</span>
          <h2>{node?.label ?? "Loading..."}</h2>
        </div>
        <button type="button" className="close" onClick={onClose} aria-label="Close details">
          ×
        </button>
      </header>

      {error ? <p className="muted pad">{error}</p> : null}

      {/* Editing acts on the plan Nova holds now, so only offer it while this step is still in it. */}
      {node?.kind === "step" && say && stepNumber(node) <= planSteps ? (
        <section className="step-actions">
          <button type="button" disabled={stepNumber(node) === 1} onClick={() => say(`move step ${stepNumber(node)} up`)}>
            Move up
          </button>
          <button
            type="button"
            disabled={stepNumber(node) === planSteps}
            onClick={() => say(`move step ${stepNumber(node)} down`)}
          >
            Move down
          </button>
          <button type="button" onClick={() => say(`remove step ${stepNumber(node)}`)}>Remove this step</button>
          <button type="button" onClick={() => say("go ahead")}>Run the plan</button>
          <button type="button" onClick={() => say("read the plan")}>Read it back</button>
        </section>
      ) : null}

      {node?.detail ? (
        <section>
          <h3>
            Reply <CopyButton text={node.detail} />
          </h3>
          <pre>{node.detail}</pre>
        </section>
      ) : null}

      <section>
        <h3>
          Actions <span className="muted">({node?.action_count ?? 0})</span>
          <CopyButton text={actions.map((a) => `${a.tool}: ${a.text}`).join("\n\n")} label="Copy all" />
        </h3>
        {hidden > 0 ? <p className="muted">{hidden} older actions are in the session log on disk.</p> : null}
        {actions.length === 0 ? (
          <p className="muted">No actions recorded for this node.</p>
        ) : (
          <ol className="action-list">
            {actions.map((action, i) => (
              <li key={i} className={`action-${action.status}`}>
                <div className="action-head">
                  <span className="action-tool">{action.tool}</span>
                  <span className="muted">{action.status}</span>
                  <CopyButton text={action.text} />
                </div>
                <pre>{action.text || "(no arguments)"}</pre>
              </li>
            ))}
          </ol>
        )}
      </section>
    </aside>
  );
}
