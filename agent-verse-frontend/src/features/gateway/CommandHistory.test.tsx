import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CommandHistory } from './CommandHistory';

const COMMANDS = [
  { command_id: 'c1', command: 'deploy the frontend', channel: 'telegram', actor_name: 'Alice', status: 'completed', response_text: 'Done', created_at: '2026-01-01T10:00:00Z' },
  { command_id: 'c2', command: 'pause all missions', channel: 'slack', actor_name: 'Bob', status: 'processing', created_at: '2026-01-01T11:00:00Z' },
];

function mockCommands(list: unknown[] = COMMANDS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/commands'))
      return new Response(JSON.stringify({ data: list }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderHistory() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CommandHistory orgId="org-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CommandHistory', () => {
  test('renders command rows and the count', async () => {
    mockCommands();
    renderHistory();
    expect(await screen.findByText('deploy the frontend')).toBeInTheDocument();
    expect(screen.getByText('pause all missions')).toBeInTheDocument();
    expect(screen.getByText('2 commands')).toBeInTheDocument();
  });

  test('shows the empty state when there are no commands', async () => {
    mockCommands([]);
    renderHistory();
    expect(await screen.findByText(/No commands yet/i)).toBeInTheDocument();
  });

  test('the search box filters commands by text', async () => {
    mockCommands();
    renderHistory();
    await screen.findByText('deploy the frontend');
    await userEvent.type(screen.getByLabelText('Search commands'), 'pause');
    await waitFor(() => expect(screen.queryByText('deploy the frontend')).not.toBeInTheDocument());
    expect(screen.getByText('pause all missions')).toBeInTheDocument();
    expect(screen.getByText('1 command')).toBeInTheDocument();
  });

  test('the channel filter narrows rows to a single channel', async () => {
    mockCommands();
    renderHistory();
    await screen.findByText('deploy the frontend');
    await userEvent.selectOptions(screen.getByLabelText('Filter by channel'), 'slack');
    await waitFor(() => expect(screen.queryByText('deploy the frontend')).not.toBeInTheDocument());
    expect(screen.getByText('pause all missions')).toBeInTheDocument();
  });

  test('clicking refresh re-requests the commands endpoint', async () => {
    const spy = mockCommands();
    renderHistory();
    await screen.findByText('deploy the frontend');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/commands')).length;
    await userEvent.click(screen.getByLabelText('Refresh history'));
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/commands')).length).toBeGreaterThan(before),
    );
  });
});
