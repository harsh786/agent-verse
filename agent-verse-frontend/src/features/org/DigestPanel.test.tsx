import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { DigestPanel } from './DigestPanel';

const DIGEST = {
  org_id: 'o1',
  tenant_id: 't',
  since: '2024-01-01T22:00:00Z',
  generated_at: '2024-01-02T07:00:00Z',
  completed_missions: [
    { category: 'completed', title: 'Shipped the API', summary: 'Deployed v2', icon: '✅', priority: 1, action_required: false, action_type: null, related_id: null, cost_usd: 2.5, duration_str: '2h' },
  ],
  completed_tasks: [],
  pending_approvals: [
    { category: 'needs_attention', title: 'Approve prod deploy', summary: 'Waiting on you', icon: '⚠️', priority: 1, action_required: true, action_type: 'approval', related_id: 'x1', cost_usd: null, duration_str: null },
  ],
  blocked_items: [],
  budget_alerts: [],
  insights: [
    { category: 'insight', title: 'Queue bottleneck', summary: 'Approvals are slow', icon: '📈', priority: 1, action_required: false, action_type: null, related_id: null, cost_usd: null, duration_str: null },
  ],
  total_cost_usd: 5,
  missions_completed: 1,
  missions_started: 2,
  agents_active: 3,
  decisions_made: 4,
  summary_text: 'You had a productive night.',
};

function mockFetch(digest: unknown = DIGEST) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/brief/morning'))
      return new Response(JSON.stringify(digest), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel(onClose?: () => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><DigestPanel orgId="o1" onClose={onClose} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('DigestPanel', () => {
  test('renders the summary text and the headline stats', async () => {
    mockFetch();
    renderPanel();
    expect(screen.getByRole('heading', { name: /While You Were Away/i })).toBeInTheDocument();
    expect(await screen.findByText('You had a productive night.')).toBeInTheDocument();
    // Stats
    expect(screen.getByText('$5.00')).toBeInTheDocument();
    expect(screen.getByText('Decisions')).toBeInTheDocument();
    expect(screen.getByText('Started')).toBeInTheDocument();
  });

  test('groups items into needs-attention, completed and insights', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('Approve prod deploy')).toBeInTheDocument();
    expect(screen.getByText('Action needed')).toBeInTheDocument();
    expect(screen.getByText('Shipped the API')).toBeInTheDocument();
    expect(screen.getByText('Queue bottleneck')).toBeInTheDocument();
  });

  test('shows a fallback when no digest is available', async () => {
    mockFetch(null);
    renderPanel();
    expect(await screen.findByText('No digest available')).toBeInTheDocument();
  });

  test('the close button invokes onClose', async () => {
    mockFetch();
    const onClose = vi.fn();
    renderPanel(onClose);
    await screen.findByText('You had a productive night.');
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('the refresh button re-requests the digest endpoint', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('You had a productive night.');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/brief/morning')).length;
    await userEvent.click(screen.getByRole('button', { name: /Refresh digest/i }));
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/brief/morning')).length).toBeGreaterThan(before),
    );
  });
});
