import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { ParentChildTopology } from './ParentChildTopology';

describe('ParentChildTopology', () => {
  test('renders empty state when there are no nodes', () => {
    render(<ParentChildTopology nodes={[]} />);
    expect(screen.getByText('No child executions.')).toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  test('renders safe_summary, state and marks the current node', () => {
    render(
      <ParentChildTopology
        nodes={[{ execution_id: 'x1', safe_summary: 'Fetched data', state: 'running', current: true }]}
      />,
    );
    expect(screen.getByText('Fetched data')).toBeInTheDocument();
    expect(screen.getByText('running · current focus')).toBeInTheDocument();
    const item = screen.getByText('Fetched data').closest('li');
    expect(item).toHaveAttribute('aria-current', 'step');
  });

  test('does not mark aria-current and omits "current focus" text when node.current is falsy', () => {
    render(<ParentChildTopology nodes={[{ execution_id: 'x2', state: 'completed', current: false }]} />);
    const item = screen.getByText('completed').closest('li');
    expect(item).not.toHaveAttribute('aria-current');
    expect(screen.queryByText(/current focus/)).not.toBeInTheDocument();
  });

  test('falls back to agent_id when safe_summary is missing', () => {
    render(<ParentChildTopology nodes={[{ agent_id: 'agent-7' }]} />);
    expect(screen.getByText('agent-7')).toBeInTheDocument();
  });

  test('falls back to a generated "execution-N" label when both safe_summary and agent_id are missing', () => {
    render(<ParentChildTopology nodes={[{}, {}]} />);
    expect(screen.getByText('execution-1')).toBeInTheDocument();
    expect(screen.getByText('execution-2')).toBeInTheDocument();
  });

  test('falls back to "pending" state when state is missing', () => {
    render(<ParentChildTopology nodes={[{ agent_id: 'a1' }]} />);
    expect(screen.getByText('pending')).toBeInTheDocument();
  });
});
