import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { SchedulesPage } from './SchedulesPage';

function renderSchedulesPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SchedulesPage />
    </QueryClientProvider>
  );
}

describe('SchedulesPage', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('creates schedules for the selected agent', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);

      if (url.endsWith('/agents')) {
        return new Response(
          JSON.stringify([
            {
              agent_id: 'agent-abc',
              name: 'Daily Reporter',
              autonomy_mode: 'autonomous',
              goal_template: 'Run reports',
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }

      if (url.endsWith('/schedules') && init?.method === 'POST') {
        return new Response(JSON.stringify({ schedule_id: 'sched-1' }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.endsWith('/schedules')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(null, { status: 404 });
    });

    renderSchedulesPage();

    await userEvent.click(screen.getByRole('button', { name: /new schedule/i }));
    await screen.findByRole('option', { name: /Daily Reporter/i });
    await userEvent.selectOptions(screen.getByLabelText(/agent/i), 'agent-abc');
    await userEvent.type(screen.getByLabelText(/goal template/i), 'Run daily report');
    await userEvent.click(screen.getByRole('button', { name: /create schedule/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/schedules$/),
        expect.objectContaining({ method: 'POST' })
      )
    );

    const createCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith('/schedules') && init?.method === 'POST'
    );
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      agent_id: 'agent-abc',
      goal_template: 'Run daily report',
      trigger_type: 'cron',
      cron_expr: '0 * * * *',
    });
  });
});
