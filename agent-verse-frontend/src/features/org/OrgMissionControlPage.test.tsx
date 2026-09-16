import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// AgentConstellation renders d3-force + a live particle canvas that jsdom can't
// drive; stub it to a marker so the page's own layout/data wiring is tested.
vi.mock('./components/AgentConstellation', () => ({
  AgentConstellation: (props: { agents?: unknown[] }) => (
    <div data-testid="agent-constellation" data-agent-count={(props.agents ?? []).length} />
  ),
}));

import { OrgMissionControlPage } from './OrgMissionControlPage';

const ORG = { id: 'o1', name: 'Nebula Labs', industry: 'SaaS' };
const HEALTH = { active_missions: 3, active_teams: 2, pending_approvals: 1, items_needing_attention: 0, health: 'healthy' };
const MISSIONS = {
  data: [{ id: 'm1', title: 'Ship v2', status: 'active' }],
  cursor: null,
  hasMore: false,
};

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/health'))
      return new Response(JSON.stringify(HEALTH), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/missions'))
      return new Response(JSON.stringify(MISSIONS), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/v1\/org\/o1$/.test(url))
      return new Response(JSON.stringify(ORG), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><OrgMissionControlPage orgId="o1" /></QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('OrgMissionControlPage', () => {
  test('renders the org name header and JARVIS subtitle', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Nebula Labs' })).toBeInTheDocument();
    expect(screen.getByText('JARVIS Neural Operations')).toBeInTheDocument();
  });

  test('surfaces health metrics and the active-mission chip', async () => {
    mockFetch();
    renderPage();
    // OrgHealthWidget renders the Missions metric label.
    expect(await screen.findByText('Missions')).toBeInTheDocument();
    // Top-bar chip appears because active_missions > 0.
    expect(await screen.findByText('3 active')).toBeInTheDocument();
  });

  test('renders the (stubbed) agent constellation and command log panels', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByTestId('agent-constellation')).toBeInTheDocument();
    expect(screen.getByRole('log', { name: /mission command log/i })).toBeInTheDocument();
  });

  test('clicking refresh refetches the organization', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: 'Nebula Labs' });
    const orgFetches = () => spy.mock.calls.filter(([u]) => /\/v1\/org\/o1$/.test(String(u))).length;
    const before = orgFetches();
    await userEvent.click(screen.getByRole('button', { name: /refresh/i }));
    await waitFor(() => expect(orgFetches()).toBeGreaterThan(before));
  });
});
