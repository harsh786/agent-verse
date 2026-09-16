import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

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

import { OrgHistoryNav } from './OrgHistoryNav';

const EVENTS = [
  { id: 'e1', event_type: 'mission.created', title: 'Launch Q1 campaign', severity: 'info', created_at: '2026-01-01T10:00:00Z' },
  { id: 'e2', event_type: 'mission.failed', title: 'Payment sync failed', severity: 'critical', created_at: '2026-01-01T11:00:00Z' },
  { id: 'e3', event_type: 'team.created', title: 'Growth team formed', severity: 'info', created_at: '2026-01-01T12:00:00Z' },
];

function mockFetch(events: unknown[] = EVENTS, opts: { pending?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/events')) {
      if (opts.pending) return new Promise(() => {}) as Promise<Response>;
      return new Response(JSON.stringify({ data: events }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderNav(compact = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><OrgHistoryNav orgId="org-1" compact={compact} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('OrgHistoryNav', () => {
  test('renders the event timeline with titles and count', async () => {
    mockFetch();
    renderNav();
    expect(await screen.findByText('Launch Q1 campaign')).toBeInTheDocument();
    expect(screen.getByText('Payment sync failed')).toBeInTheDocument();
    expect(screen.getByText('Growth team formed')).toBeInTheDocument();
    expect(screen.getByText('3 events')).toBeInTheDocument();
    // The critical event surfaces a severity badge.
    expect(screen.getByText('critical')).toBeInTheDocument();
  });

  test('shows the empty state when no events match', async () => {
    mockFetch([]);
    renderNav();
    expect(await screen.findByText(/No events match your filter/i)).toBeInTheDocument();
  });

  test('shows the loading state while the request is in flight', () => {
    mockFetch(EVENTS, { pending: true });
    renderNav();
    expect(screen.getByText(/Loading history…/i)).toBeInTheDocument();
  });

  test('the search box filters events by title', async () => {
    mockFetch();
    renderNav();
    await screen.findByText('Launch Q1 campaign');
    await userEvent.type(screen.getByLabelText('Search org history'), 'payment');
    await waitFor(() => expect(screen.queryByText('Launch Q1 campaign')).not.toBeInTheDocument());
    expect(screen.getByText('Payment sync failed')).toBeInTheDocument();
    expect(screen.getByText('1 events')).toBeInTheDocument();
  });

  test('the Missions filter tab hides non-mission events', async () => {
    mockFetch();
    renderNav();
    await screen.findByText('Growth team formed');
    await userEvent.click(screen.getByRole('tab', { name: 'Missions' }));
    await waitFor(() => expect(screen.queryByText('Growth team formed')).not.toBeInTheDocument());
    expect(screen.getByText('Launch Q1 campaign')).toBeInTheDocument();
    expect(screen.getByText('Payment sync failed')).toBeInTheDocument();
  });

  test('compact mode hides the search box and filter tabs', async () => {
    mockFetch();
    renderNav(true);
    await screen.findByText('Launch Q1 campaign');
    expect(screen.queryByLabelText('Search org history')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Missions' })).not.toBeInTheDocument();
  });
});
