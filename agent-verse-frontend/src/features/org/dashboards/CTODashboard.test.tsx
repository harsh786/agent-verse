import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { Organization } from '../types';
import { CTODashboard } from './CTODashboard';

const HEALTH = { active_missions: 6, active_teams: 3, pending_approvals: 0, items_needing_attention: 0 };
const MISSIONS = {
  data: [
    { id: 'm1', title: 'Engineering platform migration', status: 'active' },
    { id: 'm2', title: 'Marketing brand refresh', status: 'active' },
  ],
  cursor: null,
  hasMore: false,
};

function org(): Organization {
  return { id: 'o1', name: 'Acme' } as Organization;
}

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/health'))
      return new Response(JSON.stringify(HEALTH), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/missions'))
      return new Response(JSON.stringify(MISSIONS), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderDash() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><CTODashboard org={org()} /></QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CTODashboard', () => {
  test('renders the technology header with the org name', () => {
    mockFetch();
    renderDash();
    expect(screen.getByRole('heading', { name: /Technology Overview/i })).toBeInTheDocument();
    expect(screen.getByText(/Acme · CTO view/)).toBeInTheDocument();
  });

  test('surfaces the active engineering mission count from health', async () => {
    mockFetch();
    renderDash();
    expect(screen.getByText('Eng Missions')).toBeInTheDocument();
    expect(await screen.findByText('6')).toBeInTheDocument();
  });

  test('lists engineering-related missions and filters out unrelated ones', async () => {
    mockFetch();
    renderDash();
    expect(await screen.findByText('Engineering platform migration')).toBeInTheDocument();
    expect(screen.getByText('Engineering Missions')).toBeInTheDocument();
    // The marketing mission does not match the engineering/ai_ml/devops keywords.
    expect(screen.queryByText('Marketing brand refresh')).not.toBeInTheDocument();
  });
});
