import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GovernancePanel } from './GovernancePanel';

interface MockOpts {
  active?: string[];
  effectiveMode?: string;
  approvals?: unknown[];
}

function mockFetch(opts: MockOpts = {}) {
  const active = opts.active ?? [];
  const effective = opts.effectiveMode ?? 'fully-autonomous';
  const approvals = opts.approvals ?? [];
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/trust/compliance-bundles/') && url.includes('/enable') && method === 'POST')
      return new Response(JSON.stringify({ active, effective_max_autonomy: effective }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/trust/compliance-bundles/active'))
      return new Response(JSON.stringify({ active, effective_max_autonomy: effective }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/trust/approvals'))
      // Retired (a03-F057-01): the panel must never call it.
      return new Response(JSON.stringify({ detail: { code: 'TRUST_APPROVALS_RETIRED' } }), { status: 410, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/governance/approvals'))
      return new Response(JSON.stringify(approvals), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><GovernancePanel /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('GovernancePanel', () => {
  test('renders the compliance bundle catalogue and section headings', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('Compliance Bundles')).toBeInTheDocument();
    for (const name of ['HIPAA', 'GDPR', 'SOC 2', 'India DPDP', 'PCI-DSS']) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.getByText('Pending Approvals')).toBeInTheDocument();
    expect(screen.getByText('Time-Based Rules')).toBeInTheDocument();
  });

  test('an active bundle shows Enabled and the effective mode is displayed', async () => {
    mockFetch({ active: ['hipaa'], effectiveMode: 'supervised' });
    renderPanel();
    await screen.findByText('HIPAA');
    await waitFor(() => expect(screen.getByText('Enabled')).toBeInTheDocument());
    expect(screen.getByText('supervised')).toBeInTheDocument();
    // The other four bundles are still toggle-able (not yet enabled).
    expect(screen.getAllByText('Enable')).toHaveLength(4);
  });

  test('pending approvals come from the HITL gateway (/governance/approvals), never /trust/approvals', async () => {
    const spy = mockFetch({
      approvals: [
        { request_id: 'r1', action: 'delete prod database', goal_id: 'g-9', risk_level: 'critical', required_approvers: 2, approvals_received: 1, status: 'pending' },
      ],
    });
    renderPanel();
    expect(await screen.findByText('delete prod database')).toBeInTheDocument();
    expect(screen.getByText('1 pending')).toBeInTheDocument();
    expect(screen.getByText(/Goal: g-9/)).toBeInTheDocument();
    expect(screen.getByText(/Approvals: 1\/2/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Review in Approvals' })).toHaveAttribute('href', '/approvals');
    const urls = spy.mock.calls.map(([u]) => String(u));
    expect(urls.some((u) => u.includes('/governance/approvals'))).toBe(true);
    expect(urls.some((u) => u.includes('/trust/approvals'))).toBe(false);
  });

  test('shows the empty approvals state when none are pending', async () => {
    mockFetch({ approvals: [] });
    renderPanel();
    expect(await screen.findByText(/No pending approvals/i)).toBeInTheDocument();
  });

  test('clicking Enable on an inactive bundle posts to its enable endpoint', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('HIPAA');
    // First "Enable" button belongs to HIPAA (first in the BUNDLES list).
    await userEvent.click(screen.getAllByRole('button', { name: 'Enable' })[0]);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/trust/compliance-bundles/hipaa/enable') &&
        (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
  });

  test('renders the static time-based rules', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('No destructive ops overnight')).toBeInTheDocument();
    expect(screen.getByText('No prod deploys on weekends')).toBeInTheDocument();
  });
});
