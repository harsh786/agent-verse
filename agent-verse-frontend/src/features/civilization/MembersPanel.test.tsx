/**
 * Tests for MembersPanel — society roster + add/remove member flows.
 *
 * Asserts real rendered roster content and real fetch calls to the correct
 * civilization/agents endpoints (via the apiFetch → fetch path).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MembersPanel } from './MembersPanel';

const MEMBERS = [
  {
    member_id: 'm1', agent_id: 'agent-alpha', role: 'coordinator', reputation: 0.8,
    status: 'active', depth: 0, budget_usd: 20, budget_spent_usd: 2.5,
    spawned_at: '2026-09-16T10:00:00Z', last_active_at: '2026-09-16T11:00:00Z',
    agent_name: 'Alpha Bot', autonomy_mode: 'bounded-autonomous', goal_template: '',
  },
  {
    member_id: 'm2', agent_id: 'agent-beta', role: 'worker', reputation: 0.4,
    status: 'idle', depth: 1, budget_usd: 10, budget_spent_usd: 1.0,
    spawned_at: '2026-09-16T10:00:00Z', last_active_at: '2026-09-16T11:00:00Z',
    agent_name: 'Beta Bot', autonomy_mode: 'supervised', goal_template: '',
  },
];

const AGENTS = [
  { agent_id: 'agent-alpha', name: 'Alpha Bot', autonomy_mode: 'bounded-autonomous' },
  { agent_id: 'agent-gamma', name: 'Gamma Bot', autonomy_mode: 'supervised' },
];

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function mockFetch(members: unknown[] = MEMBERS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method === 'POST' && url.endsWith('/kill')) return json({ killed: 'agent-alpha' });
    if (method === 'POST' && url.endsWith('/members')) return json({ agent_id: 'x', role: 'worker', status: 'active' });
    if (method === 'POST' && url.endsWith('/agents')) return json({ agent_id: 'agent-new', name: 'New Bot' });
    if (url.endsWith('/members')) return json(members);
    if (url.endsWith('/agents')) return json(AGENTS);
    return json({});
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MembersPanel civId="civ-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MembersPanel', () => {
  test('renders the roster with member names, roles and header count', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('Alpha Bot')).toBeInTheDocument();
    expect(screen.getByText('Beta Bot')).toBeInTheDocument();
    expect(screen.getByText(/2 members/i)).toBeInTheDocument();
    // Per-member reputation percentage is rendered from the real value.
    expect(screen.getByText('80% rep')).toBeInTheDocument();
    expect(screen.getByText('40% rep')).toBeInTheDocument();
  });

  test('shows a loading state while the members request is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderPanel();
    expect(screen.getByText(/Loading members…/i)).toBeInTheDocument();
  });

  test('renders an empty state with a first-agent CTA when there are no members', async () => {
    mockFetch([]);
    renderPanel();
    expect(await screen.findByText(/No members yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add First Agent/i })).toBeInTheDocument();
  });

  test('computes quick stats (avg reputation and total spent) from the roster', async () => {
    mockFetch();
    renderPanel();
    await screen.findByText('Alpha Bot');
    // avg reputation = (0.8 + 0.4) / 2 = 0.6 → 60%; total spent = 2.5 + 1.0 = $3.50.
    expect(screen.getByText('60%')).toBeInTheDocument();
    expect(screen.getByText('$3.50')).toBeInTheDocument();
  });

  test('removing a member POSTs to the kill endpoint for that agent', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('Alpha Bot');
    await userEvent.click(screen.getByRole('button', { name: /Remove Alpha Bot/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).includes('/civilizations/civ-1/agents/agent-alpha/kill') &&
          (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('opening the Add Agent modal lists agents not already in the society', async () => {
    mockFetch();
    renderPanel();
    await screen.findByText('Alpha Bot');
    await userEvent.click(screen.getByRole('button', { name: /Add agent to civilization/i }));
    expect(await screen.findByText('Add Agent to Society')).toBeInTheDocument();
    // Gamma Bot is not a member → offered; Alpha (a member) is excluded from the picker.
    expect(await screen.findByText('Gamma Bot')).toBeInTheDocument();
  });

  test('picking an existing agent POSTs it to the members endpoint with its id', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('Alpha Bot');
    await userEvent.click(screen.getByRole('button', { name: /Add agent to civilization/i }));
    await userEvent.click(await screen.findByText('Gamma Bot'));
    await userEvent.click(screen.getByRole('button', { name: /Add to Society/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).endsWith('/civilizations/civ-1/members') &&
          (i as RequestInit)?.method === 'POST' &&
          String((i as RequestInit)?.body ?? '').includes('agent-gamma'),
        ),
      ).toBe(true),
    );
  });

  test('creating a new agent POSTs to /agents then adds it as a member', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('Alpha Bot');
    await userEvent.click(screen.getByRole('button', { name: /Add agent to civilization/i }));
    await screen.findByText('Add Agent to Society');
    await userEvent.click(screen.getByRole('button', { name: /Create New/i }));
    await userEvent.type(screen.getByPlaceholderText(/e\.g\. Jira Analyst/i), 'Delta Bot');
    await userEvent.click(screen.getByRole('button', { name: /Create & Add/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).endsWith('/agents') && (i as RequestInit)?.method === 'POST' &&
          String((i as RequestInit)?.body ?? '').includes('Delta Bot'),
        ),
      ).toBe(true),
    );
    // The freshly-created agent id (from the POST /agents response) is added as a member.
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).endsWith('/civilizations/civ-1/members') &&
          (i as RequestInit)?.method === 'POST' &&
          String((i as RequestInit)?.body ?? '').includes('agent-new'),
        ),
      ).toBe(true),
    );
  });
});
