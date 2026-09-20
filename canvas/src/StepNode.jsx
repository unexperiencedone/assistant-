import { memo } from "react";
import { Handle, Position } from "@xyflow/react";

const KIND_LABEL = {
  request: "You said",
  route: "Route",
  agent: "Agent",
  step: "Step",
  result: "Result",
};

const STATUS_LABEL = { pending: "Pending", running: "Running", done: "Done", failed: "Failed" };

function StepNode({ data }) {
  const actions = data.actions ?? [];
  const hidden = (data.action_count ?? actions.length) - actions.length;
  return (
    <div
      className={`node node-${data.kind} status-${data.status}${data.selected ? " selected" : ""}`}
      title="Click for the full detail"
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="node-top">
        <span className="node-kind">{KIND_LABEL[data.kind] ?? data.kind}</span>
        <span className="node-status">
          <span className="dot" />
          {STATUS_LABEL[data.status] ?? data.status}
        </span>
      </div>
      <div className="node-label">{data.label}</div>
      {data.detail ? <div className="node-detail">{data.detail}</div> : null}
      {actions.length > 0 ? (
        <ul className="node-actions">
          {hidden > 0 ? <li className="action-more">+{hidden} earlier (click to see)</li> : null}
          {actions.map((a, i) => (
            <li key={i} className={`action action-${a.status}`}>
              <span className="action-tool">{a.tool}</span>
              <span className="action-text">{a.text}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

export default memo(StepNode);
