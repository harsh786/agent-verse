import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AIOpsDashboard } from './AIOpsDashboard';

const GOALS = {
  goals: [
    { id: 'g1', goal: 'Deploy the billing service', status: 'executing', agent_id: 'a1' },
    { id: 'g2', goal: 'Plan the data migration', status: 'planning', agent_id: null },
    { id: 'g3', goal: 'Old finished goal', status: 'complete' },
    { id: 'g4', goal: 'Broken goal', status: 'failed' },
  ],
};
const MODELS = {
  providers: [
    { provider: 'anthropic', is_healthy: true, avg_latency_ms: 120 },
    { provider: 'openai', is_healthy: false, avg_latency_ms: 0 },
  ],
};
const ALERTS = { alerts: [{ alert_id: 'al1', message: 'Latency drift detected', severity: 'critical', drift_type: 'data' }] };
const REGRESSION = { status: 'warning', critical_alerts: 1, warning_alerts: 3 };

function mockFetch(overrides: {
  goals?: unknown;
  models?: unknown;
  alerts?: unknown;
  regression?: unknown;
} = {}) {
  const goals = overrides.goals ?? GOALS;
  const models = overrides.models ?? MODELS;
  const alerts = overrides.alerts ?? ALERTS;
  const regression = overrides.regression ?? REGRESSION;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const json = (b: unknown) =>
      new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/models/health')) return json(models);
    if (url.includes('/ai-ops/alerts')) return json(alerts);
    if (url.includes('/ai-ops/regression-status')) return json(regression);
    if (url.includes('/goals')) return json(goals);
    return json({});
  });
}

function renderDashboard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AIOpsDashboard /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('AIOpsDashboard', () => {
  test('renders the header and KPI labels', async () => {
    mockFetch();
    renderDashboard();
    expect(screen.getByRole('heading', { name: /AI Operations Center/i })).toBeInTheDocument();
    // "Completed Today" / "Failed Today" / "Healthy Providers" are unique KPI labels
    // ("Active Goals" also appears as the panel heading, so it is not unique).
    expect(await screen.findByText('Completed Today')).toBeInTheDocument();
    expect(screen.getByText('Failed Today')).toBeInTheDocument();
    expect(screen.getByText('Healthy Providers')).toBeInTheDocument();
  });

  test('derives the healthy-provider ratio from the models health feed', async () => {
    mockFetch();
    renderDashboard();
    // 1 of 2 providers is healthy.
    expect(await screen.findByText('1/2')).toBeInTheDocument();
  });

  test('lists the active (executing/planning) goals with their titles', async () => {
    mockFetch();
    renderDashboard();
    expect(await screen.findByText('Deploy the billing service')).toBeInTheDocument();
    expect(screen.getByText('Plan the data migration')).toBeInTheDocument();
    // Completed/failed goals are not in the active list.
    expect(screen.queryByText('Old finished goal')).not.toBeInTheDocument();
  });

  test('renders provider health rows with latency', async () => {
    mockFetch();
    renderDashboard();
    expect(await screen.findByText('anthropic')).toBeInTheDocument();
    expect(screen.getByText('120ms')).toBeInTheDocument();
    // Untested provider (0 latency) shows the fallback label.
    expect(screen.getByText('Not tested')).toBeInTheDocument();
  });

  test('surfaces a regression warning and recent alerts', async () => {
    mockFetch();
    renderDashboard();
    expect(await screen.findByText(/warning detected/i)).toBeInTheDocument();
    expect(screen.getByText('1 critical · 3 warnings')).toBeInTheDocument();
    expect(screen.getByText('Latency drift detected')).toBeInTheDocument();
  });

  test('shows empty states when there are no goals or providers', async () => {
    mockFetch({ goals: { goals: [] }, models: { providers: [] }, alerts: { alerts: [] }, regression: { status: 'ok' } });
    renderDashboard();
    expect(await screen.findByText('No active goals')).toBeInTheDocument();
    expect(screen.getByText('No providers tested yet')).toBeInTheDocument();
    expect(screen.getByText('0/0')).toBeInTheDocument();
  });
});
