import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { AuctionBidView } from './AuctionBidView';
import { CodeExecutionView } from './CodeExecutionView';
import { MagenticLedgerView } from './MagenticLedgerView';
import { ReflexionEvidenceView } from './ReflexionEvidenceView';
import { SwarmTopologyView } from './SwarmTopologyView';

describe('safe coordination pattern views', () => {
  test('shows the Magentic ledger revision written by a run', () => {
    render(<MagenticLedgerView ledger={{ version: 4, objective: 'Ship report', open_work: ['draft'], completed_work: ['research'], verified_facts: ['API reachable'], blockers: [], reset_count: 1, assignment_history: ['analyst', 'writer'] }} />);
    expect(screen.getByText('API reachable')).toBeInTheDocument();
    expect(screen.getByText('research')).toBeInTheDocument();
    expect(screen.getByText(/resets: 1 · last: writer/i)).toBeInTheDocument();
  });

  test('explains an empty ledger instead of showing blank fields', () => {
    render(<MagenticLedgerView ledger={null} />);
    expect(screen.getByText(/no magentic run on this session yet/i)).toBeInTheDocument();
  });

  test('renders swarm gossip links', () => {
    render(<SwarmTopologyView nodes={[{ agent_id: 'a1' }, { agent_id: 'a2' }]} edges={[{ source: 'a1', target: 'a2', message_type: 'claim', count: 2 }]} />);
    expect(screen.getByText('1 links')).toBeInTheDocument();
    expect(screen.getByText(/a1 → a2 · claim ×2/)).toBeInTheDocument();
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
