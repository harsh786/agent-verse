import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import AgentMemoryPage from './AgentMemoryPage';

const MEMORIES = [
  { id: 'm1', content: 'User prefers dark mode', source: 'reflection', created_at: '2026-01-02T10:00:00Z' },
  { id: 'm2', content: 'Deploys happen on Fridays', source: 'execution', created_at: '2026-01-03T10:00:00Z' },
];

function mockFetch(memories = MEMORIES) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/chat/memories') && method === 'GET')
      return new Response(JSON.stringify({ memories }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/chat/memories') && method === 'POST')
      return new Response(JSON.stringify({ id: 'new', content: 'Brand new fact', source: 'user', created_at: '2026-02-01T00:00:00Z' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.match(/\/chat\/memories\/.+/) && method === 'PATCH')
      return new Response(JSON.stringify({ id: 'm1', content: 'Edited content', source: 'reflection', created_at: '2026-01-02T10:00:00Z' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.match(/\/chat\/memories\/.+/) && method === 'DELETE')
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('AgentMemoryPage', () => {
  test('loads and renders memory entries with source, sending the API key', async () => {
    const f = mockFetch();
    render(<AgentMemoryPage />);
    expect(await screen.findByText('User prefers dark mode')).toBeInTheDocument();
    expect(screen.getByText('Deploys happen on Fridays')).toBeInTheDocument();
    expect(screen.getByText(/reflection/)).toBeInTheDocument();
    const firstInit = f.mock.calls[0][1] as RequestInit;
    expect(firstInit.headers).toMatchObject({ 'X-API-Key': 'k' });
  });

  test('shows the empty state when there are no memories', async () => {
    mockFetch([]);
    render(<AgentMemoryPage />);
    expect(await screen.findByText(/No memories yet/i)).toBeInTheDocument();
  });

  test('adding a memory POSTs the content and prepends the result', async () => {
    const spy = mockFetch([]);
    render(<AgentMemoryPage />);
    await screen.findByText(/No memories yet/i);

    await userEvent.click(screen.getByRole('button', { name: /add memory/i }));
    await userEvent.type(screen.getByLabelText('New memory content'), 'Brand new fact');
    await userEvent.click(screen.getByRole('button', { name: /save memory/i }));

    expect(await screen.findByText('Brand new fact')).toBeInTheDocument();
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/chat/memories') && (i as RequestInit)?.method === 'POST'
        && String((i as RequestInit)?.body).includes('Brand new fact'),
      )).toBe(true),
    );
  });

  test('deleting a memory fires a DELETE and removes the row', async () => {
    const spy = mockFetch();
    render(<AgentMemoryPage />);
    const row = (await screen.findByText('User prefers dark mode')).closest('[role="listitem"]') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: /delete memory/i }));

    await waitFor(() => expect(screen.queryByText('User prefers dark mode')).not.toBeInTheDocument());
    expect(spy.mock.calls.some(([u, i]) =>
      /\/chat\/memories\/m1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE',
    )).toBe(true);
  });

  test('editing a memory PATCHes and shows the updated content', async () => {
    const spy = mockFetch();
    render(<AgentMemoryPage />);
    const row = (await screen.findByText('User prefers dark mode')).closest('[role="listitem"]') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: /edit memory/i }));

    const input = within(row).getByDisplayValue('User prefers dark mode');
    await userEvent.clear(input);
    await userEvent.type(input, 'Edited content');
    await userEvent.click(within(row).getByRole('button', { name: /save edit/i }));

    expect(await screen.findByText('Edited content')).toBeInTheDocument();
    expect(spy.mock.calls.some(([u, i]) =>
      /\/chat\/memories\/m1$/.test(String(u)) && (i as RequestInit)?.method === 'PATCH',
    )).toBe(true);
  });
});
