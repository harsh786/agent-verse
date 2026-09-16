import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ScheduledMissions } from './ScheduledMissions';

const SCHEDULES = [
  { id: 's1', name: 'Weekly report', cron_expr: '0 9 * * 1', goal_template: 'Generate the weekly report', active: true },
  { id: 's2', name: 'Nightly backup', cron_expr: '0 0 * * *', goal_template: 'Back up the database', active: false },
];

function mockSchedules(list: unknown[] = SCHEDULES) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/schedules') && method === 'GET')
      return new Response(JSON.stringify(list), { status: 200, headers: { 'Content-Type': 'application/json' } });
    // pause/resume/fire POST and create POST and delete
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ScheduledMissions /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ScheduledMissions', () => {
  test('renders schedule cards with the name, goal template and cron', async () => {
    mockSchedules();
    renderPage();
    expect(await screen.findByText('Weekly report')).toBeInTheDocument();
    expect(screen.getByText('Generate the weekly report')).toBeInTheDocument();
    expect(screen.getByText('0 9 * * 1')).toBeInTheDocument();
    // Header summary: 1 active · 2 total
    expect(screen.getByText(/1 active · 2 total/)).toBeInTheDocument();
  });

  test('shows the empty state when there are no schedules', async () => {
    mockSchedules([]);
    renderPage();
    expect(await screen.findByText('No scheduled missions yet')).toBeInTheDocument();
  });

  test('pausing an active schedule POSTs to the pause endpoint', async () => {
    const spy = mockSchedules();
    renderPage();
    await screen.findByText('Weekly report');
    await userEvent.click(screen.getByRole('button', { name: 'Pause Weekly report' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/schedules/s1/pause') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
  });

  test('creating a schedule via the modal POSTs to /schedules with the entered fields', async () => {
    const spy = mockSchedules([]);
    renderPage();
    await screen.findByText('No scheduled missions yet');
    await userEvent.click(screen.getByRole('button', { name: /Create new scheduled mission/i }));
    await userEvent.type(screen.getByLabelText(/Describe when this should run/i), 'Every Monday at 9am');
    await userEvent.type(screen.getByLabelText(/What should the agent do/i), 'Send the Monday digest');
    await userEvent.click(screen.getByRole('button', { name: /Save Schedule/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).endsWith('/schedules') || (i as RequestInit)?.method !== 'POST') return false;
        const body = JSON.parse((i as RequestInit).body as string);
        return body.name === 'Every Monday at 9am' && body.goal_template === 'Send the Monday digest';
      })).toBe(true),
    );
  });

  test('deleting a schedule issues a DELETE for that id', async () => {
    const spy = mockSchedules();
    renderPage();
    await screen.findByText('Weekly report');
    await userEvent.click(screen.getByRole('button', { name: 'Delete Weekly report schedule' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/schedules/s1') && (i as RequestInit)?.method === 'DELETE')).toBe(true),
    );
  });
});
