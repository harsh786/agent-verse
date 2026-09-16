import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// framer-motion: keep real motion components but make animation deterministic.
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

import { CommandHistoryPanel } from './CommandHistoryPanel';

const COMMANDS = [
  { command_id: 'c1', command: 'deploy the frontend', channel: 'rest', status: 'routed', requires_2fa: false, submitted_at: '2026-01-01T10:00:00Z' },
  { command_id: 'c2', command: 'delete prod database', channel: 'slack', status: 'pending_2fa', requires_2fa: true, submitted_at: '2026-01-01T11:00:00Z' },
];

function mockFetch(commands: unknown[] = COMMANDS, opts: { pending?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/command') && method === 'POST')
      return new Response(JSON.stringify({ command_id: 'new', status: 'queued' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/commands')) {
      if (opts.pending) return new Promise(() => {}) as Promise<Response>;
      return new Response(JSON.stringify({ commands, total: commands.length }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CommandHistoryPanel orgId="org-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CommandHistoryPanel', () => {
  test('renders the command rows, total, and status text', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('deploy the frontend')).toBeInTheDocument();
    expect(screen.getByText('delete prod database')).toBeInTheDocument();
    expect(screen.getByText('2 total')).toBeInTheDocument();
    // Status labels underscore-stripped: "pending_2fa" → "pending 2fa".
    expect(screen.getByText('pending 2fa')).toBeInTheDocument();
    // requires_2fa command shows the 2FA pill.
    expect(screen.getByText('2FA')).toBeInTheDocument();
  });

  test('shows the empty state when there are no commands', async () => {
    mockFetch([]);
    renderPanel();
    expect(await screen.findByText(/No commands yet/i)).toBeInTheDocument();
  });

  test('shows the loading state while the request is in flight', () => {
    mockFetch(COMMANDS, { pending: true });
    renderPanel();
    expect(screen.getByText(/Loading commands…/i)).toBeInTheDocument();
  });

  test('sending a command posts to the command endpoint with the typed text', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('deploy the frontend');
    await userEvent.type(screen.getByLabelText('Command to send'), 'restart workers');
    await userEvent.click(screen.getByRole('button', { name: 'Send command' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/v1/org/org-1/command') || (i as RequestInit)?.method !== 'POST') return false;
        return String((i as RequestInit)?.body ?? '').includes('restart workers');
      })).toBe(true),
    );
  });

  test('the channel filter refetches scoped to the chosen channel', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('deploy the frontend');
    await userEvent.selectOptions(screen.getByLabelText('Filter by channel'), 'slack');
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('/commands?channel=slack'))).toBe(true),
    );
  });

  test('the send button is disabled until text is entered', async () => {
    mockFetch();
    renderPanel();
    await screen.findByText('deploy the frontend');
    expect(screen.getByRole('button', { name: 'Send command' })).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Command to send'), 'hello');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Send command' })).toBeEnabled(),
    );
  });
});
