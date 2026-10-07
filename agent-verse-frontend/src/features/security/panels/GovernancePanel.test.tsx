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
  policies?: unknown[];
  policiesStatus?: number;
}

function mockFetch(opts: MockOpts = {}) {
  const active = opts.active ?? [];
  const effective = opts.effectiveMode ?? 'fully-autonomous';
  const approvals = opts.approvals ?? [];
  const policies = opts.policies ?? [];
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
    if (url.includes('/governance/policies'))
      return new Response(JSON.stringify(policies), { status: opts.policiesStatus ?? 200, headers: { 'Content-Type': 'application/json' } });
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

  test('time-based rules are the tenant policies with a time window, in their own timezone', async () => {
    mockFetch({
      policies: [
        { policy_id: 'p1', name: 'No deploys out of hours', description: '', tools_pattern: 'deploy_*', action: 'deny', priority: 10,
          allowed_hours_utc: [9, 10, 11, 12, 13, 14, 15, 16], allowed_weekdays: [0, 1, 2, 3, 4], timezone: 'Europe/Berlin' },
        { policy_id: 'p2', name: 'Always approve payments', description: '', tools_pattern: 'pay_*', action: 'require_approval', priority: 5 },
      ],
    });
    renderPanel();
    const rule = await screen.findByTestId('time-rule-p1');
    expect(rule).toHaveTextContent('No deploys out of hours');
    expect(rule).toHaveTextContent('deploy_*');
    expect(rule).toHaveTextContent('Active 09:00–17:00 Europe/Berlin');
    expect(rule).toHaveTextContent('Mon, Tue, Wed, Thu, Fri');
    expect(rule).toHaveTextContent('Deny');
    // No window → not a time-based rule.
    expect(screen.queryByTestId('time-rule-p2')).not.toBeInTheDocument();
    // The removed time_policy module's static text is gone.
    expect(screen.queryByText('No destructive ops overnight')).not.toBeInTheDocument();
    expect(screen.queryByText('No prod deploys on weekends')).not.toBeInTheDocument();
  });

  test('says so when no policy has a time window', async () => {
    mockFetch({ policies: [] });
    renderPanel();
    expect(await screen.findByText(/No time-windowed policies/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Manage policies' })).toHaveAttribute('href', '/governance');
  });

  test('a failing policies request is shown, not an empty list', async () => {
    mockFetch({ policiesStatus: 503 });
    renderPanel();
    expect(await screen.findByText('Could not load policies.')).toBeInTheDocument();
  });
});
