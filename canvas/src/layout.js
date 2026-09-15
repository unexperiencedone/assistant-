import dagre from "@dagrejs/dagre";

export const NODE_WIDTH = 280;
const BASE_HEIGHT = 58;
const DETAIL_HEIGHT = 20;
const ACTION_HEIGHT = 19;

function nodeHeight(node) {
  const actions = node.actions?.length ?? 0;
  const hidden = (node.action_count ?? actions) - actions > 0 ? 1 : 0;
  return BASE_HEIGHT + (node.detail ? DETAIL_HEIGHT : 0) + (actions + hidden) * ACTION_HEIGHT + (actions ? 8 : 0);
}

// Top-to-bottom DAG layout. Positions are recomputed on every graph change;
// the graph is small (one request), so this is instant.
export function layoutGraph(graph) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 36, ranksep: 42, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));

  const heights = {};
  for (const node of graph.nodes) {
    heights[node.id] = nodeHeight(node);
    g.setNode(node.id, { width: NODE_WIDTH, height: heights[node.id] });
  }
  for (const edge of graph.edges) {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) g.setEdge(edge.source, edge.target);
  }
  dagre.layout(g);

  const statusById = Object.fromEntries(graph.nodes.map((n) => [n.id, n.status]));
  const nodes = graph.nodes.map((node) => {
    const { x, y } = g.node(node.id);
    return {
      id: node.id,
      type: "step",
      position: { x: x - NODE_WIDTH / 2, y: y - heights[node.id] / 2 },
      data: node,
      draggable: false,
    };
  });
  const edges = graph.edges
    .filter((e) => statusById[e.source] && statusById[e.target])
    .map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      animated: statusById[e.target] === "running",
      className: `edge-${statusById[e.target]}`,
    }));
  return { nodes, edges };
}
