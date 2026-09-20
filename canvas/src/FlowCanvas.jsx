import { useEffect, useMemo } from "react";
import { Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import StepNode from "./StepNode.jsx";
import { layoutGraph } from "./layout.js";

const nodeTypes = { step: StepNode };
const STATUS_COLOR = { pending: "#6b6577", running: "#c9a0ff", done: "#ff8a65", failed: "#ff8a8a" };

const stepNumber = (id) => Number(String(id).replace("step", "")) || 0;

function Graph({ graph, onSelect, selectedId, onMoveStep }) {
  const { nodes, edges } = useMemo(() => layoutGraph(graph, selectedId), [graph, selectedId]);
  const { fitView } = useReactFlow();

  /** Dropped a step on the canvas: work out which slot it landed in, by vertical position. */
  const handleDragStop = (_event, node) => {
    if (!onMoveStep || !node.id.startsWith("step")) return;
    const steps = nodes
      .filter((n) => n.id.startsWith("step"))
      .map((n) => ({ number: stepNumber(n.id), y: n.id === node.id ? node.position.y : n.position.y }))
      .sort((a, b) => a.y - b.y);
    const to = steps.findIndex((s) => s.number === stepNumber(node.id)) + 1;
    if (to > 0 && to !== stepNumber(node.id)) onMoveStep(stepNumber(node.id), to);
    else fitView({ padding: 0.18, duration: 200, maxZoom: 1.1 });  // snap back into place
  };

  // Re-frame when a new request starts or the graph grows.
  useEffect(() => {
    const id = requestAnimationFrame(() => fitView({ padding: 0.18, duration: 250, maxZoom: 1.1 }));
    return () => cancelAnimationFrame(id);
  }, [graph.run, nodes.length, fitView]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      nodesConnectable={false}
      elementsSelectable
      onNodeClick={(_event, node) => onSelect(node.id)}
      onNodeDragStop={handleDragStop}
      onPaneClick={() => onSelect(null)}
      proOptions={{ hideAttribution: true }}
      minZoom={0.2}
      fitView
    >
      <Background gap={22} size={1} />
      <Controls showInteractive={false} position="bottom-left" />
      {nodes.length > 6 ? (
        <MiniMap pannable zoomable nodeColor={(n) => STATUS_COLOR[n.data?.status] ?? "#6b6577"} nodeStrokeWidth={2} />
      ) : null}
    </ReactFlow>
  );
}

export default function FlowCanvas({ graph, onSelect, selectedId, onMoveStep }) {
  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="canvas-empty">
        <p className="canvas-empty-title">Nothing running yet</p>
        <p>Say or type a request. Its route, plan steps and actions show up here as they happen.</p>
      </div>
    );
  }
  return (
    <ReactFlowProvider>
      <Graph graph={graph} onSelect={onSelect} selectedId={selectedId} onMoveStep={onMoveStep} />
    </ReactFlowProvider>
  );
}
