import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ObservabilityPage } from './ObservabilityPage';

// recharts uses ResizeObserver — polyfill for jsdom
(globalThis as Record<string, unknown>).ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// Mock recharts to avoid SVG/canvas issues in jsdom
vi.mock('recharts', () => ({
  BarChart: ({ children }: any) => <div data-testid="bar-chart">{children}</div>,
  Bar: () => null,
  AreaChart: ({ children }: any) => <div>{children}</div>,
  Area: () => null,
  LineChart: ({ children }: any) => <div>{children}</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ObservabilityPage />
    </QueryClientProvider>
  );
}

function makeFetch({
  healthData,
  metricsText = '# metrics',
  grafanaOk = false,
  failHealth = false,
  spans = [] as object[],
}: {
  healthData?: object;
  metricsText?: string;
  grafanaOk?: boolean;
  failHealth?: boolean;
  spans?: object[];
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('localhost:3001')) {
      if (grafanaOk) return new Response(null, { status: 200 });
      throw new Error('Connection refused');
    }
    if (url.endsWith('/health')) {
      if (failHealth)
        return new Response(null, { status: 503, statusText: 'Service Unavailable' });
      return new Response(
        JSON.stringify(healthData ?? { status: 'healthy', version: '1.0.0' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.endsWith('/metrics')) {
      return new Response(metricsText, { status: 200, headers: { 'Content-Type': 'text/plain' } });
    }
    if (url.includes('/analytics/observability/spans')) {
      return new Response(JSON.stringify(spans), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('ObservabilityPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders page title', () => {
    makeFetch();
    renderPage();
    expect(screen.getByText('Observability')).toBeInTheDocument();
  });

  test('shows all 4 tabs', () => {
    makeFetch();
    renderPage();
    expect(screen.getByRole('tab', { name: 'Overview' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Metrics' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Traces' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Logs' })).toBeInTheDocument();
  });

  test('Overview tab is active by default and shows System Health', () => {
    makeFetch();
    renderPage();
    expect(screen.getByText('System Health')).toBeInTheDocument();
  });

  test('shows health status from API', async () => {
    makeFetch({ healthData: { status: 'healthy', version: '2.1.0' } });
    renderPage();
    await waitFor(() => expect(screen.getByText('healthy')).toBeInTheDocument());
  });

  test('shows version number when returned by health API', async () => {
    makeFetch({ healthData: { status: 'healthy', version: '3.5.1' } });
    renderPage();
    await waitFor(() => expect(screen.getByText(/v3\.5\.1/)).toBeInTheDocument());
  });

  test('shows deps grid when health returns checks key (new backend)', async () => {
    makeFetch({
      healthData: {
        status: 'healthy',
        checks: {
          postgres: { status: 'up', latency_ms: 5 },
          redis: { status: 'up', latency_ms: 2 },
        },
        dependencies: {
          postgres: { status: 'up', latency_ms: 5 },
          redis: { status: 'up', latency_ms: 2 },
        },
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('postgres')).toBeInTheDocument());
    expect(screen.getByText('redis')).toBeInTheDocument();
    expect(screen.getByText('5ms')).toBeInTheDocument();
  });

  test('shows deps grid when health returns dependencies key (legacy)', async () => {
    makeFetch({
      healthData: {
        status: 'ok',
        dependencies: {
          postgres: { status: 'ok', latency_ms: 5 },
          redis: { status: 'ok', latency_ms: 2 },
        },
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('deps-grid')).toBeInTheDocument());
  });

  test('shows error when health endpoint fails', async () => {
    makeFetch({ failHealth: true });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('health-error')).toBeInTheDocument());
  });

  test('Grafana link is present', () => {
    makeFetch();
    renderPage();
    expect(screen.getByText('Grafana')).toBeInTheDocument();
  });

  test('switches to Metrics tab', async () => {
    makeFetch({ metricsText: '# HELP\nsome_metric 1.0' });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    expect(screen.getByText(/Goal Duration/i)).toBeInTheDocument();
  });

  test('Metrics tab shows raw metrics text in details', async () => {
    makeFetch({ metricsText: 'http_requests_total 42' });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Metrics' }));
    expect(screen.getByText(/Raw Prometheus output/i)).toBeInTheDocument();
  });

  test('switches to Traces tab and shows empty state', async () => {
    makeFetch({ spans: [] });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Traces' }));
    await waitFor(() => expect(screen.getByText(/No spans recorded yet/i)).toBeInTheDocument());
  });

  test('Traces tab renders span waterfall when spans provided', async () => {
    const now = Date.now() * 1e6; // nanoseconds
    makeFetch({
      spans: [{
        name: 'agent.execute',
        trace_id: 'abc123',
        span_id: 'sp001',
        start_time: now,
        end_time: now + 1e8,
        attributes: {},
        status: 'OK',
      }],
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Traces' }));
    await waitFor(() => expect(screen.getByText('agent.execute')).toBeInTheDocument());
  });

  test('switches to Logs tab and shows live log entries', async () => {
    makeFetch();
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: 'Logs' }));
    // Log tab renders log-level badges
    await waitFor(() => {
      const infoBadges = screen.getAllByText('INFO');
      expect(infoBadges.length).toBeGreaterThan(0);
    });
  });
});
