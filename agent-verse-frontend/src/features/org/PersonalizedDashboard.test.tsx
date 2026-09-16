import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { Organization } from './types';
import { PersonalizedDashboard } from './PersonalizedDashboard';

function org(overrides: Partial<Organization> = {}): Organization {
  return { id: 'o1', name: 'Acme', description: '', monthly_budget_usd: 5000, ...overrides } as Organization;
}

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/health'))
      return new Response(JSON.stringify({ active_missions: 0, active_teams: 0, pending_approvals: 0, items_needing_attention: 0, budget_used_usd: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/missions'))
      return new Response(JSON.stringify({ data: [], cursor: null, hasMore: false }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderDash(o: Organization, userRole?: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><PersonalizedDashboard org={o} userRole={userRole} /></QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('PersonalizedDashboard', () => {
  test('renders the default dashboard with the org name and description when no role is given', () => {
    mockFetch();
    renderDash(org({ description: 'We automate finance' }));
    expect(screen.getByRole('heading', { name: 'Acme' })).toBeInTheDocument();
    expect(screen.getByText('We automate finance')).toBeInTheDocument();
  });

  test('falls back to the generic blurb when the org has no description', () => {
    mockFetch();
    renderDash(org({ description: '' }));
    expect(screen.getByText(/AI Organization OS/i)).toBeInTheDocument();
  });

  test('routes a cfo role to the CFO dashboard', async () => {
    mockFetch();
    renderDash(org(), 'cfo');
    expect(await screen.findByRole('heading', { name: /Finance Overview/i })).toBeInTheDocument();
  });

  test('routes a devops role to the DevOps dashboard', async () => {
    mockFetch();
    renderDash(org(), 'devops');
    expect(await screen.findByRole('heading', { name: /DevOps Overview/i })).toBeInTheDocument();
  });

  test('an unrecognised role falls back to the default dashboard', () => {
    mockFetch();
    renderDash(org({ description: 'Fallback copy here' }), 'wizard-of-oz');
    expect(screen.getByRole('heading', { name: 'Acme' })).toBeInTheDocument();
    expect(screen.getByText('Fallback copy here')).toBeInTheDocument();
  });
});
