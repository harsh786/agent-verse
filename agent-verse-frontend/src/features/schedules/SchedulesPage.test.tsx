import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { SchedulesPage } from './SchedulesPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SchedulesPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/agents'))
      return new Response(JSON.stringify([{ agent_id: 'agent-abc', name: 'Daily Reporter' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/schedules/analytics'))
      return new Response(JSON.stringify({ total: 2, active: 1, paused: 1, by_trigger_type: { cron: 2 }, fired_last_7_days: {}, schedules_summary: [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/schedules') && (init?.method ?? 'GET') === 'POST')
      return new Response(JSON.stringify({ schedule_id: 'sched-1' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/schedules'))
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/schedules/suggest'))
      return new Response(JSON.stringify({ suggestions: [], llm_powered: false }), { status: 200 });
    return new Response('{}', { status: 200 });
  });
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'tenant-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('SchedulesPage', () => {
  test('renders heading and all 4 tabs', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /schedules/i })).toBeInTheDocument();
    expect(screen.getByTestId('tab-schedules')).toBeInTheDocument();
    expect(screen.getByTestId('tab-analytics')).toBeInTheDocument();
    expect(screen.getByTestId('tab-advisor')).toBeInTheDocument();
    expect(screen.getByTestId('tab-nl')).toBeInTheDocument();
  });

  test('shows quick-start template buttons', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    expect(screen.getByRole('button', { name: /daily report/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /hourly check/i })).toBeInTheDocument();
  });

  test('opens create form when New Schedule clicked', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    await userEvent.click(screen.getByTestId('create-schedule-btn') ?? screen.getByRole('button', { name: /new schedule/i }));
    expect(await screen.findByTestId('create-schedule-form')).toBeInTheDocument();
  });

  test('creates schedule with correct POST body', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    await userEvent.click(screen.getByRole('button', { name: /new schedule/i }));
    await screen.findByTestId('create-schedule-form');
    // Fill goal template
    const goalInput = screen.getByPlaceholderText(/describe what to run/i);
    await userEvent.type(goalInput, 'Run daily report');
    await userEvent.click(screen.getByRole('button', { name: /create schedule/i }));
    await waitFor(() => {
      const postCall = spy.mock.calls.find(([u, i]) => String(u).includes('/schedules') && (i as RequestInit)?.method === 'POST' && !String(u).includes('suggest'));
      expect(postCall).toBeTruthy();
      const body = JSON.parse(String((postCall?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.goal_template).toBe('Run daily report');
      expect(body.trigger_type).toBe('cron');
    });
  });

  test('AI Advisor tab shows suggest button', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    await userEvent.click(screen.getByTestId('tab-advisor'));
    expect(await screen.findByTestId('suggest-btn')).toBeInTheDocument();
  });

  test('Analytics tab shows stats after clicking', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    await userEvent.click(screen.getByTestId('tab-analytics'));
    expect(await screen.findByText('Total')).toBeInTheDocument();
  });

  test('NL Scheduler tab shows chat input', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    await userEvent.click(screen.getByTestId('tab-nl'));
    expect(await screen.findByPlaceholderText(/plain english|schedule description/i)).toBeInTheDocument();
  });
});
