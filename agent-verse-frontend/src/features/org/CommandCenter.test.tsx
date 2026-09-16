import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CommandCenter } from './CommandCenter';

const ORG = { id: 'o1', name: 'Acme Corp', industry: 'Fintech', jurisdiction: 'US' };
const HEALTH = { active_missions: 2, active_teams: 1, pending_approvals: 0, items_needing_attention: 0 };
const EVENTS = { data: [{ id: 'e1', title: 'Mission started', created_at: '2024-01-01T09:00:00Z' }] };
const MISSIONS = { data: [], cursor: null, hasMore: false };

function mockFetch(events: unknown = EVENTS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/missions/execute') && method === 'POST')
      return new Response(JSON.stringify({ mission_id: 'm-new', title: 'Draft mission', status: 'active' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/health'))
      return new Response(JSON.stringify(HEALTH), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/events'))
      return new Response(JSON.stringify(events), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/missions'))
      return new Response(JSON.stringify(MISSIONS), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tasks'))
      return new Response(JSON.stringify({ data: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/v1\/org\/o1$/.test(url))
      return new Response(JSON.stringify(ORG), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderCenter() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CommandCenter orgId="o1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CommandCenter', () => {
  test('renders the org header, health metrics and activity feed', async () => {
    mockFetch();
    renderCenter();
    expect(await screen.findByRole('heading', { name: 'Acme Corp' })).toBeInTheDocument();
    expect(screen.getByText(/Fintech/)).toBeInTheDocument();
    // Health widget surfaces the active-mission count.
    expect(await screen.findByText('Missions')).toBeInTheDocument();
    expect(await screen.findByText('Mission started')).toBeInTheDocument();
  });

  test('shows the empty activity state when there are no events', async () => {
    mockFetch({ data: [] });
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    expect(await screen.findByText('No recent activity')).toBeInTheDocument();
  });

  test('opening the create form and submitting POSTs to the execute endpoint', async () => {
    const spy = mockFetch();
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    await userEvent.click(screen.getByRole('button', { name: /Create new mission/i }));
    const input = await screen.findByPlaceholderText(/Describe what this mission should achieve/i);
    await userEvent.type(input, 'Automate weekly report');
    await userEvent.click(screen.getByRole('button', { name: /Submit mission/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).includes('/missions/execute') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });
});
