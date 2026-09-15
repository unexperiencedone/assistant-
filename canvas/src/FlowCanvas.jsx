import { useEffect, useMemo } from "react";
import { Background, Controls, ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import StepNode from "./StepNode.jsx";
import { layoutGraph } from "./layout.js";

const nodeTypes = { step: StepNode };

function Graph({ graph }) {
  const { nodes, edges } = useMemo(() => layoutGraph(graph), [graph]);
  const { fitView } = useReactFlow();

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
      elementsSelectable={false}
      proOptions={{ hideAttribution: true }}
      minZoom={0.2}
      fitView
    >
      <Background gap={22} size={1} />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}

export default function FlowCanvas({ graph }) {
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
      <Graph graph={graph} />
    </ReactFlowProvider>
  );
}
