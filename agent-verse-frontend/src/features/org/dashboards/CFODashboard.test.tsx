import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { Organization } from '../types';
import { CFODashboard } from './CFODashboard';

const HEALTH = { budget_used_usd: 12.5, pending_approvals: 4, active_missions: 2, active_teams: 1, items_needing_attention: 0 };

function org(overrides: Partial<Organization> = {}): Organization {
  return { id: 'o1', name: 'Acme', monthly_budget_usd: 5000, ...overrides } as Organization;
}

function mockFetch(health: unknown = HEALTH) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/health'))
      return new Response(JSON.stringify(health), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderDash(o = org()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><CFODashboard org={o} /></QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CFODashboard', () => {
  test('renders the finance header with the org name', () => {
    mockFetch();
    renderDash();
    expect(screen.getByRole('heading', { name: /Finance Overview/i })).toBeInTheDocument();
    expect(screen.getByText(/Acme · CFO view/)).toBeInTheDocument();
  });

  test('shows the monthly budget and live compute spend from health data', async () => {
    mockFetch();
    renderDash();
    expect(screen.getByText('$5,000')).toBeInTheDocument();
    // budget_used_usd 12.5 → "$12.50" spend (shown in the KPI and the bar).
    expect((await screen.findAllByText(/\$12\.50/)).length).toBeGreaterThan(0);
    // pending approvals surfaced from health.
    expect(await screen.findByText('4')).toBeInTheDocument();
  });

  test('renders the utilization bar only when a budget is set', async () => {
    mockFetch();
    renderDash();
    expect(await screen.findByText(/Monthly Budget Utilization/i)).toBeInTheDocument();
  });

  test('omits the utilization bar when no budget is configured', () => {
    mockFetch();
    renderDash(org({ monthly_budget_usd: 0 }));
    expect(screen.queryByText(/Monthly Budget Utilization/i)).not.toBeInTheDocument();
  });
});
