import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { MissionCommandLog } from './MissionCommandLog';

type Ext = { type: string; label: string; detail?: string; cost?: number };

afterEach(() => vi.restoreAllMocks());

describe('MissionCommandLog', () => {
  test('shows the waiting placeholder when there are no events', () => {
    render(<MissionCommandLog externalEntries={[]} />);
    expect(screen.getByText('Waiting for events…')).toBeInTheDocument();
    // The log region is present and empty.
    expect(screen.getByRole('log', { name: /mission command log/i })).toBeInTheDocument();
  });

  test('renders event label, detail and formatted cost from external entries', () => {
    const entries: Ext[] = [
      { type: 'step_complete', label: 'Compiled the report', detail: 'report.pdf', cost: 0.0123 },
    ];
    render(<MissionCommandLog externalEntries={entries} />);
    expect(screen.getByText('Compiled the report')).toBeInTheDocument();
    expect(screen.getByText('report.pdf')).toBeInTheDocument();
    // cost > 0 renders as +$0.0123 (4 decimal places).
    expect(screen.getByText('+$0.0123')).toBeInTheDocument();
    // Placeholder is gone once there is at least one entry.
    expect(screen.queryByText('Waiting for events…')).not.toBeInTheDocument();
  });

  test('does not render a cost chip for a zero-cost event', () => {
    render(<MissionCommandLog externalEntries={[{ type: 'plan_ready', label: 'Plan ready', cost: 0 }]} />);
    expect(screen.getByText('Plan ready')).toBeInTheDocument();
    expect(screen.queryByText(/^\+\$/)).not.toBeInTheDocument();
  });

  test('appends newly-supplied external entries on re-render', () => {
    const first: Ext[] = [{ type: 'step_started', label: 'Step one begins' }];
    const { rerender } = render(<MissionCommandLog externalEntries={first} />);
    expect(screen.getByText('Step one begins')).toBeInTheDocument();
    expect(screen.queryByText('Step two begins')).not.toBeInTheDocument();

    // A new array with an additional entry appended → only the new one is added,
    // and both are now visible in the log.
    rerender(<MissionCommandLog externalEntries={[...first, { type: 'step_started', label: 'Step two begins' }]} />);
    expect(screen.getByText('Step one begins')).toBeInTheDocument();
    expect(screen.getByText('Step two begins')).toBeInTheDocument();
  });
});
