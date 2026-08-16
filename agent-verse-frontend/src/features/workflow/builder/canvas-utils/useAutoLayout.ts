/**
 * useAutoLayout — Dagre-based automatic node layout.
 *
 * Arranges workflow nodes in a left-to-right hierarchical layout
 * respecting the edges (depends_on relationships).
 */
import { useCallback } from 'react';
import type { Node, Edge } from '@xyflow/react';

const NODE_WIDTH = 210;
const NODE_HEIGHT = 70;
const H_GAP = 80;
const V_GAP = 40;

interface LayoutResult {
  nodes: Node[];
  edges: Edge[];
}

/**
 * Simple topological layout without dagre dependency.
 * For production, replace with dagre for proper graph layout.
 */
function topologicalLayout(nodes: Node[], edges: Edge[]): LayoutResult {
  if (nodes.length === 0) return { nodes, edges };

  // Build adjacency
  const inbound: Record<string, string[]> = {};
  const outbound: Record<string, string[]> = {};
  for (const n of nodes) {
    inbound[n.id] = [];
    outbound[n.id] = [];
  }
  for (const e of edges) {
    outbound[e.source]?.push(e.target);
    inbound[e.target]?.push(e.source);
  }

  // Kahn's algorithm for topological levels
  const levels: Record<string, number> = {};
  const queue: string[] = nodes.filter((n) => (inbound[n.id]?.length ?? 0) === 0).map((n) => n.id);
  const inDegree = Object.fromEntries(nodes.map((n) => [n.id, inbound[n.id]?.length ?? 0]));

  for (const id of queue) levels[id] = 0;

  const processing = [...queue];
  while (processing.length > 0) {
    const id = processing.shift()!;
    for (const next of outbound[id] ?? []) {
      inDegree[next] = (inDegree[next] ?? 0) - 1;
      levels[next] = Math.max(levels[next] ?? 0, (levels[id] ?? 0) + 1);
      if (inDegree[next] === 0) processing.push(next);
    }
  }

  // Position nodes in grid
  const colCounts: Record<number, number> = {};
  const positioned = nodes.map((n) => {
    const col = levels[n.id] ?? 0;
    const row = colCounts[col] ?? 0;
    colCounts[col] = row + 1;

    return {
      ...n,
      position: {
        x: 60 + col * (NODE_WIDTH + H_GAP),
        y: 60 + row * (NODE_HEIGHT + V_GAP),
      },
    };
  });

  return { nodes: positioned, edges };
}

export function useAutoLayout() {
  const applyLayout = useCallback((nodes: Node[], edges: Edge[]): LayoutResult => {
    return topologicalLayout(nodes, edges);
  }, []);

  return { applyLayout };
}
