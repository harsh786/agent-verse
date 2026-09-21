import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { AuctionBidView } from './AuctionBidView';

describe('AuctionBidView', () => {
  test('renders 0 sealed and no rows when state is empty', () => {
    render(<AuctionBidView state={{}} />);
    expect(screen.getByText('0 sealed')).toBeInTheDocument();
    expect(screen.getByText('Winner')).toBeInTheDocument();
    expect(screen.queryAllByRole('row')).toHaveLength(1); // header only
  });

  test('renders sealed_bid_count and full row data', () => {
    render(
      <AuctionBidView
        state={{
          sealed_bid_count: 4,
          items: [{ allocation_id: 'alloc-1', winner_id: 'agent-x', score: 91, fairness_adjustment: -2 }],
        }}
      />,
    );
    expect(screen.getByText('4 sealed')).toBeInTheDocument();
    expect(screen.getByText('agent-x')).toBeInTheDocument();
    expect(screen.getByText('91')).toBeInTheDocument();
    expect(screen.getByText('-2')).toBeInTheDocument();
  });

  test('falls back to agent_id when winner_id is missing', () => {
    render(<AuctionBidView state={{ items: [{ agent_id: 'agent-fallback' }] }} />);
    expect(screen.getByText('agent-fallback')).toBeInTheDocument();
  });

  test('falls back to "pending" when neither winner_id nor agent_id is present', () => {
    render(<AuctionBidView state={{ items: [{}] }} />);
    expect(screen.getByText('pending')).toBeInTheDocument();
  });

  test('falls back to em-dash for missing score and fairness_adjustment', () => {
    render(<AuctionBidView state={{ items: [{ winner_id: 'a1' }] }} />);
    const dashes = screen.getAllByText('—');
    expect(dashes).toHaveLength(2);
  });

  test('renders multiple rows keyed by index when allocation_id is missing', () => {
    render(
      <AuctionBidView
        state={{ items: [{ winner_id: 'a1' }, { winner_id: 'a2' }] }}
      />,
    );
    expect(screen.getByText('a1')).toBeInTheDocument();
    expect(screen.getByText('a2')).toBeInTheDocument();
  });
});
