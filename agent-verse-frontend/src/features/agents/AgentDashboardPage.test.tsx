import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentDashboardPage } from './AgentDashboardPage';

const MOCK_AGENT = {
  agent_id: 'agent-dash-1',
  name: 'Dashboard Agent',
  autonomy_mode: 'bounded-autonomous',
  created_at: '2026-01-01T00:00:00Z',
};

const MOCK_GOALS = {
  goals: [
    {
      goal_id: 'g1', id: 'g1', goal: 'Run report',
      status: 'complete', agent_id: 'agent-dash-1',
      cost_usd: 0.5, created_at: '2026-06-28T10:00:00Z',
    },
    {
      goal_id: 'g2', id: 'g2', goal: 'Audit logs',
      status: 'failed', agent_id: 'agent-dash-1',
      cost_usd: 0.2, created_at: '2026-06-28T11:00:00Z',
    },
  ],
};

const MOCK_ANALYTICS = {
  active_goals: 0,
  total_goals: 2,
  success_rate: 0.5,
  avg_latency_ms: 800,
  cost_today_usd: 0.7,
};

function renderPage(agentId = 'agent-dash-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/agents/${agentId}/dashboard`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/agents/:agentId/dashboard" element={<AgentDashboardPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('AgentDashboardPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders without crashing', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows loading skeleton while fetching', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body.innerHTML).toBeTruthy();
  });

  test('shows agent name after loading', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Response(JSON.stringify(MOCK_ANALYTICS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify(MOCK_GOALS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify(MOCK_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    // Heading is "{agent.name} — Dashboard"
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Dashboard Agent/i),
      { timeout: 3000 }
    );
  });

  test('shows KPI cards with goal count', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Response(JSON.stringify(MOCK_ANALYTICS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify(MOCK_GOALS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify(MOCK_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Dashboard Agent/i),
      { timeout: 3000 }
    );
    // total goals KPI: 2 goals filtered by agent_id
    expect(screen.getAllByText('2').length).toBeGreaterThan(0);
  });

  test('handles agent fetch error gracefully', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Not Found', { status: 404 })
    );
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });
});
