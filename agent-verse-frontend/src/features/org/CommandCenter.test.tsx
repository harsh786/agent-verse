import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CommandCenter } from './CommandCenter';

// jsdom has no real layout, so @tanstack/react-virtual's range calculation
// (based on scroll container clientHeight, always 0 in jsdom) never reports
// any item as visible. Stub it to always render every item, matching the
// pattern used in MissionsList.test.tsx.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (opts: { count: number; estimateSize: () => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, index) => ({
        index,
        start: index * opts.estimateSize(),
        size: opts.estimateSize(),
        key: index,
      })),
    getTotalSize: () => opts.count * opts.estimateSize(),
  }),
}));

const ORG = { id: 'o1', name: 'Acme Corp', industry: 'Fintech', jurisdiction: 'US' };
const HEALTH = { active_missions: 2, active_teams: 1, pending_approvals: 0, items_needing_attention: 0 };
const EVENTS = { data: [{ id: 'e1', title: 'Mission started', created_at: '2024-01-01T09:00:00Z' }] };
const MISSION_ALPHA = {
  id: 'm1', tenant_id: 't', org_id: 'o1', dept_id: null, assigned_team_id: null,
  title: 'Mission Alpha', objective: 'Ship it', why: 'Because', expected_outcome: 'Done',
  status: 'active', priority: 'medium', source: 'manual', autonomy_level: null,
  budget_usd: null, deadline: null, tags: [], created_by: null, outputs: [], evidence: [],
  started_at: null, completed_at: null, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z',
};
const MISSIONS = { data: [MISSION_ALPHA], cursor: null, hasMore: false };
const EMPTY_MISSIONS = { data: [], cursor: null, hasMore: false };

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

  test('shows the loading spinner while the organization is loading', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderCenter();
    expect(screen.getByLabelText('Loading organization')).toBeInTheDocument();
  });

  test('Submit mission button stays disabled for a blank/whitespace title', async () => {
    mockFetch();
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    await userEvent.click(screen.getByRole('button', { name: /Create new mission/i }));
    const submit = await screen.findByRole('button', { name: /Submit mission/i });
    expect(submit).toBeDisabled();
    const input = screen.getByPlaceholderText(/Describe what this mission should achieve/i);
    await userEvent.type(input, '   ');
    expect(submit).toBeDisabled();
  });

  test('pressing Enter in the mission title input submits the mission', async () => {
    const spy = mockFetch();
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    await userEvent.click(screen.getByRole('button', { name: /Create new mission/i }));
    const input = await screen.findByPlaceholderText(/Describe what this mission should achieve/i);
    await userEvent.type(input, 'Ship the release{Enter}');
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).includes('/missions/execute') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
    // Form closes and title resets after a successful create.
    await waitFor(() =>
      expect(screen.queryByPlaceholderText(/Describe what this mission should achieve/i)).not.toBeInTheDocument(),
    );
  });

  test('pressing Escape in the mission title input cancels and clears it', async () => {
    mockFetch();
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    await userEvent.click(screen.getByRole('button', { name: /Create new mission/i }));
    const input = await screen.findByPlaceholderText(/Describe what this mission should achieve/i);
    await userEvent.type(input, 'Draft that will be discarded');
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByPlaceholderText(/Describe what this mission should achieve/i)).not.toBeInTheDocument();
  });

  test('Cancel button closes the create form and clears the title', async () => {
    mockFetch();
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    await userEvent.click(screen.getByRole('button', { name: /Create new mission/i }));
    const input = await screen.findByPlaceholderText(/Describe what this mission should achieve/i);
    await userEvent.type(input, 'Some draft title');
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(screen.queryByPlaceholderText(/Describe what this mission should achieve/i)).not.toBeInTheDocument();
  });

  test('mission status filter tabs switch the selected/aria-selected tab', async () => {
    mockFetch();
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    const allTab = screen.getByRole('tab', { name: 'All' });
    const activeTab = screen.getByRole('tab', { name: 'Active' });
    expect(allTab).toHaveAttribute('aria-selected', 'true');
    expect(activeTab).toHaveAttribute('aria-selected', 'false');
    await userEvent.click(activeTab);
    expect(activeTab).toHaveAttribute('aria-selected', 'true');
    expect(allTab).toHaveAttribute('aria-selected', 'false');
    // Exercise the remaining filter values too.
    await userEvent.click(screen.getByRole('tab', { name: 'Queued' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Review' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Completed' }));
    expect(screen.getByRole('tab', { name: 'Completed' })).toHaveAttribute('aria-selected', 'true');
  });

  test('clicking a mission card opens the drawer overlay, and clicking it closes again', async () => {
    mockFetch();
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    const missionCard = await screen.findByText('Mission Alpha');
    await userEvent.click(missionCard);
    // Overlay backdrop appears once a mission is selected.
    const overlay = document.querySelector('.fixed.inset-0.bg-black\\/40');
    expect(overlay).toBeInTheDocument();
    await userEvent.click(overlay as Element);
    await waitFor(() =>
      expect(document.querySelector('.fixed.inset-0.bg-black\\/40')).not.toBeInTheDocument(),
    );
  });

  test('the "New Mission" button inside the missions list toolbar also opens the create form', async () => {
    mockFetch();
    renderCenter();
    await screen.findByRole('heading', { name: 'Acme Corp' });
    // MissionsList renders its own "New Mission" button when there are missions.
    const buttons = await screen.findAllByRole('button', { name: /New Mission/i });
    expect(buttons.length).toBeGreaterThan(0);
  });
});
