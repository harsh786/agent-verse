import { renderHook } from '@testing-library/react';
import type { Node, Edge } from '@xyflow/react';
import { describe, expect, test } from 'vitest';
import { useAutoLayout } from './useAutoLayout';

// Layout constants mirrored from the implementation.
const X0 = 60;
const Y0 = 60;
const COL = 210 + 80; // NODE_WIDTH + H_GAP
const ROW = 70 + 40; // NODE_HEIGHT + V_GAP

function node(id: string): Node {
  return { id, position: { x: -1, y: -1 }, data: {} } as unknown as Node;
}
function edge(source: string, target: string): Edge {
  return { id: `${source}-${target}`, source, target } as unknown as Edge;
}

function layout(nodes: Node[], edges: Edge[]) {
  const { result } = renderHook(() => useAutoLayout());
  return result.current.applyLayout(nodes, edges);
}

function posOf(nodes: Node[], id: string) {
  return nodes.find((n) => n.id === id)!.position;
}

describe('useAutoLayout', () => {
  test('returns the inputs unchanged for an empty graph', () => {
    const out = layout([], []);
    expect(out.nodes).toEqual([]);
    expect(out.edges).toEqual([]);
  });

  test('places a linear chain in left-to-right columns at the same row', () => {
    const nodes = [node('a'), node('b'), node('c')];
    const edges = [edge('a', 'b'), edge('b', 'c')];
    const out = layout(nodes, edges);
    expect(posOf(out.nodes, 'a')).toEqual({ x: X0, y: Y0 });
    expect(posOf(out.nodes, 'b')).toEqual({ x: X0 + COL, y: Y0 });
    expect(posOf(out.nodes, 'c')).toEqual({ x: X0 + 2 * COL, y: Y0 });
  });

  test('stacks independent roots vertically in the first column', () => {
    const out = layout([node('a'), node('b')], []);
    expect(posOf(out.nodes, 'a')).toEqual({ x: X0, y: Y0 });
    expect(posOf(out.nodes, 'b')).toEqual({ x: X0, y: Y0 + ROW });
  });

  test('a diamond puts the join node in the deepest column', () => {
    const nodes = [node('a'), node('b'), node('c'), node('d')];
    const edges = [edge('a', 'b'), edge('a', 'c'), edge('b', 'd'), edge('c', 'd')];
    const out = layout(nodes, edges);
    expect(posOf(out.nodes, 'a')).toEqual({ x: X0, y: Y0 });
    // b and c share column 1, stacked in two rows.
    expect(posOf(out.nodes, 'b')).toEqual({ x: X0 + COL, y: Y0 });
    expect(posOf(out.nodes, 'c')).toEqual({ x: X0 + COL, y: Y0 + ROW });
    // d is one level past the deepest of its parents.
    expect(posOf(out.nodes, 'd')).toEqual({ x: X0 + 2 * COL, y: Y0 });
  });

  test('preserves the edges array', () => {
    const edges = [edge('a', 'b')];
    const out = layout([node('a'), node('b')], edges);
    expect(out.edges).toBe(edges);
  });
});
