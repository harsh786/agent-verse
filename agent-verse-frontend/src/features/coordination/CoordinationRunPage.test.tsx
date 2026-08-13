import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import { CoordinationRunPage } from './CoordinationRunPage';

vi.mock('./coordinationApi', () => ({
  coordinationApi: {
    getRun: vi.fn().mockResolvedValue({
      session: { session_id: 's1', tenant_id: 't1', state: 'active', next_sequence: 2, version: 1 },
      messages: { items: [{ message_id: 'm1', sequence: 1, sender_agent_id: 'planner', safe_content: 'Plan accepted' }] },
      ledger: { version: 3, task_state: 'executing', progress_summary: 'One task running' },
      moa: { items: [{ layer_index: 0, quorum_met: true, proposals: [] }] },
      camel: { items: [] },
      generative: { items: [] },
      swarm: { nodes: [{ agent_id: 'planner' }, { agent_id: 'worker' }], edges: [] },
      auction: { items: [], sealed_bid_count: 2 },
    }),
    transition: vi.fn(),
  },
}));

vi.mock('./useCoordinationStream', () => ({
  useCoordinationStream: () => ({ events: [], status: 'live', lastSequence: 1 }),
}));

describe('CoordinationRunPage', () => {
  test('renders the causal timeline and pattern evidence', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/coordination/s1']}>
          <Routes><Route path="/coordination/:sessionId" element={<CoordinationRunPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByRole('heading', { name: /coordination ledger/i })).toBeInTheDocument();
    expect(await screen.findByText('Plan accepted')).toBeInTheDocument();
    expect(screen.getByText('2 sealed bids')).toBeInTheDocument();
    expect(screen.getByLabelText('Swarm topology')).toHaveTextContent('planner');
    expect(screen.getByText(/stream: live/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /parent and child topology/i })).toBeInTheDocument();
  });
});
