import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GoalsListPage } from './GoalsListPage';

function renderGoalsListPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <GoalsListPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('GoalsListPage', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
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

  test('submits the selected agent with single-agent workflow mode', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);

      if (url.endsWith('/agents')) {
        return new Response(
          JSON.stringify([
            {
              agent_id: 'agent-1',
              name: 'Incident Fixer',
              autonomy_mode: 'supervised',
              goal_template: 'Fix incidents',
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }

      if (url.endsWith('/goals') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'planning', goal: 'Fix prod' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }

      if (url.endsWith('/goals')) {
        return new Response(JSON.stringify({ goals: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(null, { status: 404 });
    });

    renderGoalsListPage();

    await screen.findByRole('option', { name: /Incident Fixer \(supervised\)/i });
    await userEvent.selectOptions(screen.getByLabelText(/agent/i), 'agent-1');
    await userEvent.type(screen.getByLabelText(/goal text/i), 'Fix prod');
    await userEvent.click(screen.getByRole('button', { name: /submit/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/goals$/),
        expect.objectContaining({ method: 'POST' })
      )
    );

    const submitCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith('/goals') && init?.method === 'POST'
    );
    expect(JSON.parse(String(submitCall?.[1]?.body))).toEqual({
      goal: 'Fix prod',
      dry_run: false,
      agent_id: 'agent-1',
      workflow_mode: 'single_agent',
    });
  });

  test('submits auto-selected goals with auto-route workflow mode', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);

      if (url.endsWith('/agents')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.endsWith('/goals') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ id: 'goal-2', goal_id: 'goal-2', status: 'planning', goal: 'Preview deploy' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }

      if (url.endsWith('/goals')) {
        return new Response(JSON.stringify({ goals: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(null, { status: 404 });
    });

    renderGoalsListPage();

    expect(await screen.findByRole('option', { name: /auto-select best agent/i })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/goal text/i), 'Preview deploy');
    await userEvent.click(screen.getByLabelText(/dry run/i));
    await userEvent.click(screen.getByRole('button', { name: /dry run/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/goals$/),
        expect.objectContaining({ method: 'POST' })
      )
    );

    const submitCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith('/goals') && init?.method === 'POST'
    );
    expect(JSON.parse(String(submitCall?.[1]?.body))).toEqual({
      goal: 'Preview deploy',
      dry_run: true,
      workflow_mode: 'auto_route',
    });
  });
});
