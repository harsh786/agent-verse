/**
 * Tests for GoalExecutionGraph — the React Flow DAG that renders live goal
 * execution. @xyflow/react renders to a real canvas jsdom can't provide, so we
 * stub ReactFlow with a lightweight surface that still invokes the component's
 * own `nodeTypes`/`edgeTypes` (ExecNode / AnimatedEdge) the way React Flow
 * would, so their status/color branches are genuinely exercised rather than
 * skipped.
 */
import { render, screen } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import type { GraphEdge, GraphNode } from '@/hooks/useGoalExecutionGraph';

const { useGoalExecutionGraphMock, useReducedMotionMock } = vi.hoisted(() => ({
  useGoalExecutionGraphMock: vi.fn(),
  useReducedMotionMock: vi.fn(() => false),
}));

vi.mock('@/hooks/useGoalExecutionGraph', () => ({
  useGoalExecutionGraph: useGoalExecutionGraphMock,
}));

vi.mock('@/hooks/useSSEToParticles', () => ({
  useSSEToParticles: vi.fn(),
}));

vi.mock('@/components/canvas/ParticleCanvas', () => ({
  ParticleCanvas: React.forwardRef((props: { width: number; height: number }, _ref) => (
    <div data-testid="particle-canvas" data-width={props.width} data-height={props.height} />
  )),
}));

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => useReducedMotionMock(),
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

// Real React Flow renders to canvas/SVG in ways jsdom can't; stub it with a
// surface that hands each node/edge to the component's own nodeTypes/edgeTypes
// (matching how React Flow actually dispatches), so ExecNode and AnimatedEdge
// get real coverage instead of being skipped entirely.
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, edges, nodeTypes, edgeTypes }: {
    nodes: Array<{ id: string; type: string; data: unknown }>;
    edges: Array<{ id: string; type: string; data: unknown; source: string; target: string }>;
    nodeTypes: Record<string, React.ComponentType<{ id: string; data: unknown }>>;
    edgeTypes: Record<string, React.ComponentType<Record<string, unknown>>>;
  }) => (
    <div data-testid="react-flow">
      {nodes.map((n) => {
        const NodeComp = nodeTypes[n.type];
        return (
          <div key={n.id} data-testid={`node-${n.id}`}>
            <NodeComp id={n.id} data={n.data} />
          </div>
        );
      })}
      {edges.map((e) => {
        const EdgeComp = edgeTypes[e.type];
        return (
          <div key={e.id} data-testid={`edge-${e.id}`}>
            <EdgeComp id={e.id} sourceX={0} sourceY={0} targetX={100} targetY={100} data={e.data} />
          </div>
        );
      })}
    </div>
  ),
  Background: () => null,
  Controls: () => null,
  getBezierPath: () => ['M0,0 L100,100'],
}));

import { GoalExecutionGraph } from './GoalExecutionGraph';

function node(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 'start', type: 'start', label: 'Goal', status: 'active',
    position: { x: 200, y: 20 }, data: {},
    ...overrides,
  };
}

function baseGraph(overrides: Partial<ReturnType<typeof useGoalExecutionGraphMock>> = {}) {
  return {
    nodes: [node()],
    edges: [] as GraphEdge[],
    activeNodeId: 'start',
    tokenStats: { input: 0, output: 0, costUsd: 0, tps: 0 },
    guardrailStats: { fired: 0, blocked: 0 },
    hitlStats: { pending: 0, approved: 0, rejected: 0 },
    ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

describe('GoalExecutionGraph — single node graph', () => {
  test('renders the canvas, particle overlay, and a single node with no HUD pills', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph());
    render(<GoalExecutionGraph events={[]} />);

    expect(screen.getByTestId('react-flow')).toBeInTheDocument();
    expect(screen.getByTestId('particle-canvas')).toBeInTheDocument();
    expect(screen.getByTestId('node-start')).toBeInTheDocument();
    expect(screen.getByText('Goal')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();

    // Zero stats -> none of the HUD pills render.
    expect(screen.queryByText(/tok$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/blocked/)).not.toBeInTheDocument();
    expect(screen.queryByText(/pending/)).not.toBeInTheDocument();
  });

  test('indexes particle positions by step and tool identifiers when present in node data', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      nodes: [
        node(),
        node({ id: 'step-1', type: 'step', status: 'active', label: 'Search the web', data: { step: 'search the web' } }),
        node({ id: 'tool-1', type: 'tool', status: 'done', label: 'search_web', data: { tool: 'search_web' } }),
      ],
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByTestId('node-step-1')).toBeInTheDocument();
    expect(screen.getByTestId('node-tool-1')).toBeInTheDocument();
  });

  test('applies a custom className to the outer container', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph());
    const { container } = render(<GoalExecutionGraph events={[]} className="my-graph" />);
    expect(container.querySelector('.my-graph')).toBeInTheDocument();
  });
});

describe('GoalExecutionGraph — HUD token/guardrail/HITL stats', () => {
  test('shows the token count pill once output tokens are produced', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      tokenStats: { input: 10, output: 128, costUsd: 0.002, tps: 12 },
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByText('128 tok')).toBeInTheDocument();
  });

  test('shows the blocked pill once a guardrail has fired', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      guardrailStats: { fired: 2, blocked: 2, lastRule: 'no-prod-delete' },
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByText(/2 blocked/)).toBeInTheDocument();
  });

  test('shows the pending-approval pill while a HITL request is outstanding', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      hitlStats: { pending: 1, approved: 0, rejected: 0 },
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByText(/1 pending/)).toBeInTheDocument();
  });

  test('shows all three HUD pills together on a busy run', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      tokenStats: { input: 40, output: 512, costUsd: 0.02, tps: 30 },
      guardrailStats: { fired: 1, blocked: 1, lastRule: 'rate-limit' },
      hitlStats: { pending: 3, approved: 1, rejected: 0 },
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByText('512 tok')).toBeInTheDocument();
    expect(screen.getByText(/1 blocked/)).toBeInTheDocument();
    expect(screen.getByText(/3 pending/)).toBeInTheDocument();
  });
});

describe('GoalExecutionGraph — ExecNode status/type rendering', () => {
  test('renders a failed node with output text and no active pulse ring', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      nodes: [node({
        id: 'step-1', type: 'step', label: 'Deploy service', status: 'failed',
        data: { output: 'Connection refused while calling the deploy endpoint' },
      })],
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByText('Deploy service')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.getByText(/Connection refused/)).toBeInTheDocument();
  });

  test('renders every known node type/status combination without crashing', () => {
    const nodes: GraphNode[] = [
      node({ id: 'n-start', type: 'start', status: 'active', label: 'Goal' }),
      node({ id: 'n-plan', type: 'plan', status: 'done', label: 'Plan (3 steps)' }),
      node({ id: 'n-tool', type: 'tool', status: 'done', label: 'search_web' }),
      node({ id: 'n-verify', type: 'verify', status: 'done', label: 'Verified' }),
      node({ id: 'n-hitl', type: 'hitl', status: 'waiting', label: 'Awaiting Approval' }),
      node({ id: 'n-guardrail', type: 'guardrail', status: 'blocked', label: 'Blocked: policy' }),
      node({ id: 'n-knowledge', type: 'knowledge', status: 'done', label: 'Knowledge hit' }),
      node({ id: 'n-replan', type: 'replan', status: 'active', label: 'Replanning…' }),
      node({ id: 'n-complete', type: 'complete', status: 'done', label: 'Complete' }),
      node({ id: 'n-failed', type: 'failed', status: 'failed', label: 'Failed' }),
      node({ id: 'n-pending', type: 'step', status: 'pending', label: 'Waiting to start' }),
      // Unknown node type falls back to the `step` visual config.
      node({ id: 'n-unknown', type: 'mystery' as GraphNode['type'], status: 'pending', label: 'Unknown type' }),
    ];
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({ nodes }));
    render(<GoalExecutionGraph events={[]} />);

    for (const n of nodes) {
      expect(screen.getByTestId(`node-${n.id}`)).toBeInTheDocument();
    }
    expect(screen.getByText('Unknown type')).toBeInTheDocument();
  });

  test('suppresses the active pulse ring when reduced motion is preferred', () => {
    useReducedMotionMock.mockReturnValue(true);
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      nodes: [node({ id: 'n-active', type: 'step', status: 'active', label: 'Running step' })],
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByText('Running step')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
  });
});

describe('GoalExecutionGraph — AnimatedEdge rendering', () => {
  test('renders an edge with an explicit color and one with the default fallback color', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      nodes: [
        node({ id: 'a', label: 'A' }),
        node({ id: 'b', label: 'B' }),
        node({ id: 'c', label: 'C' }),
      ],
      edges: [
        { id: 'e-a-b', source: 'a', target: 'b', animated: true, color: '#00E676' },
        // No color -> AnimatedEdge falls back to its default stroke color.
        { id: 'e-b-c', source: 'b', target: 'c', animated: false, color: undefined as unknown as string },
      ],
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByTestId('edge-e-a-b')).toBeInTheDocument();
    expect(screen.getByTestId('edge-e-b-c')).toBeInTheDocument();
  });

  test('handles a self-referencing edge (a node pointing back at itself) without crashing', () => {
    useGoalExecutionGraphMock.mockReturnValue(baseGraph({
      nodes: [node({ id: 'a', label: 'A' })],
      edges: [{ id: 'e-a-a', source: 'a', target: 'a', animated: true, color: '#FFB300' }],
    }));
    render(<GoalExecutionGraph events={[]} />);
    expect(screen.getByTestId('edge-e-a-a')).toBeInTheDocument();
  });
});
