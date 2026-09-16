import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { TriggerEvent } from '../types';
import { TriggerHistoryPanel } from './TriggerHistoryPanel';

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const EVENT = (over: Partial<TriggerEvent> = {}): TriggerEvent => ({
  event_id: 'ev-1',
  trigger_id: 'sch-1',
  trigger_type: 'cron',
  tenant_id: 't',
  idempotency_key: 'idem-1',
  payload: {},
  goal_id_created: 'abcd1234efgh5678',
  fired_at: '2026-03-01T12:00:00Z',
  simulated: false,
  ...over,
});

function mockEvents(events: TriggerEvent[] | { status: number }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/events')) {
      if (Array.isArray(events)) return json(events);
      return json({ error: { message: 'boom' } }, events.status);
    }
    return json({});
  });
}

function renderPanel(scheduleId = 'sch-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TriggerHistoryPanel scheduleId={scheduleId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('TriggerHistoryPanel', () => {
  test('shows skeleton placeholders while loading', () => {
    mockEvents([EVENT()]);
    const { container } = renderPanel();
    // Initial synchronous render is the isLoading branch (3 pulse rows, no text yet).
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(3);
    expect(screen.queryByText(/events \(most recent first\)/i)).not.toBeInTheDocument();
  });

  test('renders event rows with the count header and truncated goal id', async () => {
    mockEvents([EVENT(), EVENT({ event_id: 'ev-2', goal_id_created: undefined })]);
    renderPanel();
    expect(await screen.findByText('2 events (most recent first)')).toBeInTheDocument();
    // goal_id_created.slice(0, 8) + ellipsis
    expect(screen.getByText(/abcd1234…/)).toBeInTheDocument();
    expect(screen.getByLabelText('Goal created')).toBeInTheDocument();
  });

  test('renders the empty state when the trigger has never fired', async () => {
    mockEvents([]);
    renderPanel();
    expect(await screen.findByText('This trigger has never fired.')).toBeInTheDocument();
  });

  test('a simulated event shows the Simulated badge', async () => {
    mockEvents([EVENT({ simulated: true, goal_id_created: undefined })]);
    renderPanel();
    expect(await screen.findByText('Simulated')).toBeInTheDocument();
    expect(screen.getByLabelText('Simulated')).toBeInTheDocument();
  });

  test('the Refresh button refetches the event history', async () => {
    const spy = mockEvents([EVENT()]);
    renderPanel();
    await screen.findByText('1 events (most recent first)');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/events')).length;
    await userEvent.click(screen.getByRole('button', { name: 'Refresh event history' }));
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/events')).length).toBeGreaterThan(before),
    );
  });

  test('a 500 falls back to the empty (never fired) state', async () => {
    mockEvents({ status: 500 });
    renderPanel();
    expect(await screen.findByText('This trigger has never fired.')).toBeInTheDocument();
  });
});
