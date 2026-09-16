import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { Organization } from '../types';
import { DevOpsDashboard } from './DevOpsDashboard';

function org(): Organization {
  return { id: 'o1', name: 'Acme' } as Organization;
}

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/health'))
      return new Response(JSON.stringify({ active_missions: 0, active_teams: 0, pending_approvals: 0, items_needing_attention: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderDash() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><DevOpsDashboard org={org()} /></QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('DevOpsDashboard', () => {
  test('renders the DevOps header with the org name', () => {
    mockFetch();
    renderDash();
    expect(screen.getByRole('heading', { name: /DevOps Overview/i })).toBeInTheDocument();
    expect(screen.getByText(/Acme · DevOps \/ SRE view/)).toBeInTheDocument();
  });

  test('shows the reliability KPIs', () => {
    mockFetch();
    renderDash();
    expect(screen.getByText('Uptime (30d)')).toBeInTheDocument();
    // 99.9% appears for both Uptime and Reliability-style tiles.
    expect(screen.getAllByText('99.9%').length).toBeGreaterThan(0);
    expect(screen.getByText('Infra Health')).toBeInTheDocument();
  });

  test('renders the service status list with operational services', () => {
    mockFetch();
    renderDash();
    expect(screen.getByText('Service Status')).toBeInTheDocument();
    for (const svc of ['API Gateway', 'Agent Runtime', 'Knowledge Store', 'Redis Cache', 'Celery Workers']) {
      expect(screen.getByText(svc)).toBeInTheDocument();
    }
    expect(screen.getAllByText('Operational').length).toBe(5);
  });
});
