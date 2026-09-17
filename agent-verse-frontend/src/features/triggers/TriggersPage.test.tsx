import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TriggersPage } from './TriggersPage';
import type { Trigger } from './types';

const TRIGGERS: Trigger[] = [
  {
    schedule_id: 'sched-1',
    goal_id: '',
    goal_template: 'Send the weekly report',
    spec: { trigger_type: 'cron', cron_expression: '0 9 * * 1', description: 'Weekly digest' },
    paused: false,
    fire_count: 5,
  },
  {
    schedule_id: 'sched-2',
    goal_id: '',
    goal_template: 'Deploy on push',
    spec: { trigger_type: 'github_webhook', description: 'GitHub push' },
    paused: true,
    fire_count: 1,
  },
];

function mockFetch(triggers: Trigger[] | { fail?: boolean } = TRIGGERS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/triggers/dlq'))
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/triggers')) {
      if (!Array.isArray(triggers) && triggers.fail)
        return new Response(JSON.stringify({ detail: 'boom' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify(triggers), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    // /agents and /goals for the create modal.
    if (url.includes('/agents'))
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response(JSON.stringify({ goals: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><TriggersPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TriggersPage', () => {
  test('renders the header and stat cards computed from the fetched triggers', async () => {
    mockFetch(TRIGGERS);
    renderPage();
    expect(screen.getByRole('heading', { name: 'Triggers' })).toBeInTheDocument();
    // Wait for the data to land (a family section appears), then assert the derived
    // counts: Total = 2, Paused = 1.
    await screen.findByRole('button', { name: /Time & Schedule/ });
    const totalCard = screen.getByText('Total').closest('div')!.parentElement!;
    expect(totalCard).toHaveTextContent('2');
    const pausedCard = screen.getByText('Paused').closest('div')!.parentElement!;
    expect(pausedCard).toHaveTextContent('1');
  });

  test('lists the triggers grouped by family once loaded', async () => {
    mockFetch(TRIGGERS);
    renderPage();
    // Two distinct families (cron → Time, github_webhook → Webhooks) → both header buttons show.
    expect(await screen.findByRole('button', { name: /Time & Schedule/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Webhooks/ })).toBeInTheDocument();
  });

  test('shows the error banner when the trigger request fails', async () => {
    mockFetch({ fail: true });
    renderPage();
    expect(await screen.findByText(/Failed to load triggers/i)).toBeInTheDocument();
  });

  test('renders the empty state when there are no triggers', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByText('No triggers yet')).toBeInTheDocument();
  });

  test('switching to the DLQ tab renders the dead letter queue panel', async () => {
    mockFetch([]);
    renderPage();
    await screen.findByText('No triggers yet');
    await userEvent.click(screen.getByRole('button', { name: /Dead Letter Queue/i }));
    expect(await screen.findByText(/Dead Letter Queue is empty/i)).toBeInTheDocument();
  });

  test('New Trigger opens the create-trigger modal', async () => {
    mockFetch(TRIGGERS);
    renderPage();
    await screen.findByRole('button', { name: /Time & Schedule/ });
    await userEvent.click(screen.getByRole('button', { name: /New Trigger/i }));
    expect(await screen.findByRole('dialog', { name: /Create trigger/i })).toBeInTheDocument();
    expect(screen.getByText(/Choose a trigger family/i)).toBeInTheDocument();
  });
});
