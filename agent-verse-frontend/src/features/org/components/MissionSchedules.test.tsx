import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MissionSchedules } from './MissionSchedules';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

const SCHEDULE = {
  id: 'sch-1',
  org_id: 'o1',
  name: '',
  title: 'Morning digest',
  objective: 'Summarise overnight activity',
  priority: 'medium',
  autonomy_level: null,
  cron_expression: '0 8 * * *',
  timezone: 'UTC',
  enabled: true,
  next_fire_at: '2099-01-01T08:00:00Z',
  last_fired_at: null,
  last_mission_id: null,
  fire_count: 3,
  created_at: null,
  publish: { connector_server_id: 'builtin-utility', tool_name: 'http_request', arguments: {}, approved: false },
};

function mockFetch(schedules: unknown[] = [SCHEDULE]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (/\/schedules\/[^/]+\/approve-publishing$/.test(url) && method === 'POST')
      return new Response(JSON.stringify({ ...SCHEDULE, released_missions: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/schedules\/[^/]+$/.test(url) && method === 'PATCH')
      return new Response(JSON.stringify({ ...SCHEDULE, enabled: false }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/schedules\/[^/]+$/.test(url) && method === 'DELETE')
      return new Response(null, { status: 204 });
    if (/\/schedules$/.test(url) && method === 'POST')
      return new Response(JSON.stringify(SCHEDULE), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/schedules$/.test(url))
      return new Response(JSON.stringify(schedules), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderSchedules() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MissionSchedules orgId="o1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MissionSchedules', () => {
  test('renders the schedule list with cadence, run count and publish target', async () => {
    mockFetch();
    renderSchedules();
    expect(screen.getByRole('heading', { name: /Scheduled Missions/i })).toBeInTheDocument();
    expect(await screen.findByText('Morning digest')).toBeInTheDocument();
    expect(screen.getByText('0 8 * * *')).toBeInTheDocument();
    expect(screen.getByText('3 runs')).toBeInTheDocument();
    expect(screen.getByText(/Publishes via/)).toBeInTheDocument();
    expect(screen.getByText('http_request')).toBeInTheDocument();
  });

  test('renders the empty state when there are no schedules', async () => {
    mockFetch([]);
    renderSchedules();
    expect(await screen.findByText('No scheduled missions yet.')).toBeInTheDocument();
  });

  test('create is disabled until a title is entered, then POSTs the schedule', async () => {
    const spy = mockFetch([]);
    renderSchedules();
    await screen.findByText('No scheduled missions yet.');
    const create = screen.getByRole('button', { name: /Schedule mission/i });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'Daily LinkedIn post' } });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          if (!/\/schedules$/.test(String(u)) || (i as RequestInit)?.method !== 'POST') return false;
          return JSON.parse(String((i as RequestInit).body)).title === 'Daily LinkedIn post';
        }),
      ).toBe(true),
    );
  });

  test('pausing a schedule PATCHes it', async () => {
    const spy = mockFetch();
    renderSchedules();
    await screen.findByText('Morning digest');
    fireEvent.click(screen.getByRole('button', { name: /Pause Morning digest/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/schedules\/sch-1$/.test(String(u)) && (i as RequestInit)?.method === 'PATCH'),
      ).toBe(true),
    );
  });

  test('deleting a schedule DELETEs it', async () => {
    const spy = mockFetch();
    renderSchedules();
    await screen.findByText('Morning digest');
    fireEvent.click(screen.getByRole('button', { name: /Delete Morning digest/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/schedules\/sch-1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE'),
      ).toBe(true),
    );
  });

  test('approving publishing POSTs to approve-publishing', async () => {
    const spy = mockFetch();
    renderSchedules();
    await screen.findByText('Morning digest');
    fireEvent.click(screen.getByRole('button', { name: /Approve publishing/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/schedules\/sch-1\/approve-publishing$/.test(String(u)) && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });
});
