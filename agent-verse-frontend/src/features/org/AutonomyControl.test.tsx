import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AutonomyControl } from './AutonomyControl';
import type { Organization } from './types';

const ORG = {
  id: 'o1',
  tenant_id: 't',
  name: 'Acme',
  slug: 'acme',
  description: '',
  industry: 'Tech',
  jurisdiction: 'US',
  mission: '',
  vision: '',
  status: 'active',
  autonomy_level: 3,
  risk_tolerance: 'balanced',
  monthly_budget_usd: 1000,
  created_by: null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
} as unknown as Organization;

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (/\/v1\/org\/o1$/.test(url) && method === 'PATCH')
      return new Response(JSON.stringify({ ...ORG, autonomy_level: 5 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderControl() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AutonomyControl org={ORG} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('AutonomyControl', () => {
  test('renders the current level (L3) description by default', () => {
    mockFetch();
    renderControl();
    expect(screen.getByRole('heading', { name: /Autonomy Level/i })).toBeInTheDocument();
    expect(screen.getByText('L3 — Standard')).toBeInTheDocument();
    expect(screen.getByText('Runs autonomously with configurable approval gates.')).toBeInTheDocument();
  });

  test('selecting a different level reveals a save button for that level', async () => {
    mockFetch();
    renderControl();
    // The save CTA lives outside the animated description panel and names the
    // pending level, so it is the reliable signal that the selection took.
    await userEvent.click(screen.getByRole('radio', { name: /L5/ }));
    expect(
      await screen.findByRole('button', { name: /Set to L5 — Mission Autonomous/i }),
    ).toBeInTheDocument();
  });

  test('saving a new level PATCHes the organization with the chosen level', async () => {
    const spy = mockFetch();
    renderControl();
    await userEvent.click(screen.getByRole('radio', { name: /L5/ }));
    await userEvent.click(await screen.findByRole('button', { name: /Set to L5/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          const init = i as RequestInit | undefined;
          if (!/\/v1\/org\/o1$/.test(String(u)) || init?.method !== 'PATCH') return false;
          return JSON.parse(String(init?.body ?? '{}')).autonomy_level === 5;
        }),
      ).toBe(true),
    );
  });

  test('re-selecting the current level does not show a save button', async () => {
    mockFetch();
    renderControl();
    await userEvent.click(screen.getByRole('radio', { name: /L3/ }));
    expect(screen.queryByRole('button', { name: /Set to/i })).not.toBeInTheDocument();
  });
});
