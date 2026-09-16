import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { StateMachinesPage } from './StateMachinesPage';

const MACHINES = [
  { machine_id: 'sm-1', name: 'Order Flow', state_count: 3 },
  { machine_id: 'sm-2', name: 'Ticket Flow', state_count: 2 },
];

const DETAIL = {
  machine_id: 'sm-1',
  name: 'Order Flow',
  states: [
    { name: 'pending', is_initial: true, is_terminal: false },
    { name: 'processing', is_initial: false, is_terminal: false },
    { name: 'completed', is_initial: false, is_terminal: true },
  ],
  transitions: [
    { from_state: 'pending', to_state: 'processing', event: 'start' },
    { from_state: 'processing', to_state: 'completed', event: 'complete' },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(opts: { list?: unknown[]; error?: boolean; pending?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/state-machines/') && method === 'DELETE') return json({ ok: true });
    if (url.includes('/state-machines/') && method === 'GET') return json(DETAIL);
    if (url.includes('/state-machines') && method === 'POST') return json({ machine_id: 'sm-new' }, 201);
    if (url.includes('/state-machines')) {
      if (opts.error) return json({ error: { message: 'boom' } }, 500);
      if (opts.pending) return new Promise<Response>(() => {});
      return json(opts.list ?? MACHINES);
    }
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><StateMachinesPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('StateMachinesPage', () => {
  test('renders the machine list', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /State Machines/i })).toBeInTheDocument();
    expect(await screen.findByText('Order Flow')).toBeInTheDocument();
    expect(screen.getByText('Ticket Flow')).toBeInTheDocument();
    expect(screen.getByText('3 states')).toBeInTheDocument();
  });

  test('shows the empty state when there are no machines', async () => {
    mockFetch({ list: [] });
    renderPage();
    expect(await screen.findByText(/No state machines yet/i)).toBeInTheDocument();
  });

  test('shows an error banner when the list request fails', async () => {
    mockFetch({ error: true });
    renderPage();
    expect(await screen.findByText(/Failed to load state machines/i)).toBeInTheDocument();
  });

  test('selecting a machine loads and renders its states and transitions', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(await screen.findByText('Order Flow'));
    expect(await screen.findByText('States')).toBeInTheDocument();
    expect(screen.getByText(/pending \(initial\)/i)).toBeInTheDocument();
    expect(screen.getByText(/completed \(terminal\)/i)).toBeInTheDocument();
    expect(screen.getByText('Transitions')).toBeInTheDocument();
    expect(screen.getByText('–start→')).toBeInTheDocument();
  });

  test('delete button DELETEs the chosen machine', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Order Flow');
    await userEvent.click(screen.getByRole('button', { name: /Delete Order Flow/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/state-machines/sm-1') && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });

  test('create modal submits a POST with the parsed name and states', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Order Flow');
    await userEvent.click(screen.getByRole('button', { name: /New State Machine/i }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByPlaceholderText(/Order Flow/i), 'Refund Flow');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => {
      const post = spy.mock.calls.find(([u, i]) =>
        String(u).endsWith('/state-machines') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
      const body = JSON.parse(String((post?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.name).toBe('Refund Flow');
      expect(Array.isArray(body.states)).toBe(true);
      expect(body.states[0].is_initial).toBe(true);
    });
  });
});
