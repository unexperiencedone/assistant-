import { useEffect, useMemo, useState } from "react";

/**
 * The detailed log for the request on screen: how many commands ran, and every one of
 * them in order, with its arguments and how it ended.
 *
 * Simple-task mode hides the plan graph, but it must never hide what actually ran —
 * this tab is where that detail stays live (and History keeps it after the fact).
 * It reads /api/run, which serves the untruncated actions the canvas snapshot trims.
 */

const KIND_LABEL = { request: "You said", route: "Route", agent: "Agent", step: "Plan step", result: "Result" };

export default function RunDetail({ graph, run }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  // Re-fetch when the request changes or another action lands, not on every mic tick.
  const pulse = useMemo(() => {
    const nodes = graph?.nodes ?? [];
    return `${graph?.run ?? 0}:${nodes.length}:${nodes.reduce((sum, n) => sum + (n.action_count ?? 0), 0)}:${nodes
      .map((n) => n.status)
      .join("")}`;
  }, [graph]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(run == null ? "/api/run" : `/api/run/${run}`, { cache: "no-store" });
        if (!response.ok) throw new Error(`${response.status}`);
        const loaded = await response.json();
        if (!cancelled) {
          setDetail(loaded);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError(`No detail for this request: ${e.message}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pulse, run]);

  const nodes = (detail?.nodes ?? []).filter((node) => (node.actions?.length ?? 0) > 0);
  const total = nodes.reduce((sum, node) => sum + node.actions.length, 0);
  const result = (detail?.nodes ?? []).find((node) => node.kind === "result");

  if (error && !detail) return <p className="detail-empty">{error}</p>;
  if (!detail || !detail.nodes?.length) {
    return <p className="detail-empty">Nothing has run yet. Every command Nova runs is listed here as it happens.</p>;
  }

  return (
    <div className="detail">
      <div className="detail-run on">
        <div className="detail-head">
          <span className="what">{detail.label || `request ${detail.run}`}</span>
          <span className="count">
            {total} command{total === 1 ? "" : "s"}
          </span>
        </div>
        <div className="detail-meta">
          request {detail.run} · {nodes.length} stage{nodes.length === 1 ? "" : "s"}
          {result ? ` · ${result.status}` : ""}
        </div>
      </div>

      {nodes.map((node) => (
        <div key={node.id} className="detail-run">
          <div className="detail-head">
            <span className="what">{KIND_LABEL[node.kind] ?? node.kind}: {node.label}</span>
            <span className="count">{node.actions.length}</span>
          </div>
          <ol className="detail-cmds">
            {node.actions.map((action, i) => (
              <li key={i} className={`detail-cmd ${action.status}`}>
                <span className="n">{String(i + 1).padStart(2, "0")}</span>
                <span>
                  <span className="tool">{action.tool}</span>{" "}
                  <span className="args">{action.text || "(no arguments)"}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      ))}

      {result?.detail ? (
        <div className="detail-run">
          <div className="detail-head">
            <span className="what">Reply</span>
          </div>
          <p className="detail-result">{result.detail}</p>
        </div>
      ) : null}
    </div>
  );
}
