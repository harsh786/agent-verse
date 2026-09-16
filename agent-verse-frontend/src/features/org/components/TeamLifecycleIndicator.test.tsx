import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TeamLifecycleIndicator } from './TeamLifecycleIndicator';

const LIFECYCLE = {
  team_id: 'team-1',
  current_state: 'execute',
  next_allowed: ['review'],
  all_states: ['create', 'staff', 'brief', 'execute', 'review', 'complete', 'archive'],
};

function mockFetch(payload: unknown = LIFECYCLE) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/lifecycle') && method === 'POST')
      return new Response(JSON.stringify({ team_id: 'team-1', current_state: 'review', next_allowed: ['complete'], all_states: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/lifecycle'))
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderIndicator(props: { compact?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TeamLifecycleIndicator orgId="o1" teamId="team-1" {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TeamLifecycleIndicator', () => {
  test('shows a loading state while the lifecycle request is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderIndicator();
    expect(screen.getByText(/Loading lifecycle…/i)).toBeInTheDocument();
  });

  test('renders the current stage label and description', async () => {
    mockFetch();
    renderIndicator();
    // current_state 'execute' → Execute / "Mission in progress".
    expect(await screen.findByText('Mission in progress')).toBeInTheDocument();
    expect(screen.getAllByText('Execute').length).toBeGreaterThan(0);
  });

  test('advancing posts to the lifecycle endpoint', async () => {
    const spy = mockFetch();
    renderIndicator();
    const advance = await screen.findByRole('button', { name: /Advance to review/i });
    await userEvent.click(advance);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).includes('/teams/team-1/lifecycle') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('a terminal (archive) state shows no advance action', async () => {
    mockFetch({ team_id: 'team-1', current_state: 'archive', next_allowed: [], all_states: [] });
    renderIndicator();
    expect(await screen.findByText('Terminal state')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Advance to/i })).not.toBeInTheDocument();
  });

  test('compact mode renders a compact advance control', async () => {
    mockFetch();
    renderIndicator({ compact: true });
    expect(await screen.findByRole('button', { name: /Advance team to review/i })).toBeInTheDocument();
  });
});
