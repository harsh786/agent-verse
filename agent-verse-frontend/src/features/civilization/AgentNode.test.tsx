/**
 * Tests for AgentNode — the React Flow node card for a civilization agent.
 *
 * AgentNode renders inside React Flow and only depends on `Handle`/`Position`
 * from @xyflow/react, so we stub that module and render the node directly with
 * representative `data` props to exercise its status/reputation/role branches.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Top: 'top', Bottom: 'bottom' },
}));

import { AgentNode } from './AgentNode';

// AgentNode is a memo() wrapper; render it as a component.
function renderNode(data: Record<string, unknown>, selected = false) {
  const Node = AgentNode as unknown as React.ComponentType<{ data: unknown; selected?: boolean }>;
  return render(<Node data={data} selected={selected} />);
}

describe('AgentNode', () => {
  test('renders label, humanized status, depth, role badge, high reputation and cost', () => {
    renderNode({
      label: 'coordinator-01',
      status: 'active',
      reputation: 0.8,
      depth: 0,
      role: 'coordinator',
      budget_spent_usd: 1.2345,
    });
    expect(screen.getByText('coordinator-01')).toBeInTheDocument();
    // STATUS_LABELS maps active → "Active"
    expect(screen.getByText('Active')).toBeInTheDocument();
    // depth badge
    expect(screen.getByText('D:0')).toBeInTheDocument();
    // role badge text
    expect(screen.getByText('coordinator')).toBeInTheDocument();
    // 0.8 → 80% (green threshold)
    expect(screen.getByText('80%')).toBeInTheDocument();
    // cost formatted to 3 decimals
    expect(screen.getByText('$1.234')).toBeInTheDocument();
  });

  test('falls back to the raw status string for an unknown status', () => {
    renderNode({ label: 'weird', status: 'zombie', reputation: 0.5, depth: 2 });
    // STATUS_LABELS has no "zombie" → renders the raw status
    expect(screen.getByText('zombie')).toBeInTheDocument();
    expect(screen.getByText('D:2')).toBeInTheDocument();
    // 0.5 → 50% (amber threshold)
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  test('defaults reputation to 50% when it is not provided', () => {
    // reputation undefined → (0.5) → 50%
    renderNode({ label: 'no-rep', status: 'idle', depth: 1 } as Record<string, unknown>);
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getByText('Idle')).toBeInTheDocument();
  });

  test('renders a low-reputation percentage (red threshold)', () => {
    renderNode({ label: 'low', status: 'failed', reputation: 0.1, depth: 3 });
    expect(screen.getByText('10%')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  test('omits the role badge when no role is set', () => {
    renderNode({ label: 'roleless', status: 'active', reputation: 0.7, depth: 0 });
    expect(screen.getByText('roleless')).toBeInTheDocument();
    // No role → no worker/analyst/coordinator badge text
    expect(screen.queryByText('worker')).not.toBeInTheDocument();
    expect(screen.queryByText('coordinator')).not.toBeInTheDocument();
  });

  test('truncates a long current_step with an ellipsis marker', () => {
    renderNode({
      label: 'busy',
      status: 'active',
      reputation: 0.7,
      depth: 0,
      current_step: 'analyzing the entire production incident timeline carefully',
    });
    // First 22 chars of the step, followed by an ellipsis.
    expect(screen.getByText(/▶ analyzing the entire/)).toBeInTheDocument();
    expect(screen.getByText(/…$/)).toBeInTheDocument();
  });

  test('shows a short current_step without truncation and no cost when budget is absent', () => {
    renderNode({
      label: 'short',
      status: 'idle',
      reputation: 0.6,
      depth: 1,
      current_step: 'planning',
    });
    expect(screen.getByText(/▶ planning/)).toBeInTheDocument();
    // No budget_spent_usd → no dollar-prefixed cost text
    expect(screen.queryByText(/^\$/)).not.toBeInTheDocument();
  });
});
