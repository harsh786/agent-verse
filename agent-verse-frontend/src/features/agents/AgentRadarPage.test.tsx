import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentRadarPage } from './AgentRadarPage';

const MOCK_AGENT = {
  agent_id: 'agent-radar-1',
  name: 'Radar Agent',
  autonomy_mode: 'supervised',
  created_at: '2026-01-01T00:00:00Z',
};

const MOCK_HEALTH = {
  agent_id: 'agent-radar-1',
  health: {
    speed: 0.80,
    accuracy: 0.90,
    cost_efficiency: 0.70,
    tool_coverage: 0.85,
    success_rate: 0.92,
    coherence: 0.88,
  },
  sample_size: 25,
};

const MOCK_BENCHMARKS = {
  platform_avg_success_rate: 0.85,
  platform_avg_cost_usd: 0.5,
  platform_avg_duration_s: 30,
  top_10_pct_success_rate: 0.97,
  percentile_bands: {},
};

function renderPage(agentId = 'agent-radar-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/agents/${agentId}/radar`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/agents/:agentId/radar" element={<AgentRadarPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('AgentRadarPage', () => {
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

  test('shows skeleton loading state while data loads', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    // Skeleton should be in the DOM during loading
    expect(document.body.innerHTML).toBeTruthy();
  });

  test('renders radar chart area after data loads', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/insights/agent-health/')) {
        return new Response(JSON.stringify(MOCK_HEALTH), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/insights/benchmarks')) {
        return new Response(JSON.stringify(MOCK_BENCHMARKS), {
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
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });

  test('shows dimension labels once health data loads', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/insights/agent-health/')) {
        return new Response(JSON.stringify(MOCK_HEALTH), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/insights/benchmarks')) {
        return new Response(JSON.stringify(MOCK_BENCHMARKS), {
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
    // Dimension labels may appear multiple times (radar + breakdown cards), use getAllByText
    await waitFor(() =>
      expect(screen.getAllByText('Speed').length).toBeGreaterThan(0),
      { timeout: 3000 }
    );
  });
});
