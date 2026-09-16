import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { Organization } from '../types';
import { CEODashboard } from './CEODashboard';

const HEALTH = { health_score_pct: 92, active_missions: 5, pending_approvals: 2, active_teams: 3, items_needing_attention: 1 };
const MISSIONS = {
  data: [
    { id: 'm1', title: 'Expand into EU market', status: 'active' },
    { id: 'm2', title: 'Series B fundraise', status: 'active' },
  ],
  cursor: null,
  hasMore: false,
};

function org(): Organization {
  return { id: 'o1', name: 'Acme' } as Organization;
}

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/health'))
      return new Response(JSON.stringify(HEALTH), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/missions'))
      return new Response(JSON.stringify(MISSIONS), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderDash() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><CEODashboard org={org()} /></QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CEODashboard', () => {
  test('renders the executive header with the org name', () => {
    mockFetch();
    renderDash();
    expect(screen.getByRole('heading', { name: /Executive Overview/i })).toBeInTheDocument();
    expect(screen.getByText(/Acme · CEO view/)).toBeInTheDocument();
  });

  test('shows composite health score and active mission count from health', async () => {
    mockFetch();
    renderDash();
    expect(screen.getByText('Org Health')).toBeInTheDocument();
    // health_score_pct 92 and active_missions 5.
    expect(await screen.findByText('92')).toBeInTheDocument();
    expect(await screen.findByText('5')).toBeInTheDocument();
  });

  test('lists all active missions returned by the API', async () => {
    mockFetch();
    renderDash();
    expect(await screen.findByText('Expand into EU market')).toBeInTheDocument();
    expect(screen.getByText('Series B fundraise')).toBeInTheDocument();
    // The section heading (distinct from the "Active Missions" KPI label).
    expect(screen.getByRole('heading', { name: 'Active Missions' })).toBeInTheDocument();
  });
});
