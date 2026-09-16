/**
 * Tests for DebateViewer — a presentational debate transcript.
 *
 * Rendered entirely from props: assert the empty state, the resolved/open
 * stats bar, the claim face-off with confidence meters, the consensus verdict,
 * and collapse/expand behaviour.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { DebateViewer } from './DebateViewer';

const RESOLVED = {
  id: 'd0',
  topic: 'Ship or wait',
  outcome: 'consensus',
  rounds: 3,
  payload: {
    debate_id: 'abcd1234ef',
    consensus: 'Ship it',
    consensus_level: 0.9,
    claim_a: { content: 'Ship now', confidence: 0.7 },
    claim_b: { content: 'Wait a week', confidence: 0.4 },
  },
};

const OPEN = {
  id: 'd1',
  topic: 'Refactor first',
  rounds: 1,
  participants: ['agent-1234567890', 'agent-abcdefghij'],
  payload: {
    status: 'debating',
    claim_a: { content: 'Yes refactor', confidence: 0.6 },
  },
};

describe('DebateViewer', () => {
  test('renders an empty state when there are no debates', () => {
    render(<DebateViewer debates={[]} />);
    expect(screen.getByText('No debates yet')).toBeInTheDocument();
    expect(screen.getByText(/Debates trigger automatically/i)).toBeInTheDocument();
  });

  test('summarises resolved / open / total counts in the stats bar', () => {
    render(<DebateViewer debates={[RESOLVED, OPEN]} />);
    expect(screen.getByText('1 resolved')).toBeInTheDocument();
    expect(screen.getByText('1 open')).toBeInTheDocument();
    expect(screen.getByText('2 total')).toBeInTheDocument();
  });

  test('renders the claim face-off with confidence meters for the first (open) card', () => {
    render(<DebateViewer debates={[RESOLVED]} />);
    expect(screen.getByText('Claim A')).toBeInTheDocument();
    expect(screen.getByText('Ship now')).toBeInTheDocument();
    expect(screen.getByText('Claim B')).toBeInTheDocument();
    expect(screen.getByText('Wait a week')).toBeInTheDocument();
    // Confidence meters render the two claim confidences.
    expect(screen.getByText('70%')).toBeInTheDocument();
    expect(screen.getByText('40%')).toBeInTheDocument();
    // Resolved metadata.
    expect(screen.getByText('Resolved')).toBeInTheDocument();
    expect(screen.getByText('3 rounds')).toBeInTheDocument();
    expect(screen.getByText('#abcd1234')).toBeInTheDocument();
  });

  test('renders the consensus verdict with its confidence level', () => {
    render(<DebateViewer debates={[RESOLVED]} />);
    expect(screen.getByText('Consensus Reached')).toBeInTheDocument();
    expect(screen.getByText('90% confidence')).toBeInTheDocument();
    expect(screen.getByText('Ship it')).toBeInTheDocument();
  });

  test('a non-first card is collapsed until its header is clicked', () => {
    render(<DebateViewer debates={[RESOLVED, OPEN]} />);
    // OPEN is index 1 → collapsed, so its claim body is not shown yet.
    expect(screen.queryByText('Yes refactor')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Refactor first'));
    expect(screen.getByText('Yes refactor')).toBeInTheDocument();
  });

  test('an expanded open debate shows participants and its pending status', () => {
    render(<DebateViewer debates={[OPEN]} />);
    // OPEN is the only (first) card → expanded by default.
    expect(screen.getByText('Participants')).toBeInTheDocument();
    expect(screen.getByText('agent-1234…')).toBeInTheDocument();
    // "debating" appears as both the header badge and the pending-status row.
    expect(screen.getAllByText('debating').length).toBeGreaterThanOrEqual(1);
  });
});
