import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { LearningLedger } from './LearningLedger';
import type { LearningRecord } from '../../lib/api/civilizationApi';

function record(overrides: Partial<LearningRecord> = {}): LearningRecord {
  return {
    id: 'rec-1',
    candidate: 'Always retry transient 5xx errors.',
    source_agent_id: 'agent-abcdefghijklmnop',
    status: 'candidate',
    eval_score: 0.5,
    promoted_memory_id: null,
    created_at: '2024-01-01T00:00:00Z',
    decided_at: null,
    ...overrides,
  };
}

describe('LearningLedger', () => {
  test('renders empty state when there are no records', () => {
    render(<LearningLedger records={[]} />);
    expect(screen.getByText('No learnings yet')).toBeInTheDocument();
    expect(screen.getByText('The society accumulates learnings from completed goals')).toBeInTheDocument();
  });

  test('renders promoted count and total count summary', () => {
    render(
      <LearningLedger
        records={[record({ id: 'a', status: 'promoted' }), record({ id: 'b', status: 'candidate' }), record({ id: 'c', status: 'promoted' })]}
      />,
    );
    expect(screen.getByText('2 promoted to LTM')).toBeInTheDocument();
    expect(screen.getByText('3 total')).toBeInTheDocument();
  });

  test.each([
    ['candidate', 'Candidate'],
    ['validated', 'Validated'],
    ['promoted', 'Promoted'],
    ['rejected', 'Rejected'],
  ] as const)('renders the %s status configuration', (status, label) => {
    render(<LearningLedger records={[record({ status })]} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  test('falls back to a default indigo config using the raw status label for unknown statuses', () => {
    render(<LearningLedger records={[record({ status: 'archived' as LearningRecord['status'] })]} />);
    expect(screen.getByText('archived')).toBeInTheDocument();
  });

  test('defaults to "Candidate" styling when status is missing from the API payload', () => {
    render(<LearningLedger records={[record({ status: undefined as unknown as LearningRecord['status'] })]} />);
    expect(screen.getByText('Candidate')).toBeInTheDocument();
  });

  test('renders eval score percentage and colors it green above 70%', () => {
    render(<LearningLedger records={[record({ eval_score: 0.9 })]} />);
    const scoreEl = screen.getByText('90%');
    expect(scoreEl).toHaveStyle({ color: '#22c55e' });
  });

  test('colors eval score amber between 41% and 70%', () => {
    render(<LearningLedger records={[record({ eval_score: 0.55 })]} />);
    expect(screen.getByText('55%')).toHaveStyle({ color: '#f59e0b' });
  });

  test('colors eval score red at or below 40%', () => {
    render(<LearningLedger records={[record({ eval_score: 0.3 })]} />);
    expect(screen.getByText('30%')).toHaveStyle({ color: '#ef4444' });
  });

  test('omits the score badge and score bar when eval_score is null', () => {
    render(<LearningLedger records={[record({ eval_score: null })]} />);
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument();
  });

  test('treats an eval_score of 0 as present (renders "0%")', () => {
    render(<LearningLedger records={[record({ eval_score: 0 })]} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  test('shows the LTM badge when promoted_memory_id is set', () => {
    render(<LearningLedger records={[record({ promoted_memory_id: 'ltm-1' })]} />);
    expect(screen.getByText('✓ LTM')).toBeInTheDocument();
  });

  test('hides the LTM badge when promoted_memory_id is null', () => {
    render(<LearningLedger records={[record({ promoted_memory_id: null })]} />);
    expect(screen.queryByText('✓ LTM')).not.toBeInTheDocument();
  });

  test('renders candidate text and truncated source agent id', () => {
    render(<LearningLedger records={[record({ candidate: 'Cache warm queries.', source_agent_id: 'agent-1234567890' })]} />);
    expect(screen.getByText('Cache warm queries.')).toBeInTheDocument();
    expect(screen.getByText(/agent-1234/)).toBeInTheDocument();
  });

  test('handles a missing source_agent_id gracefully', () => {
    render(<LearningLedger records={[record({ source_agent_id: undefined as unknown as string })]} />);
    // Should not throw, and candidate text still renders.
    expect(screen.getByText('Always retry transient 5xx errors.')).toBeInTheDocument();
  });
});
