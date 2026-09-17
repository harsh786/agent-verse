import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
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

  test('shows a loading skeleton before the schedules resolve', () => {
    mockFetch();
    const { container } = renderSchedules();
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(3);
  });

  test('switching to a custom cadence reveals a raw cron field and gates creation on it', async () => {
    mockFetch([]);
    renderSchedules();
    await screen.findByText('No scheduled missions yet.');
    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'Custom cadence mission' } });
    const create = screen.getByRole('button', { name: /Schedule mission/i });
    expect(create).toBeEnabled();

    expect(screen.queryByLabelText(/Custom cron expression/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Cadence/i), { target: { value: 'custom' } });
    expect(screen.getByLabelText(/Custom cron expression/i)).toBeInTheDocument();
    expect(create).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Custom cron expression/i), { target: { value: '30 7 * * 1-5' } });
    expect(create).toBeEnabled();

    fireEvent.change(screen.getByLabelText(/Cadence/i), { target: { value: '0 21 * * *' } });
    expect(screen.queryByLabelText(/Custom cron expression/i)).not.toBeInTheDocument();
  });

  test('toggling on auto-publish reveals connector/tool/args fields and posts them on create', async () => {
    const spy = mockFetch([]);
    renderSchedules();
    await screen.findByText('No scheduled missions yet.');
    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'Publishing mission' } });

    expect(screen.queryByLabelText(/Publish connector server id/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Publish each result automatically/i));
    expect(screen.getByLabelText(/Publish connector server id/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Publish connector server id/i), { target: { value: 'my-connector' } });
    fireEvent.change(screen.getByLabelText(/Publish tool name/i), { target: { value: 'slack_post' } });
    fireEvent.change(screen.getByLabelText(/Publish arguments/i), { target: { value: '{"channel":"general"}' } });

    fireEvent.click(screen.getByRole('button', { name: /Schedule mission/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          if (!/\/schedules$/.test(String(u)) || (i as RequestInit)?.method !== 'POST') return false;
          const body = JSON.parse(String((i as RequestInit).body));
          return body.publish?.tool_name === 'slack_post' && body.publish?.connector_server_id === 'my-connector';
        }),
      ).toBe(true),
    );
  });

  test('shows an error when publish arguments are invalid JSON', async () => {
    mockFetch([]);
    renderSchedules();
    await screen.findByText('No scheduled missions yet.');
    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'Bad JSON mission' } });
    fireEvent.click(screen.getByText(/Publish each result automatically/i));
    fireEvent.change(screen.getByLabelText(/Publish arguments/i), { target: { value: 'not json' } });

    fireEvent.click(screen.getByRole('button', { name: /Schedule mission/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Publish arguments must be valid JSON.');
  });

  test('shows an error when publishing is on but connector or tool is missing', async () => {
    mockFetch([]);
    renderSchedules();
    await screen.findByText('No scheduled missions yet.');
    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'No connector mission' } });
    fireEvent.click(screen.getByText(/Publish each result automatically/i));
    fireEvent.change(screen.getByLabelText(/Publish connector server id/i), { target: { value: '  ' } });

    fireEvent.click(screen.getByRole('button', { name: /Schedule mission/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Add a connector and a tool to publish the result.');
  });

  test('editing the title after an error clears it', async () => {
    mockFetch([]);
    renderSchedules();
    await screen.findByText('No scheduled missions yet.');
    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'Bad JSON mission 2' } });
    fireEvent.click(screen.getByText(/Publish each result automatically/i));
    fireEvent.change(screen.getByLabelText(/Publish arguments/i), { target: { value: 'not json' } });
    fireEvent.click(screen.getByRole('button', { name: /Schedule mission/i }));
    await screen.findByRole('alert');

    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'Bad JSON mission 2 edited' } });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  test('a paused schedule shows Resume, "paused" and hides the run count when it is zero', async () => {
    mockFetch([{ ...SCHEDULE, enabled: false, fire_count: 0, publish: null }]);
    renderSchedules();
    const item = (await screen.findByText('Morning digest')).closest('li') as HTMLElement;
    expect(within(item).getByRole('button', { name: /Resume Morning digest/i })).toBeInTheDocument();
    expect(within(item).getByText('paused')).toBeInTheDocument();
    expect(within(item).queryByText(/run/)).not.toBeInTheDocument();
    expect(within(item).queryByText(/Publishes via/)).not.toBeInTheDocument();
  });

  test('a schedule with a single run uses singular wording', async () => {
    mockFetch([{ ...SCHEDULE, fire_count: 1 }]);
    renderSchedules();
    const item = (await screen.findByText('Morning digest')).closest('li') as HTMLElement;
    expect(within(item).getByText('1 run')).toBeInTheDocument();
  });

  test('an already-approved publish target shows the auto badge instead of the approve button', async () => {
    mockFetch([{ ...SCHEDULE, publish: { ...SCHEDULE.publish, approved: true } }]);
    renderSchedules();
    const item = (await screen.findByText('Morning digest')).closest('li') as HTMLElement;
    expect(within(item).getByText('auto')).toBeInTheDocument();
    expect(within(item).queryByRole('button', { name: /Approve publishing/i })).not.toBeInTheDocument();
  });

  test('shows a spinner on the approve button while the request is pending', async () => {
    let resolveApprove: (r: Response) => void = () => {};
    const approvePromise = new Promise<Response>((resolve) => { resolveApprove = resolve; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (/\/schedules\/[^/]+\/approve-publishing$/.test(url) && method === 'POST') return approvePromise;
      if (/\/schedules$/.test(url)) return new Response(JSON.stringify([SCHEDULE]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderSchedules();
    await screen.findByText('Morning digest');
    fireEvent.click(screen.getByRole('button', { name: /Approve publishing/i }));
    const approveBtn = screen.getByRole('button', { name: /Approve publishing/i });
    await waitFor(() => expect(approveBtn).toBeDisabled());
    resolveApprove(new Response(JSON.stringify({ ...SCHEDULE, released_missions: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await waitFor(() => expect(screen.queryByRole('button', { name: /Approve publishing/i })).not.toBeDisabled());
  });

  test('an overdue schedule shows "ago" rather than "in"', async () => {
    mockFetch([{ ...SCHEDULE, next_fire_at: '2000-01-01T08:00:00Z' }]);
    renderSchedules();
    const item = (await screen.findByText('Morning digest')).closest('li') as HTMLElement;
    expect(within(item).getByText(/ago$/)).toBeInTheDocument();
  });

  test('submitting a custom cron posts the raw expression', async () => {
    const spy = mockFetch([]);
    renderSchedules();
    await screen.findByText('No scheduled missions yet.');
    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'Custom cron mission' } });
    fireEvent.change(screen.getByLabelText(/Cadence/i), { target: { value: 'custom' } });
    fireEvent.change(screen.getByLabelText(/Custom cron expression/i), { target: { value: '30 7 * * 1-5' } });
    fireEvent.click(screen.getByRole('button', { name: /Schedule mission/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          if (!/\/schedules$/.test(String(u)) || (i as RequestInit)?.method !== 'POST') return false;
          return JSON.parse(String((i as RequestInit).body)).cron_expression === '30 7 * * 1-5';
        }),
      ).toBe(true),
    );
  });

  test('shows a spinner on the create button while the mutation is pending', async () => {
    let resolveCreate: (r: Response) => void = () => {};
    const createPromise = new Promise<Response>((resolve) => { resolveCreate = resolve; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (/\/schedules$/.test(url) && method === 'POST') return createPromise;
      if (/\/schedules$/.test(url)) return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderSchedules();
    await screen.findByText('No scheduled missions yet.');
    fireEvent.change(screen.getByLabelText(/Schedule mission title/i), { target: { value: 'Pending mission' } });
    const create = screen.getByRole('button', { name: /Schedule mission/i });
    fireEvent.click(create);
    await waitFor(() => expect(create).toBeDisabled());
    resolveCreate(new Response(JSON.stringify(SCHEDULE), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await waitFor(() => expect(screen.getByLabelText(/Schedule mission title/i)).toHaveValue(''));
  });
});
