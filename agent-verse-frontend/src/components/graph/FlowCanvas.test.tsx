import { render, screen } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';

// @xyflow/react renders a real canvas jsdom can't provide — stub the runtime
// exports FlowCanvas imports so we exercise its layout + prop wiring instead.
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="react-flow">{children}</div>
  ),
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  BackgroundVariant: { Dots: 'dots', Lines: 'lines', Cross: 'cross' },
}));

import { FlowCanvas, layeredLayout } from './FlowCanvas';

const X_GAP = 220;
const Y_GAP = 110;

afterEach(() => vi.restoreAllMocks());

describe('layeredLayout', () => {
  test('positions a chain by BFS depth', () => {
    const pos = layeredLayout(
      [
        { id: 'a', label: 'A' },
        { id: 'b', label: 'B' },
      ],
      [{ id: 'e1', source: 'a', target: 'b' }],
    );
    expect(pos.a).toEqual({ x: 0, y: 0 });
    expect(pos.b).toEqual({ x: X_GAP, y: 0 });
  });

  test('stacks sibling roots at depth 0', () => {
    const pos = layeredLayout(
      [
        { id: 'a', label: 'A' },
        { id: 'b', label: 'B' },
      ],
      [],
    );
    expect(pos.a).toEqual({ x: 0, y: 0 });
    expect(pos.b).toEqual({ x: 0, y: Y_GAP });
  });

  test('branches spread across a depth level', () => {
    const pos = layeredLayout(
      [
        { id: 'root', label: 'R' },
        { id: 'l', label: 'L' },
        { id: 'r', label: 'R2' },
      ],
      [
        { id: 'e1', source: 'root', target: 'l' },
        { id: 'e2', source: 'root', target: 'r' },
      ],
    );
    expect(pos.root).toEqual({ x: 0, y: 0 });
    expect(pos.l).toEqual({ x: X_GAP, y: 0 });
    expect(pos.r).toEqual({ x: X_GAP, y: Y_GAP });
  });

  test('an orphan node (unreachable) is placed at depth 0', () => {
    const pos = layeredLayout(
      [
        { id: 'a', label: 'A' },
        { id: 'b', label: 'B' },
        { id: 'orphan', label: 'O' },
      ],
      [{ id: 'e1', source: 'a', target: 'b' }],
    );
    expect(pos.orphan.x).toBe(0);
  });
});

describe('FlowCanvas', () => {
  test('simple mode renders the canvas wrapper and the ReactFlow surface', () => {
    render(
      <FlowCanvas
        nodes={[
          { id: 'a', label: 'Node A' },
          { id: 'b', label: 'Node B' },
        ]}
        edges={[{ id: 'e1', source: 'a', target: 'b' }]}
      />,
    );
    expect(screen.getByTestId('flow-canvas')).toBeInTheDocument();
    expect(screen.getByTestId('react-flow')).toBeInTheDocument();
  });

  test('controlled mode renders with caller-managed nodes/edges and change handlers', () => {
    render(
      <FlowCanvas
        nodes={[{ id: 'a', position: { x: 0, y: 0 }, data: { label: 'A' } }]}
        edges={[]}
        onNodesChange={vi.fn()}
        onEdgesChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('react-flow')).toBeInTheDocument();
  });

  test('applies a custom className on the wrapper', () => {
    render(<FlowCanvas nodes={[]} edges={[]} className="my-canvas" />);
    expect(screen.getByTestId('flow-canvas')).toHaveClass('my-canvas');
  });
});
