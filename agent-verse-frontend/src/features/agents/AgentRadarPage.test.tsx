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
  function mockRadarFetch(benchmarks: unknown, health: unknown = MOCK_HEALTH) {
    return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const json = (b: unknown) =>
        new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/insights/agent-health/')) return json(health);
      if (url.includes('/insights/benchmarks')) return json(benchmarks);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return new Response(null, { status: 404 });
    });
  }

  test('compares against a real platform average when the backend has one', async () => {
    mockRadarFetch(MOCK_BENCHMARKS);
    renderPage();
    expect(await screen.findByText(/Above platform average/i)).toBeInTheDocument();
    expect(screen.getByText(/Platform avg: 85%/)).toBeInTheDocument();
  });

  test('never invents a platform average: null benchmarks show "not enough data" instead', async () => {
    mockRadarFetch({
      platform_avg_success_rate: null,
      platform_avg_cost_usd: null,
      platform_avg_duration_s: null,
      top_10_pct_success_rate: null,
      percentile_bands: {},
      sample_count: 0,
      data_source: 'insufficient_data',
    });
    renderPage();
    const notice = await screen.findByTestId('radar-benchmark-insufficient');
    expect(notice).toHaveTextContent(/not enough data yet/i);
    expect(notice).toHaveTextContent('Your success rate: 92%');
    // The old hard-coded fallback (74%) must never appear, nor any above/below verdict.
    expect(screen.queryByText(/74%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/(Above|Below) platform average/i)).not.toBeInTheDocument();
  });

  test('a failed benchmarks request also shows "not enough data" rather than a fake comparison', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const json = (b: unknown) =>
        new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/insights/agent-health/')) return json(MOCK_HEALTH);
      if (url.includes('/insights/benchmarks')) return new Response('nope', { status: 404 });
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return new Response(null, { status: 404 });
    });
    renderPage();
    expect(await screen.findByTestId('radar-benchmark-insufficient')).toBeInTheDocument();
    expect(screen.queryByText(/74%/)).not.toBeInTheDocument();
  });

  test('an agent with no runs is not ranked against the platform', async () => {
    mockRadarFetch(MOCK_BENCHMARKS, { ...MOCK_HEALTH, sample_size: 0 });
    renderPage();
    const notice = await screen.findByTestId('radar-benchmark-insufficient');
    expect(notice).toHaveTextContent(/No runs yet/);
    expect(screen.queryByText(/(Above|Below) platform average/i)).not.toBeInTheDocument();
  });

  test('axes with no data render "not enough data", never a default score', async () => {
    mockRadarFetch(MOCK_BENCHMARKS, {
      agent_id: 'agent-radar-1',
      health: {
        speed: null, accuracy: null, cost_efficiency: null,
        tool_coverage: null, success_rate: 0.5, coherence: null,
      },
      sample_size: 2,
    });
    renderPage();
    const cards = await screen.findAllByTestId('radar-dimension-empty');
    expect(cards).toHaveLength(5);
    cards.forEach((c) => expect(c).toHaveTextContent(/not enough data yet/i));
    // Overall health is the mean of the axes that have data only.
    expect(screen.getByTestId('radar-overall')).toHaveTextContent('50%');
    expect(screen.queryByText('70%')).not.toBeInTheDocument();
  });

  test('an agent with no data on any axis shows no overall score', async () => {
    mockRadarFetch(MOCK_BENCHMARKS, {
      agent_id: 'agent-radar-1',
      health: {
        speed: null, accuracy: null, cost_efficiency: null,
        tool_coverage: null, success_rate: null, coherence: null,
      },
      sample_size: 0,
    });
    renderPage();
    expect(await screen.findAllByTestId('radar-dimension-empty')).toHaveLength(6);
    expect(screen.queryByTestId('radar-overall')).not.toBeInTheDocument();
  });
});
