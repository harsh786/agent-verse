import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TriggerList } from './TriggerList';
import type { Trigger } from '../types';

const CRON: Trigger = {
  schedule_id: 'sched-cron-1',
  goal_id: '',
  goal_template: 'Send the weekly report',
  spec: { trigger_type: 'cron', cron_expression: '0 9 * * 1', description: 'Weekly digest' },
  paused: false,
  fire_count: 2,
};
const WEBHOOK: Trigger = {
  schedule_id: 'sched-hook-1',
  goal_id: '',
  goal_template: 'Deploy on push',
  spec: { trigger_type: 'github_webhook', description: 'GitHub push' },
  paused: true,
  fire_count: 0,
};

// TriggerCard mounts mutation hooks (no fetch until interacted with) but still
// needs a QueryClient in context.
function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
}

function renderList(triggers: Trigger[], isLoading = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TriggerList triggers={triggers} isLoading={isLoading} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TriggerList', () => {
  test('shows the empty state when there are no triggers', () => {
    mockFetch();
    renderList([]);
    expect(screen.getByText('No triggers yet')).toBeInTheDocument();
    expect(screen.getByText(/Create a trigger to automate goal execution/i)).toBeInTheDocument();
  });

  test('renders the loading skeleton (no search UI) while loading', () => {
    mockFetch();
    renderList([CRON], true);
    // The search/filter chrome and cards only appear once loaded.
    expect(screen.queryByPlaceholderText(/Search triggers/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Weekly digest')).not.toBeInTheDocument();
  });

  test('groups triggers by family with a per-family count and renders the card', () => {
    mockFetch();
    renderList([CRON, WEBHOOK]);
    // Family section header buttons (distinct from the filter <select> options).
    expect(screen.getByRole('button', { name: /Time & Schedule/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Webhooks/ })).toBeInTheDocument();
    // With two families neither auto-expands, so card bodies stay collapsed.
    expect(screen.queryByText('Weekly digest')).not.toBeInTheDocument();
  });

  test('a single-family list auto-expands and shows the card details', () => {
    mockFetch();
    renderList([CRON]);
    expect(screen.getByText('Weekly digest')).toBeInTheDocument();
    expect(screen.getByText('Send the weekly report')).toBeInTheDocument();
  });

  test('searching filters out non-matching triggers', async () => {
    mockFetch();
    renderList([CRON, WEBHOOK]);
    await userEvent.type(screen.getByPlaceholderText(/Search triggers/i), 'nonexistent-goal');
    expect(screen.getByText(/No results match your filter/i)).toBeInTheDocument();
    // The family section header disappears (the <select> option of the same name stays).
    expect(screen.queryByRole('button', { name: /Time & Schedule/ })).not.toBeInTheDocument();
  });

  test('the family filter narrows the visible families', async () => {
    mockFetch();
    renderList([CRON, WEBHOOK]);
    await userEvent.selectOptions(screen.getByRole('combobox'), 'webhook');
    expect(screen.getByRole('button', { name: /Webhooks/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Time & Schedule/ })).not.toBeInTheDocument();
  });
});
