import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TriggerCard } from './TriggerCard';
import type { Trigger } from '../types';

const TRIGGER: Trigger = {
  schedule_id: 'sched-abcdef123456',
  goal_id: '',
  goal_template: 'Send the weekly report',
  spec: { trigger_type: 'cron', cron_expression: '0 9 * * 1', description: 'Weekly report' },
  paused: false,
  fire_count: 3,
};

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    // The detail drawer's history panel expects an array of events.
    const body = url.includes('/events') ? '[]' : '{}';
    return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderCard(trigger: Trigger = TRIGGER) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TriggerCard trigger={trigger} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TriggerCard', () => {
  test('renders the description, schedule summary, goal template and active status', () => {
    mockFetch();
    renderCard();
    expect(screen.getByText('Weekly report')).toBeInTheDocument();
    expect(screen.getByText('Send the weekly report')).toBeInTheDocument();
    expect(screen.getByText('0 9 * * 1')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    // Short schedule-id chip.
    expect(screen.getByText('#sched-ab')).toBeInTheDocument();
  });

  test('a paused trigger shows the paused badge and a Resume action', () => {
    mockFetch();
    renderCard({ ...TRIGGER, paused: true });
    expect(screen.getByText('paused')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /resume trigger/i })).toBeInTheDocument();
  });

  test('Fire now posts to the fire endpoint', async () => {
    const spy = mockFetch();
    renderCard();
    await userEvent.click(screen.getByRole('button', { name: /fire trigger now/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/sched-abcdef123456/fire') &&
            (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('Pause posts to the pause endpoint for an active trigger', async () => {
    const spy = mockFetch();
    renderCard();
    await userEvent.click(screen.getByRole('button', { name: /pause trigger/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/sched-abcdef123456/pause') &&
            (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('delete requires a confirming second click before issuing DELETE', async () => {
    const spy = mockFetch();
    renderCard();
    const delBtn = screen.getByRole('button', { name: /delete trigger/i });

    // First click only arms the confirm state — no request yet.
    await userEvent.click(delBtn);
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'DELETE')).toBe(false);
    expect(delBtn).toHaveAttribute('title', expect.stringMatching(/click again to confirm/i));

    // Second click actually deletes.
    await userEvent.click(delBtn);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/sched-abcdef123456') &&
            (i as RequestInit)?.method === 'DELETE',
        ),
      ).toBe(true),
    );
  });

  test('clicking the card body opens the detail drawer', async () => {
    mockFetch();
    renderCard();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await userEvent.click(screen.getByText('Weekly report'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    // Drawer surfaces the Fire Now footer action.
    expect(screen.getByRole('button', { name: /fire now/i })).toBeInTheDocument();
  });
});
