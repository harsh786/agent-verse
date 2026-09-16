/**
 * Tests for SpawnLineageTimeline — a presentational timeline of spawn decisions.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { SpawnLineageTimeline } from './SpawnLineageTimeline';
import type { SpawnRequest } from '../../lib/api/civilizationApi';

function spawn(overrides: Partial<SpawnRequest> = {}): SpawnRequest {
  return {
    id: 'sp-1',
    requester_agent_id: 'agent-parent-0001',
    requested_capability: 'web_search',
    decision: 'approved',
    reason: 'needed a researcher',
    created_at: '2026-09-16T12:34:56Z',
    ...overrides,
  };
}

describe('SpawnLineageTimeline', () => {
  test('renders an empty state when there are no spawns', () => {
    render(<SpawnLineageTimeline spawns={[]} />);
    expect(screen.getByText('No spawns yet')).toBeInTheDocument();
    expect(screen.getByText(/Spawn events appear when the society creates sub-agents/i)).toBeInTheDocument();
  });

  test('summarises approved and rejected counts', () => {
    render(
      <SpawnLineageTimeline
        spawns={[
          spawn({ id: 'a', decision: 'approved' }),
          spawn({ id: 'b', decision: 'approved' }),
          spawn({ id: 'c', decision: 'denied' }),
        ]}
      />,
    );
    expect(screen.getByText('2 approved')).toBeInTheDocument();
    expect(screen.getByText('1 rejected')).toBeInTheDocument();
  });

  test('does not render a rejected pill when every spawn was approved', () => {
    render(<SpawnLineageTimeline spawns={[spawn({ decision: 'approved' })]} />);
    expect(screen.getByText('1 approved')).toBeInTheDocument();
    expect(screen.queryByText(/rejected/i)).not.toBeInTheDocument();
  });

  test('renders the capability chip, decision badge and reason for each spawn', () => {
    render(<SpawnLineageTimeline spawns={[spawn({ requested_capability: 'code_exec', reason: 'run the build' })]} />);
    expect(screen.getByText('code_exec')).toBeInTheDocument();
    expect(screen.getByText('approved')).toBeInTheDocument();
    expect(screen.getByText('run the build')).toBeInTheDocument();
  });

  test('shows a truncated requester id and a formatted timestamp', () => {
    render(<SpawnLineageTimeline spawns={[spawn({ requester_agent_id: 'agent-parent-0001', created_at: '2026-09-16T12:34:56Z' })]} />);
    // Requester id is sliced to the first 10 chars + ellipsis.
    expect(screen.getByText('agent-pare…')).toBeInTheDocument();
    // created_at is sliced to yyyy-mm-ddThh:mm and the T is replaced with a space.
    expect(screen.getByText('2026-09-16 12:34')).toBeInTheDocument();
  });

  test('falls back to "unknown" capability when the field is empty', () => {
    render(<SpawnLineageTimeline spawns={[spawn({ requested_capability: '' })]} />);
    expect(screen.getByText('unknown')).toBeInTheDocument();
  });
});
