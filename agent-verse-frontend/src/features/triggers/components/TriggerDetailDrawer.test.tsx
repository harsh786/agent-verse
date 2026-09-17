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

  test('Cancel exits the edit form without saving', async () => {
    mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /edit trigger/i }));
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.queryByRole('button', { name: /save changes/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /edit trigger/i })).toBeInTheDocument();
  });

  test('shows the update error message when the save mutation fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if ((init as RequestInit)?.method === 'PATCH') {
        return new Response(JSON.stringify({ error: { message: 'boom' } }), { status: 500 });
      }
      const body = url.includes('/events') ? '[]' : '{}';
      return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /edit trigger/i }));
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() =>
      expect(screen.getByText(/update failed|boom/i)).toBeInTheDocument(),
    );
  });

  test('Pause button pauses an active trigger', async () => {
    const spy = mockFetch();
    renderDrawer();
    const pauseButton = screen.getByRole('button', { name: /^pause$/i });
    await userEvent.click(pauseButton);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/sched-777/pause') &&
            (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('Resume button resumes a paused trigger, and Fire Now is disabled while paused', async () => {
    const spy = mockFetch();
    renderDrawer(vi.fn(), { ...TRIGGER, paused: true });
    const fireButton = screen.getByRole('button', { name: /fire now/i });
    expect(fireButton).toBeDisabled();
    const resumeButton = screen.getByRole('button', { name: /^resume$/i });
    await userEvent.click(resumeButton);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/sched-777/resume') &&
            (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
    // Paused status renders "Paused" in the badge and the timing section.
    expect(screen.getAllByText('Paused').length).toBeGreaterThanOrEqual(1);
  });

  test('Fire Now posts to the fire endpoint when active', async () => {
    const spy = mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /fire now/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/sched-777/fire') &&
            (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('renders simulate result JSON after a successful simulation', async () => {
    mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getByText('{}')).toBeInTheDocument());
  });

  test('timing section shows next fire and last fired dates when present', () => {
    mockFetch();
    const trigger: Trigger = {
      ...TRIGGER,
      next_fire_at: '2026-01-01T00:00:00.000Z',
      last_fired_at: '2025-12-31T00:00:00.000Z',
    };
    renderDrawer(vi.fn(), trigger);
    expect(screen.getByText('Next fire')).toBeInTheDocument();
    expect(screen.getByText('Last fired')).toBeInTheDocument();
  });

  test('goal template falls back to the goal id when no template is set', () => {
    mockFetch();
    const trigger: Trigger = { ...TRIGGER, goal_template: '', goal_id: 'goal-42' };
    renderDrawer(vi.fn(), trigger);
    expect(screen.getByText('goal: goal-42')).toBeInTheDocument();
  });

  test('goal template shows an em dash when no template or goal id exists', () => {
    mockFetch();
    const trigger: Trigger = { ...TRIGGER, goal_template: '', goal_id: '' };
    renderDrawer(vi.fn(), trigger);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  test('shows "No extra configuration" when the spec has no extra fields', () => {
    mockFetch();
    const trigger: Trigger = {
      ...TRIGGER,
      spec: { trigger_type: 'condition', description: 'Bare trigger' },
    };
    renderDrawer(vi.fn(), trigger);
    expect(screen.getByText('No extra configuration.')).toBeInTheDocument();
  });

  test('uses the trigger type as the dialog label when no description is set', () => {
    mockFetch();
    const trigger: Trigger = {
      ...TRIGGER,
      spec: { trigger_type: 'condition' },
    };
    renderDrawer(vi.fn(), trigger);
    expect(
      screen.getByRole('dialog', { name: 'Trigger detail: condition' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Trigger Detail' })).toBeInTheDocument();
  });

  test('editing seeds the draft goal as empty when goal_template is unset', async () => {
    mockFetch();
    const trigger: Trigger = { ...TRIGGER, goal_template: '' };
    renderDrawer(vi.fn(), trigger);
    await userEvent.click(screen.getByRole('button', { name: /edit trigger/i }));
    const textarea = screen.getByPlaceholderText(/Describe the goal to run on each fire/i);
    expect(textarea).toHaveValue('');
  });
});
