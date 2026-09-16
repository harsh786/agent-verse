import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { RunTimeline } from './RunTimeline';
import type { CoordinationEvent } from './types';

function makeEvent(overrides?: Partial<CoordinationEvent>): CoordinationEvent {
  return {
    event_id: 'e1',
    sequence: 1,
    schema_version: 1,
    event_type: 'plan_ready',
    session_id: 's1',
    correlation_id: 'c1',
    producer: 'planner',
    classification: 'info',
    payload: {},
    ...overrides,
  } as CoordinationEvent;
}

describe('RunTimeline', () => {
  it('shows the empty state when there are no events', () => {
    render(<RunTimeline events={[]} />);
    expect(screen.getByText('No live events recorded yet.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Run timeline' })).toBeInTheDocument();
  });

  it('renders a list item per event with sequence, type and producer', () => {
    const events = [
      makeEvent({ event_id: 'e1', sequence: 1, event_type: 'plan_ready', producer: 'planner' }),
      makeEvent({ event_id: 'e2', sequence: 2, event_type: 'tool_call', producer: 'worker' }),
    ];
    render(<RunTimeline events={events} />);
    expect(screen.queryByText('No live events recorded yet.')).not.toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText('#2')).toBeInTheDocument();
    expect(screen.getByText('plan_ready')).toBeInTheDocument();
    expect(screen.getByText('tool_call')).toBeInTheDocument();
    expect(screen.getByText('planner')).toBeInTheDocument();
    expect(screen.getByText('worker')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });
});
