import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TriggerDetailDrawer } from './TriggerDetailDrawer';
import type { Trigger } from '../types';

const TRIGGER: Trigger = {
  schedule_id: 'sched-777',
  goal_id: '',
  goal_template: 'Escalate large orders',
  spec: {
    trigger_type: 'condition',
    description: 'Large order guard',
    condition_expression: 'payload.amount > 1000',
  },
  paused: false,
  fire_count: 5,
};

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const body = url.includes('/events') ? '[]' : '{}';
    return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderDrawer(onClose = vi.fn(), trigger: Trigger = TRIGGER) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TriggerDetailDrawer trigger={trigger} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TriggerDetailDrawer', () => {
  test('renders the type, family label, goal template and spec fields', () => {
    mockFetch();
    renderDrawer();
    expect(screen.getByRole('heading', { name: 'Large order guard' })).toBeInTheDocument();
    // trigger_type badge + resolved family label (state_condition → "State & Condition").
    expect(screen.getByText('condition')).toBeInTheDocument();
    expect(screen.getByText('State & Condition')).toBeInTheDocument();
    expect(screen.getByText('Escalate large orders')).toBeInTheDocument();
    // Spec fields render the non-excluded keys/values.
    expect(screen.getByText('condition_expression')).toBeInTheDocument();
    expect(screen.getByText('"payload.amount > 1000"')).toBeInTheDocument();
  });

  test('timing section shows total fires and active status', () => {
    mockFetch();
    renderDrawer();
    expect(screen.getByText('Total fires')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    // "Active" appears both in the status badge and the timing dd.
    expect(screen.getAllByText('Active').length).toBeGreaterThanOrEqual(1);
  });

  test('the close button invokes onClose', async () => {
    mockFetch();
    const onClose = renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /close trigger detail/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('Edit switches to the edit form with a goal-template textarea and Save action', async () => {
    mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /edit trigger/i }));
    expect(
      screen.getByPlaceholderText(/Describe the goal to run on each fire/i),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument();
  });

  test('Run Simulation posts to the simulate endpoint', async () => {
    const spy = mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/sched-777/simulate') &&
            (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('saving an edit issues a PATCH to the trigger', async () => {
    const spy = mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /edit trigger/i }));
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/sched-777') &&
            (i as RequestInit)?.method === 'PATCH',
        ),
      ).toBe(true),
    );
  });
});
