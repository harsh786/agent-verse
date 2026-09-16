import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { OrgIntelligence } from './OrgIntelligence';

const WORK = {
  items: [
    {
      type: 'bottleneck',
      severity: 'critical',
      title: 'Approval queue backed up',
      detail: 'Twelve missions are waiting on a human approver.',
      department: 'Finance',
      recommendation: 'Add a second approver',
      impact_estimate: '2x throughput',
    },
  ],
};

const CAPS = {
  items: [
    {
      type: 'opportunity',
      severity: 'info',
      title: 'Enable RAG for support',
      detail: 'Support answers could cite the knowledge base.',
    },
  ],
};

function mockFetch(work: unknown = WORK, caps: unknown = CAPS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/intelligence/work'))
      return new Response(JSON.stringify(work), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/intelligence/capabilities'))
      return new Response(JSON.stringify(caps), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><OrgIntelligence orgId="o1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('OrgIntelligence', () => {
  test('renders normalized insights from both intelligence endpoints', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByRole('heading', { name: /Org Intelligence/i })).toBeInTheDocument();
    expect(await screen.findByText('Approval queue backed up')).toBeInTheDocument();
    expect(screen.getByText('Add a second approver')).toBeInTheDocument();
    expect(screen.getByText('Enable RAG for support')).toBeInTheDocument();
  });

  test('filtering by opportunity hides the bottleneck insight', async () => {
    mockFetch();
    renderPanel();
    await screen.findByText('Approval queue backed up');
    await userEvent.click(screen.getByRole('tab', { name: /Opportunities/i }));
    expect(screen.getByText('Enable RAG for support')).toBeInTheDocument();
    expect(screen.queryByText('Approval queue backed up')).not.toBeInTheDocument();
  });

  test('shows an empty state when no insights are returned', async () => {
    mockFetch({ items: [] }, { items: [] });
    renderPanel();
    expect(await screen.findByText('No insights available yet.')).toBeInTheDocument();
  });

  test('the refresh button re-requests the intelligence endpoints', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('Approval queue backed up');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/intelligence/work')).length;
    await userEvent.click(screen.getByRole('button', { name: /Refresh insights/i }));
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/intelligence/work')).length).toBeGreaterThan(before),
    );
  });
});
