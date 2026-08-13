import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { AuctionBidView } from './AuctionBidView';
import { CodeExecutionView } from './CodeExecutionView';
import { MagenticLedgerView } from './MagenticLedgerView';
import { ReflexionEvidenceView } from './ReflexionEvidenceView';
import { SwarmTopologyView } from './SwarmTopologyView';

describe('safe coordination pattern views', () => {
  test('shows ledger facts and bounded progress without private reasoning', () => {
    render(<MagenticLedgerView ledger={{ version: 4, facts: ['API reachable'], assumptions: ['Cache warm'], stall_count: 1, next_actor: 'worker' }} />);
    expect(screen.getByText('API reachable')).toBeInTheDocument();
    expect(screen.getByText(/next: worker/i)).toBeInTheDocument();
  });

  test('shows fenced swarm claims and public auction factors', () => {
    render(<><SwarmTopologyView nodes={[{ agent_id: 'a1', claim_state: 'leased', fencing_token: 7 }]} edges={[]} /><AuctionBidView state={{ sealed_bid_count: 3, items: [{ winner_id: 'a1', score: 82, fairness_adjustment: 2 }] }} /></>);
    expect(screen.getByText(/fence 7/i)).toBeInTheDocument();
    expect(screen.getByText('82')).toBeInTheDocument();
  });

  test('renders sanitized code and memory evidence', () => {
    render(<><CodeExecutionView executions={[{ language: 'python', state: 'completed', exit_code: 0, stdout_summary: '2 rows' }]} /><ReflexionEvidenceView evidence={[{ applicability: 'matching tool', confidence: 0.8, quarantined: false, provenance: 'goal:g1' }]} /></>);
    expect(screen.getByText('2 rows')).toBeInTheDocument();
    expect(screen.getByText(/80%/)).toBeInTheDocument();
    expect(screen.queryByText(/chain.of.thought/i)).not.toBeInTheDocument();
  });
});
